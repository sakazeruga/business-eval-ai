"""
事業評価AIサービス - FastAPI メインアプリケーション
OpenRouter 経由でモデルを呼び出す（Phase 2 でモデル切替が容易）
"""

import asyncio
import json
import logging
import os
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from starlette.middleware.base import BaseHTTPMiddleware

from prompts import (
    SYSTEM_BASE,
    build_context,
    prompt_cashflow,
    prompt_coo_diagnosis,
    prompt_cross_swot,
    prompt_marketing,
    prompt_npv_exit,
    prompt_operations,
    prompt_vrio,
    prompt_why_me,
    prompt_why_now,
)

load_dotenv()

app = FastAPI(title="事業評価AIサービス")
templates = Jinja2Templates(directory="templates")

REQUIRE_CF_ACCESS = os.getenv("REQUIRE_CF_ACCESS", "false").lower() == "true"

class CloudflareAccessMiddleware(BaseHTTPMiddleware):
    """Cloudflare Access 経由以外の直アクセスを遮断する。
    Cloudflare は認証済みリクエストに cf-access-authenticated-user-email を付与し、
    直接アクセスではこのヘッダーを剥ぎ取るため、偽装不可。
    """
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if not request.headers.get("cf-access-authenticated-user-email"):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        return await call_next(request)

if REQUIRE_CF_ACCESS:
    app.add_middleware(CloudflareAccessMiddleware)

# ============================================================
# モデル設定
#   - 検索が必要なセクション（SEARCH_MODELS）は Perplexity Agent API 経由
#   - それ以外は OpenRouter 経由（ZDR 強制）
#   - 要件: 学習利用なし / 非中華モデル
# ============================================================
HAIKU = "anthropic/claude-haiku-5.5"
SONNET = "anthropic/claude-sonnet-5.5"
LUNA = "openai/gpt-6-luna"

# Perplexity Agent API のモデルID（provider/model、バージョンはハイフン区切り）
SEARCH_MODELS = {
    "competitors": "anthropic/claude-haiku-5-5",
    "why_now":     "anthropic/claude-haiku-5-5",
}

MODELS = {
    "competitors":     "claude-haiku-5.5 + Web検索（Perplexity）",
    "why_me":          HAIKU,
    "vrio":            HAIKU,
    "marketing":       HAIKU,
    "cashflow":        HAIKU,
    "npv_exit":        HAIKU,
    "cross_swot":      SONNET,    # 統合判断のみ上位モデル
    "coo_diagnosis":   HAIKU,
    "why_now":         "claude-haiku-5.5 + Web検索（Perplexity）",
    "operations":      LUNA,      # 多段業務フロー系ベンチで Haiku より優位
}

MAX_TOKENS = {
    "competitors":     4096,
    "why_now":         6144,
    "operations":      4096,
    "npv_exit":        6000,
    "cross_swot":      8192,   # Sonnet は冗長なため、切り捨て防止で余裕を持たせる
}
DEFAULT_MAX_TOKENS = 4096

# 推論（thinking）設定。Haiku 5.5 は既定で推論が走り、量が毎回変わって max_tokens を食い潰す
# （実測: 推論 0〜4096 tokens、財務CFが本文空のまま打ち切られた）。定型出力のセクションは無効化する。
# Sonnet 5.5 は推論を無効化できない（400）ため effort で調整する。
DEFAULT_REASONING = {"enabled": False}
REASONING = {
    "cross_swot": {"effort": "medium"},
}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PERPLEXITY_AGENT_URL = "https://api.perplexity.ai/v1/agent"
APP_TITLE = "Business Evaluation AI"

# OpenRouter 側で Zero Data Retention エンドポイントのみに限定する。
# 対応エンドポイントが無いモデルでリクエストが失敗する場合は OPENROUTER_ZDR=false で切り分ける。
OPENROUTER_ZDR = os.getenv("OPENROUTER_ZDR", "true").lower() == "true"

SEARCH_INSTRUCTIONS_SUFFIX = (
    "\n\nWeb検索ツールで最新の公開データを確認し、"
    "市場規模・成長率などの数値には出典と年を併記すること。"
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("business_eval")


def get_client() -> OpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY が設定されていません")
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers={"X-Title": APP_TITLE},
    )


async def call_search_section(prompt_text: str, section_key: str) -> str:
    """Web検索が必要なセクションを Perplexity Agent API で評価する"""
    model = SEARCH_MODELS[section_key]
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        # 鍵が無くても評価全体は止めず、該当セクションのみ警告を表示する
        return f"⚠️ [{model}] PERPLEXITY_API_KEY が設定されていないため、Web検索セクションを実行できませんでした。"

    payload = {
        "model": model,
        "instructions": SYSTEM_BASE + SEARCH_INSTRUCTIONS_SUFFIX,
        "input": prompt_text,
        "max_output_tokens": MAX_TOKENS.get(section_key, DEFAULT_MAX_TOKENS),
        "tools": [{"type": "web_search", "max_results": 5}],
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            PERPLEXITY_AGENT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:300])
        except ValueError:
            detail = resp.text[:300]
        return f"⚠️ [{model}] Perplexity API エラー（HTTP {resp.status_code}）: {detail}"

    data = resp.json()
    if data.get("status") == "failed" or data.get("error"):
        msg = (data.get("error") or {}).get("message", "unknown")
        return f"⚠️ [{model}] Perplexity API が失敗を返しました: {msg}"

    texts: list[str] = []
    sources: dict[str, str] = {}
    for item in data.get("output", []):
        # 出典URLは search_results 項目に入る（message の annotations は実測で常に空）
        if item.get("type") == "search_results":
            for res in item.get("results") or []:
                if res.get("url"):
                    sources.setdefault(res["url"], res.get("title") or res["url"])
            continue
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") != "output_text":
                continue
            texts.append(part.get("text", ""))
            for ann in part.get("annotations", []) or []:
                if ann.get("type") == "url_citation" and ann.get("url"):
                    sources.setdefault(ann["url"], ann.get("title") or ann["url"])

    content = "".join(texts).strip()
    if not content:
        return f"⚠️ [{model}] 空の応答が返されました（status: {data.get('status')}）"

    usage = data.get("usage") or {}
    logger.info(
        "usage section=%s model=%s in=%s out=%s cost=%s",
        section_key, model, usage.get("input_tokens"), usage.get("output_tokens"),
        (usage.get("cost") or {}).get("total_cost"),
    )

    if sources:
        content += "\n\n**出典**\n" + "\n".join(f"- [{t}]({u})" for u, t in sources.items())
    return content


async def call_section(prompt_text: str, section_key: str) -> str:
    """単一セクションを評価する（非同期）。検索系は Perplexity、他は OpenRouter 経由"""
    if section_key in SEARCH_MODELS:
        return await call_search_section(prompt_text, section_key)

    client = get_client()
    model = MODELS.get(section_key, HAIKU)
    max_tokens = MAX_TOKENS.get(section_key, DEFAULT_MAX_TOKENS)

    def _sync_call() -> str:
        messages = [
            {"role": "system", "content": SYSTEM_BASE},
            {"role": "user", "content": prompt_text},
        ]

        kwargs: dict = dict(model=model, max_tokens=max_tokens, messages=messages)
        extra_body: dict = {"reasoning": REASONING.get(section_key, DEFAULT_REASONING)}
        if OPENROUTER_ZDR:
            extra_body["provider"] = {"zdr": True}
        kwargs["extra_body"] = extra_body

        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content

        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "usage section=%s model=%s in=%s out=%s finish=%s",
                section_key, model, usage.prompt_tokens, usage.completion_tokens,
                getattr(response.choices[0], "finish_reason", None),
            )

        # 空応答を無音で通すのではなくエラーとして表面化
        if not content:
            finish = getattr(response.choices[0], "finish_reason", "unknown")
            return f"⚠️ [{model}] 空の応答が返されました（finish_reason: {finish}）\nモデルIDまたはアカウント残高を確認してください。"

        return content

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_call)


async def fetch_competitors(business_idea_summary: str) -> str:
    """Web検索付きで競合を自動調査する（失敗しても評価全体は止めない）"""
    prompt = f"""以下の事業の主要競合企業を5社、Markdown形式でリストアップしてください。

事業概要:
{business_idea_summary}

出力形式（必ずMarkdownで）:
### 主要競合調査

| # | 社名 | 事業概要 | 差別化ポイント |
|---|---|---|---|
| 1 | [社名] | [概要] | [ポイント] |
| 2 | ... | ... | ... |

**この事業との最大の差別化軸:**
[1〜2文で]
"""
    try:
        return await call_section(prompt, "competitors")
    except Exception as e:  # 競合調査の失敗で全セクションを落とさない
        logger.exception("competitors failed")
        return f"⚠️ 競合調査に失敗しました: {e}"


# 表示順。クロスSWOT は全セクションの結果を要約して渡すため必ず最後。
SECTION_ORDER = {
    "startup": ["why_me", "why_now", "vrio", "marketing", "operations", "cashflow", "npv_exit"],
    "coo":     ["coo_diagnosis", "vrio", "cashflow"],
}


def section_prompts_for(mode: str, context: str) -> dict[str, str]:
    """モードごとの（クロスSWOT を除く）セクションプロンプトを表示順で返す"""
    builders = {
        "why_me":        prompt_why_me,
        "why_now":       prompt_why_now,
        "vrio":          prompt_vrio,
        "marketing":     prompt_marketing,
        "operations":    prompt_operations,
        "cashflow":      prompt_cashflow,
        "npv_exit":      prompt_npv_exit,
        "coo_diagnosis": prompt_coo_diagnosis,
    }
    keys = SECTION_ORDER["startup" if mode == "startup" else "coo"]
    return {k: builders[k](context) for k in keys}


async def evaluate_sections(mode: str, context: str):
    """セクションを並列実行し、完成した順に (key, text) を yield する。

    クロスSWOT だけは全セクションの要約を入力に取るため、最後に実行する。
    """
    section_prompts = section_prompts_for(mode, context)

    async def run(key: str, prompt_text: str) -> tuple[str, str]:
        try:
            return key, await call_section(prompt_text, key)
        except Exception as e:  # 1セクションの失敗で全体を止めない
            logger.exception("section failed: %s", key)
            return key, f"⚠️ {key} の評価に失敗しました: {e}"

    tasks = [asyncio.create_task(run(k, pr)) for k, pr in section_prompts.items()]

    # 差し込み先は data-section で決まるため、表示順ではなく完了順に流す。
    # 遅いセクション（Web検索系）が先に終わった軽いセクションを足止めしない。
    results: dict[str, str] = {}
    for completed in asyncio.as_completed(tasks):
        key, text = await completed
        results[key] = text
        yield key, text

    summary = "\n\n".join(f"=== {k.upper()} ===\n{v[:800]}" for k, v in results.items())
    try:
        cross = await call_section(
            prompt_cross_swot(f"{context}\n\n## 各セクション評価サマリー\n{summary}"),
            "cross_swot",
        )
    except Exception as e:
        logger.exception("cross_swot failed")
        cross = f"⚠️ クロスSWOT の生成に失敗しました: {e}"
    yield "cross_swot", cross


# ============================================================
# 自動入力（URL から公開情報を調査してフォームを下書きする）
# ============================================================
# フォーム項目は両モードで共通なので、1つの実装で startup / coo の両方に使える。
# 自動入力の対象項目。COOモードでは Why Me セクション（#why-me-section）が
# 画面上で非表示になるため、見えない欄を埋めないよう対象を絞る。
AUTOFILL_FIELDS_COMMON = {
    "problem":        "この事業／企業が解決しようとしている課題",
    "solution":       "提供している製品・サービスの内容",
    "business_model": "収益モデル（課金方式・価格・主要顧客）",
    "phase":          "現在のフェーズ（設立年・従業員数・拠点・直近の動き）",
}

# スタートアップモードでのみ表示される Why Me 関連の項目
AUTOFILL_FIELDS_STARTUP = {
    "full_commit_members": "代表者および常勤役員の氏名と経歴",
    "committable_members": "非常勤・顧問・アドバイザー",
    "q2_2_credentials":    "代表者・役員が公開している学位・資格",
    "q2_4_ai_usage":       "AI・デジタルツールの活用状況",
}

AUTOFILL_LABELS = {
    "problem": "解決する課題", "solution": "ソリューション",
    "business_model": "ビジネスモデル", "phase": "フェーズ",
    "full_commit_members": "フルコミットメンバー", "committable_members": "コミッタブルメンバー",
    "q2_2_credentials": "保有資格・学位", "q2_4_ai_usage": "AI活用状況",
}


def autofill_fields_for(mode: str) -> dict[str, str]:
    fields = dict(AUTOFILL_FIELDS_COMMON)
    if mode == "startup":
        fields.update(AUTOFILL_FIELDS_STARTUP)
    return fields


# 公開情報から埋まらない項目。推測させず、必ず利用者に記入してもらう（スタートアップのみ）。
AUTOFILL_ASK_USER = {
    "q2_1_continuity":  "最も長く独力で取り組み続けたこと（期間と、何度やめようと思ったか）",
    "q2_3_application": "資格・能力を実務でどう適用し、どんな成果が出たか",
    "q3_1_network":     "理由を聞かずに1万円を貸してくれるハブ人材の人数と、ネットワークの重複度",
    "q3_2_doors":       "1次ドア（資源・権限への直接経路）と2次ドア（コミュニティの橋渡し役）の人数",
}

AUTOFILL_MANUAL_NOTE = {
    "startup": "背景情報（2-1／2-3／3-1／3-2）は公開情報に存在しないため、ご自身で記入してください。"
               "Why Me 評価の3因子に使われます。",
    "coo":     "COOターンアラウンドモードでは Why Me 評価を行わないため、"
               "入力は上記4項目のみです。財務状況など非公開の情報は追記してください。",
}

AUTOFILL_INSTRUCTIONS = """あなたは企業調査の専門家です。指定URLとWeb検索から、公開情報のみを収集します。

厳守事項:
- 出典を確認できた内容だけを value に入れること
- 根拠が無い項目は value を空文字にし、confidence を "unknown" にすること。推測で埋めてはならない
- confidence は high（指定URL等の一次情報で確認）／medium（第三者の記事等）／low（断片的）／unknown（不明）
- sources には参照したURLを入れること。value が空なら空配列でよい
- 代表者については、公開されている職務経歴・学歴・資格に限定して記載すること。
  私生活・家族・住所・連絡先など業務に無関係な個人情報は収集も記載もしないこと
- 日本語で記述すること"""


def autofill_schema(fields: dict[str, str]) -> dict:
    field = {
        "type": "object",
        "properties": {
            "value":      {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
            "sources":    {"type": "array", "items": {"type": "string"}},
        },
        "required": ["value", "confidence", "sources"],
    }
    props = {k: dict(field) for k in fields}
    props["company_name"] = {"type": "string"}
    return {
        "type": "object",
        "properties": props,
        "required": ["company_name", *fields],
    }


async def autofill_from_url(url: str, mode: str) -> dict:
    """URL から公開情報を調査し、フォームの下書きを返す"""
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        raise ValueError("PERPLEXITY_API_KEY が設定されていません")

    fields = autofill_fields_for(mode)
    focus = (
        "既存事業の立て直しを前提とした調査です。現在の事業内容・組織規模・"
        "オペレーション上の課題、直近の業績や資金繰りに関する公開情報を重視してください。"
        if mode != "startup" else
        "新規事業の評価を前提とした調査です。構想・提供価値・差別化要素・"
        "現在の開発フェーズに関する公開情報を重視してください。"
    )
    asks = "\n".join(f"- {k}: {v}" for k, v in fields.items())
    payload = {
        "model": SEARCH_MODELS["competitors"],
        "instructions": AUTOFILL_INSTRUCTIONS,
        "input": (
            f"調査対象URL: {url}\n\n{focus}\n\n"
            f"次の項目を調べてください。\n{asks}"
        ),
        "max_output_tokens": 8192,
        "tools": [
            {"type": "fetch_url", "max_urls": 10},
            {"type": "web_search", "max_results": 8},
            {"type": "people_search"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "autofill", "strict": True, "schema": autofill_schema(fields)},
        },
    }

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            PERPLEXITY_AGENT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:300])
        except ValueError:
            detail = resp.text[:300]
        raise RuntimeError(f"Perplexity API エラー（HTTP {resp.status_code}）: {detail}")

    data = resp.json()
    text = ""
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                text += part.get("text", "")
    if not text.strip():
        raise RuntimeError(f"空の応答が返されました（status: {data.get('status')}）")

    usage = data.get("usage") or {}
    logger.info(
        "autofill url=%s mode=%s in=%s out=%s cost=%s",
        url, mode, usage.get("input_tokens"), usage.get("output_tokens"),
        (usage.get("cost") or {}).get("total_cost"),
    )

    result = json.loads(text)
    is_startup = mode == "startup"
    return {
        "company_name": result.pop("company_name", ""),
        # スキーマ外の項目が混ざっても画面に出さない
        "fields": {k: v for k, v in result.items() if k in fields},
        "labels": {k: AUTOFILL_LABELS.get(k, k) for k in fields},
        "ask_user": AUTOFILL_ASK_USER if is_startup else {},
        "manual_note": AUTOFILL_MANUAL_NOTE["startup" if is_startup else "coo"],
        "cost": (usage.get("cost") or {}).get("total_cost"),
    }


# ============================================================
# ストリーミング描画
# ============================================================
# result.html のこの位置に、完成したセクションを流し込む <script> を挿入する
STREAM_SENTINEL = "<!--STREAM_INSERT-->"


def fill_script(key: str, markdown: str) -> str:
    """1セクション分の本文をブラウザへ送る <script> を生成する"""
    # 本文中の "</script>" でタグが閉じるのを防ぐため "<" を退避する
    payload = json.dumps(markdown, ensure_ascii=False).replace("<", "\\u003c")
    return f"<script>fillSection({json.dumps(key)}, {payload});</script>\n"


# ============================================================
# ルーティング
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/evaluate", response_class=HTMLResponse)
async def evaluate(
    request: Request,
    mode: str = Form(...),
    problem: str = Form(default=""),
    solution: str = Form(default=""),
    business_model: str = Form(default=""),
    phase: str = Form(default=""),
    position: str = Form(default="CEO / 創業者"),
    full_commit_members: str = Form(default=""),
    committable_members: str = Form(default=""),
    q2_1_continuity: str = Form(default=""),
    q2_2_credentials: str = Form(default=""),
    q2_3_application: str = Form(default=""),
    q2_4_ai_usage: str = Form(default=""),
    q3_1_network: str = Form(default=""),
    q3_2_doors: str = Form(default=""),
    extra_context: str = Form(default=""),
):
    business_idea = {
        "problem": problem,
        "solution": solution,
        "business_model": business_model,
        "phase": phase,
    }
    idea_summary = f"課題: {problem}\nソリューション: {solution}\nビジネスモデル: {business_model}"

    answers = {
        "position":           position,
        "full_commit_members": full_commit_members,
        "committable_members": committable_members,
        "q2_1_continuity":    q2_1_continuity,
        "q2_2_credentials":   q2_2_credentials,
        "q2_3_application":   q2_3_application,
        "q2_4_ai_usage":      q2_4_ai_usage,
        "q3_1_network":       q3_1_network,
        "q3_2_doors":         q3_2_doors,
    }

    context = build_context(business_idea, answers)
    if extra_context.strip():
        context += f"\n\n## 追加ヒアリング（AI対話で取得）\n{extra_context.strip()}"

    # 画面に出るカード数。競合調査カードは startup モードのみ表示される
    total = len(SECTION_ORDER["startup" if mode == "startup" else "coo"]) + 1
    if mode == "startup":
        total += 1

    async def generate():
        page = templates.get_template("result.html").render(
            request=request,
            mode=mode,
            business_idea=business_idea,
            results={},
            models=MODELS,
            streaming=True,
            total_sections=total,
        )
        head, _, tail = page.partition(STREAM_SENTINEL)
        yield head

        competitors = await fetch_competitors(idea_summary)
        yield fill_script("competitors", competitors)

        ctx = context + f"\n\n## 主要競合（AI自動調査）\n{competitors}"
        async for key, text in evaluate_sections(mode, ctx):
            yield fill_script(key, text)

        yield "<script>finishStreaming();</script>"
        yield tail

    return StreamingResponse(
        generate(),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-store",
            # プロキシのバッファリングを無効化（効かないと逐次表示にならない）
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# 対話インテーク（URL → 調査 → 不足をAIが質問 → 充足判定 → 評価）
# ============================================================
INTAKE_MAX_QUESTIONS = 6

# モードごとに「評価に最低限必要なもの」を定義する。
# ここが満たされたかどうかを AI が判定し、進行可否を決める。
INTAKE_REQUIREMENTS = {
    "startup": """この評価では次が揃っている必要があります。
1. 解決する課題 / ソリューション / 収益モデル / 現在のフェーズ
2. Why Me 3因子:
   - 情報収集能力: 競合に対する差別化を説明できるか
   - 背景: 独力で長く継続した経験、保有資格と実務での適用、AI活用度
   - 巻き込み力: 1次ドア（資源・権限への直接経路）と2次ドア（コミュニティの橋渡し）の人数と重複度
Why Me の3因子は公開情報に存在しないため、必ず質問して取得すること。""",
    "coo": """この評価では次が揃っている必要があります。
1. 現在の事業内容 / 収益モデル / 組織規模
2. ターンアラウンド診断に必要な実態:
   - 直近の売上規模と推移、収支（赤字なら月次の赤字額）
   - 資金繰りの状況（手元資金の持ち時間、借入・調達予定）
   - オペレーション上の最大のボトルネック
   - 社長が「何が壊れたか」をどう認識しているか
財務情報は非公開のため、必ず質問して取得すること。""",
}

INTAKE_SYSTEM = """あなたは事業評価の入力を整えるインタビュアーです。
調査結果とこれまでの回答を読み、評価を始められるだけの情報が揃ったかを判定します。

判定の原則:
- 公開情報で分かることは質問しない。調査結果に書いてあることを聞き返さない
- 1回に1問だけ。答えやすい具体的な聞き方にすること
- 評価の結論を左右しない細部は聞かない。足りなければ「不明」として進めてよい
- 既に十分なら ready を true にして質問を打ち切ること

必ず次のJSONだけを出力すること（前後に説明文を付けない）:
{
  "ready": true/false,
  "question": "次に聞くこと（ready が true なら空文字）",
  "why": "その質問が評価のどこに効くか（1文）",
  "summary": {
    "problem": "", "solution": "", "business_model": "", "phase": ""
  }
}
summary は調査結果と回答から現時点で分かる内容を日本語で埋めること。"""


def intake_user_prompt(mode: str, research: str, qa: list) -> str:
    history = "\n".join(f"Q: {x.get('q','')}\nA: {x.get('a','')}" for x in qa) or "（まだ質問していません）"
    return (
        f"{INTAKE_REQUIREMENTS['startup' if mode == 'startup' else 'coo']}\n\n"
        f"## 公開情報の調査結果\n{research}\n\n"
        f"## これまでのやり取り\n{history}\n\n"
        f"（これまでの質問数: {len(qa)} / 上限 {INTAKE_MAX_QUESTIONS}）"
    )


async def intake_assess(mode: str, research: str, qa: list) -> dict:
    """不足を判定し、次の質問または充足判定を返す"""
    client = get_client()

    def _call() -> str:
        kwargs = dict(
            model=HAIKU,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": INTAKE_SYSTEM},
                {"role": "user", "content": intake_user_prompt(mode, research, qa)},
            ],
            extra_body={"reasoning": {"enabled": False}},
        )
        if OPENROUTER_ZDR:
            kwargs["extra_body"]["provider"] = {"zdr": True}
        r = client.chat.completions.create(**kwargs)
        return r.choices[0].message.content or ""

    loop = asyncio.get_event_loop()
    raw = (await loop.run_in_executor(None, _call)).strip()
    # ```json ... ``` で囲まれて返ることがある
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw.strip("`")
        raw = raw[4:] if raw.lower().startswith("json") else raw
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        logger.warning("intake_assess: JSON parse failed: %.200s", raw)
        # 判定できない場合は進行を止めず、利用者に自由記述を求める
        return {"ready": False, "question": "他に評価にあたって伝えておきたいことはありますか？",
                "why": "不足判定に失敗したため、補足を伺います", "summary": {}}

    # 上限に達したら強制的に打ち切る（無限に質問が続かないようにする）
    if len(qa) >= INTAKE_MAX_QUESTIONS:
        data["ready"] = True
    return data


def normalize_url(raw: str) -> str:
    """入力されたURLを正規化する。スキーム省略時は https:// を補う。

    不正な場合は ValueError を送出する。
    """
    raw = raw.strip().strip("<>\"'")
    if not raw:
        raise ValueError("URLを入力してください")
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("http または https のURLを入力してください")
    host = (parsed.hostname or "").lower()
    if "." not in host or host.endswith("."):
        raise ValueError("ドメイン名が正しくありません（例: example.co.jp）")
    # 社内ネットワークやループバックの調査依頼は受け付けない
    if host in ("localhost",) or host.endswith(".local") or host.startswith(("127.", "10.", "192.168.", "169.254.")):
        raise ValueError("外部に公開されているURLを指定してください")
    return parsed.geturl()


@app.get("/quick", response_class=HTMLResponse)
async def quick(request: Request):
    """URL から始める高速パス（調査 → AI対話 → 評価）"""
    return templates.TemplateResponse(request, "quick.html")


def research_to_text(result: dict) -> str:
    """autofill の結果を、対話AIに渡す読み物テキストに変換する"""
    lines = [f"対象: {result.get('company_name','')}"]
    labels = result.get("labels", {})
    for key, info in (result.get("fields") or {}).items():
        val = (info.get("value") or "").strip()
        if not val:
            continue
        lines.append(f"- {labels.get(key, key)}（確信度 {info.get('confidence')}）: {val}")
    return "\n".join(lines)


@app.post("/intake/start")
async def intake_start(url: str = Form(...), mode: str = Form(default="coo")):
    """URL を調査し、最初の質問を返す"""
    try:
        url = normalize_url(url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    try:
        research = await autofill_from_url(url, mode)
        research_text = research_to_text(research)
        assessment = await intake_assess(mode, research_text, [])
    except Exception as e:
        logger.exception("intake_start failed: %s", url)
        return JSONResponse(status_code=502, content={"error": str(e)})

    return JSONResponse({
        "state": {"mode": mode, "url": url, "research": research_text, "qa": []},
        "company_name": research.get("company_name", ""),
        "research_summary": research_text,
        **assessment,
    })


@app.post("/intake/reply")
async def intake_reply(state: str = Form(...), question: str = Form(...), answer: str = Form(...)):
    """回答を受け取り、次の質問または充足判定を返す"""
    try:
        st = json.loads(state)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "セッション情報が壊れています。最初からやり直してください。"})

    qa = list(st.get("qa") or [])
    qa.append({"q": question, "a": answer})
    st["qa"] = qa
    try:
        assessment = await intake_assess(st.get("mode", "coo"), st.get("research", ""), qa)
    except Exception as e:
        logger.exception("intake_reply failed")
        return JSONResponse(status_code=502, content={"error": str(e)})

    return JSONResponse({"state": st, **assessment})


@app.post("/autofill")
async def autofill(url: str = Form(...), mode: str = Form(default="coo")):
    """URL から公開情報を調査し、フォームの下書きを JSON で返す"""
    try:
        url = normalize_url(url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    try:
        return JSONResponse(await autofill_from_url(url, mode))
    except Exception as e:
        logger.exception("autofill failed: %s", url)
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.get("/health")
async def health():
    return {"status": "ok", "models": MODELS}
