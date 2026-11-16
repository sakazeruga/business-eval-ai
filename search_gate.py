"""外部検索（Perplexity）に送る内容を「一般的な検索語」だけに絞る。

Perplexity の検索サービスは、API 規約の Search Addendum で次のように定められている。
  - §2.2: 検索の入力・出力（Search Data）を保持し、製品・サービスの改善に使える
  - §1.2.2(iii): 他製品のゼロデータ保持（ZDR）の義務は検索サービスに及ばない
そのため、利用者の事業構想の本文は Perplexity に送らない。流れは次のとおり。

  1. OpenRouter（ZDR）で、事業構想から一般的な検索語を作る（build_search_plan）
  2. ここで機械的に検査し、固有名・数値・URL・入力の書き写しを含む語を捨てる（sanitize）
  3. Perplexity には残った検索語だけを送り、事実と出典URLを集めさせる
  4. 評価本文は、事業構想と調査結果を合わせて OpenRouter（ZDR）で書く

業種や製品カテゴリを表す語（例: 「製造業 見積 自動化」）は検索に必要なので送る。
"""
import json
import re
import unicodedata

PURPOSES = ("competitors", "why_now")
MAX_QUERIES = 5
MAX_QUERY_LEN = 40
# 入力からこの文字数以上を連続で書き写した検索語は捨てる（固有の言い回しの持ち出しを防ぐ）
COPY_RUN = 10

_URLISH = re.compile(r"https?:|www\.|@|[a-z0-9-]+\.[a-z]{2,}\b", re.I)
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_DIGIT = re.compile(r"\d")


def schema() -> dict:
    """検索計画の JSON スキーマ（OpenRouter の構造化出力で使う）"""
    return {
        "type": "object",
        "properties": {
            "competitors": {"type": "array", "items": {"type": "string"}},
            "why_now": {"type": "array", "items": {"type": "string"}},
            "private_terms": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["competitors", "why_now", "private_terms"],
        "additionalProperties": False,
    }


def parse(raw: str) -> dict:
    """LLM の出力から JSON を取り出す。コードフェンスや前後の説明文があっても読む。"""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"検索計画のJSONを読み取れません: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("検索計画のJSONがオブジェクトではありません")
    return data


def _norm(s: str) -> str:
    """比較用に正規化する（全角→半角、小文字、空白除去）"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def _clean(q: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", q or "")).strip()


def reject_reason(query: str, source_text: str, private_terms: list[str]) -> str | None:
    """送ってはいけない検索語なら理由を返す。送ってよければ None。"""
    q = _clean(query)
    if not q:
        return "空"
    if len(q) > MAX_QUERY_LEN:
        return "長すぎる"
    if _URLISH.search(q):
        return "URL・ドメイン・メールアドレス"
    if _DIGIT.search(_YEAR.sub("", q)):
        return "数値（年以外）"
    nq = _norm(q)
    for term in private_terms or []:
        t = _norm(term)
        if len(t) >= 2 and t in nq:
            return "固有名"
    src = _norm(source_text)
    for i in range(len(nq) - COPY_RUN + 1):
        if nq[i:i + COPY_RUN] in src:
            return "入力の書き写し"
    return None


def sanitize(queries, source_text: str, private_terms) -> tuple[list[str], int]:
    """送ってよい検索語だけを残す。返り値: (残した語, 捨てた数)"""
    kept: list[str] = []
    dropped = 0
    seen: set[str] = set()
    terms = [t for t in (private_terms or []) if isinstance(t, str)]
    for q in queries or []:
        if not isinstance(q, str):
            dropped += 1
            continue
        if reject_reason(q, source_text, terms):
            dropped += 1
            continue
        c = _clean(q)
        key = _norm(c)
        if key in seen:
            continue
        seen.add(key)
        kept.append(c)
    if len(kept) > MAX_QUERIES:
        dropped += len(kept) - MAX_QUERIES
        kept = kept[:MAX_QUERIES]
    return kept, dropped


def build_plan(raw: dict, source_text: str) -> dict:
    """LLM の検索計画を検査し、{purpose: [検索語]} と捨てた数を返す。

    private_terms（入力に出てくる固有名）は検査にだけ使い、外部には送らない。
    """
    terms = raw.get("private_terms") if isinstance(raw.get("private_terms"), list) else []
    plan: dict = {"dropped": 0}
    for purpose in PURPOSES:
        qs = raw.get(purpose) if isinstance(raw.get(purpose), list) else []
        kept, dropped = sanitize(qs, source_text, terms)
        plan[purpose] = kept
        plan["dropped"] += dropped
    return plan


RESEARCH_INSTRUCTIONS = """あなたはリサーチャーです。与えられた検索語でWeb検索し、見つかった事実だけを報告してください。
- 評価・提案・感想は書かないこと
- 数値には必ず年と出典URLを添えること
- 確認できなかった項目は「未確認」と書くこと
- 日本語で、Markdown の箇条書きで出力すること"""

_RESEARCH_GOALS = {
    "competitors": """この領域で実際に提供されている製品・サービスを最大8件挙げてください。
各件について次を書くこと: 社名・サービス名 / 誰に何を提供しているか / 価格 / 導入実績 / 訴求している強み / 出典URL""",
    "why_now": """この領域の市場について、次を調べてください。
- 市場規模と成長率（調査主体・対象年・出典URL）
- 関連する政策・補助金・規制の動き（名称・年・出典URL）
- 普及を後押し、または妨げている技術・社会の変化""",
}


def research_input(purpose: str, queries: list[str]) -> str:
    """Perplexity に送る本文。事業構想は含めず、目的と検索語だけで組み立てる。"""
    lines = "\n".join(f"- {q}" for q in queries)
    return f"{_RESEARCH_GOALS[purpose]}\n\n## 検索語\n{lines}"


def sent_note(queries: list[str]) -> str:
    """レポートに出す「外部検索に送った語」の注記"""
    if not queries:
        return ""
    return "\n\n**外部検索（Perplexity）に送った語**: " + " ／ ".join(f"`{q}`" for q in queries)
