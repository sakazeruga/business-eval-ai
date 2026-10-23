"""
事業評価AIサービス - FastAPI メインアプリケーション
OpenRouter 経由でモデルを呼び出す（Phase 2 でモデル切替が容易）
"""

import asyncio
import html
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from starlette.middleware.base import BaseHTTPMiddleware

import access
import cfaccess
import store
from prompts import (
    SYSTEM_BASE,
    build_context,
    prompt_cashflow,
    prompt_competitors,
    prompt_coo_diagnosis,
    prompt_cross_swot,
    prompt_marketing,
    prompt_npv_exit,
    prompt_operations,
    prompt_rival,
    prompt_vrio,
    prompt_why_me,
    prompt_why_now,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("business_eval")

app = FastAPI(title="事業評価AIサービス")
templates = Jinja2Templates(directory="templates")

REQUIRE_CF_ACCESS = os.getenv("REQUIRE_CF_ACCESS", "false").lower() == "true"

class CloudflareAccessMiddleware(BaseHTTPMiddleware):
    """Cloudflare Access 経由以外の直アクセスを遮断する。

    オリジン（*.fly.dev）はインターネットから直接叩けるため、
    cf-access-authenticated-user-email は攻撃者が自分で付けられる。
    CF_ACCESS_TEAM_DOMAIN と CF_ACCESS_AUD を設定すると、
    Cloudflare の公開鍵で署名付きトークンを検証する。
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        if cfaccess.VERIFY_ENABLED:
            try:
                request.state.verified_email = cfaccess.verify(cfaccess.extract_token(request))
            except cfaccess.InvalidAccessToken as e:
                logger.warning("access token rejected path=%s: %s", request.url.path, e)
                return JSONResponse(status_code=403, content={"detail": "Forbidden"})
            return await call_next(request)

        # 未設定時はヘッダーの有無しか見られない（偽装可能）。
        # 被害を限定するため、この状態では管理者権限と上限免除を与えない。
        if not request.headers.get("cf-access-authenticated-user-email"):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        return await call_next(request)

if REQUIRE_CF_ACCESS and not cfaccess.VERIFY_ENABLED:
    logger.critical(
        "Cloudflare Access のトークン検証が無効です。"
        "cf-access-authenticated-user-email は偽装可能なため、"
        "管理者権限と上限免除は無効化されています。"
        "CF_ACCESS_TEAM_DOMAIN と CF_ACCESS_AUD を設定してください。"
    )


# 承認なしでも通すパス。申請・招待・ヘルスチェックは承認前に触れる必要がある。
ACCESS_OPEN_PATHS = ("/health", "/request", "/no-access")
ACCESS_OPEN_PREFIXES = ("/invite/",)


class ApprovalMiddleware(BaseHTTPMiddleware):
    """Cloudflare Access は「本人か」を確認するだけなので、
    「使ってよいか」はアプリ側の承認リストで判定する。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ACCESS_OPEN_PATHS or path.startswith(ACCESS_OPEN_PREFIXES):
            return await call_next(request)

        email = current_user(request)
        try:
            allowed = await access.is_approved(email)
        except store.StoreError as e:
            # 承認状況を確認できないときは通さない（誤って開放しない）
            logger.warning("approval check failed email=%s: %s", email, e)
            if request.method == "GET":
                return HTMLResponse(
                    render_notice("ただいま確認できません",
                                  "利用状況を確認できませんでした。少し時間をおいてお試しください。"),
                    status_code=503)
            return JSONResponse(status_code=503, content={"error": "一時的に確認できません。"})

        if allowed:
            return await call_next(request)

        if request.method == "GET":
            return RedirectResponse("/no-access", status_code=303)
        return JSONResponse(status_code=403, content={"error": "利用が許可されていません。"})


# ミドルウェアは「後から追加したものが外側（先に実行）」になる。
# Cloudflare Access の確認を最も外側に置き、未認証の直アクセスは
# 承認判定に到達する前に遮断する。
app.add_middleware(ApprovalMiddleware)
if REQUIRE_CF_ACCESS:
    app.add_middleware(CloudflareAccessMiddleware)

# ============================================================
# 利用上限（コスト保護）
# ============================================================
# URL を共有した相手が使いすぎても、費用が膨らまないようにする。
# 利用者は Cloudflare Access が付与するメールアドレスで識別する。
#
# 注意: 集計はプロセス内メモリで持つ。Fly のマシンは2台構成で、停止・再起動・
# デプロイで消えるため、実効上限は設定値の最大2倍程度になり得る。
# 厳密にするなら共有ストアが必要。最終的な歯止めは OpenRouter / Perplexity の
# 前払い残高（使い切れば止まる）。
JST = timezone(timedelta(hours=9))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# 1ユーザー・1日あたり（JST 0時でリセット）
USER_LIMITS = {
    "evaluate": _env_int("LIMIT_EVALUATE_PER_USER", 5),    # 評価レポート（約11円/回）
    "research": _env_int("LIMIT_RESEARCH_PER_USER", 10),   # URL調査・自動入力（約1円/回）
    "chat":     _env_int("LIMIT_CHAT_PER_USER", 40),       # 追加質問・ヒアリング返信（1円未満/回）
}
# サービス全体・1日あたり。利用者が増えても総額に天井を設ける
GLOBAL_LIMITS = {
    "evaluate": _env_int("LIMIT_EVALUATE_GLOBAL", 60),
    "research": _env_int("LIMIT_RESEARCH_GLOBAL", 150),
    "chat":     _env_int("LIMIT_CHAT_GLOBAL", 600),
}
KIND_LABEL = {"evaluate": "評価レポートの作成", "research": "URLの調査", "chat": "AIへの質問"}
ADMIN_EMAILS = access.ADMIN_EMAILS

MAX_CONCURRENT_EVALS = _env_int("MAX_CONCURRENT_EVALS", 4)
EVAL_SLOTS = asyncio.Semaphore(MAX_CONCURRENT_EVALS)

# 入力サイズの上限（巨大な入力でトークン費用を膨らませる使い方を防ぐ）
MAX_EVAL_CHARS = 60000
MAX_REPORT_CHARS = 120000
MAX_QUESTION_CHARS = 3000

def current_user(request: Request) -> str:
    """このリクエストの利用者。署名検証が有効ならその結果を使う。"""
    verified = getattr(request.state, "verified_email", None)
    if verified:
        return verified
    return (request.headers.get("cf-access-authenticated-user-email") or "local").strip().lower()


def identity_verified(request: Request) -> bool:
    """利用者が本人だと暗号的に確認できているか。"""
    if not cfaccess.VERIFY_ENABLED:
        # ローカル開発（Cloudflare 自体を使わない）では検証しようがない
        return not REQUIRE_CF_ACCESS
    return bool(getattr(request.state, "verified_email", None))


def is_admin_request(request: Request) -> bool:
    """管理者として扱ってよいか。偽装可能な状態では管理者権限を与えない。"""
    return identity_verified(request) and access.is_admin(current_user(request))


def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def _usage_key(kind: str, who: str) -> str:
    return f"usage:{_today()}:{kind}:{who}"


# 翌日の集計に残らないよう、鍵は2日で消す
USAGE_TTL = 2 * 86400


async def usage_snapshot(request: Request) -> dict:
    """現在の利用者の、本日の使用回数と上限を返す"""
    user = current_user(request)
    unlimited = is_admin_request(request)
    out = {}
    for kind in USER_LIMITS:
        try:
            used = await store.get_int(_usage_key(kind, user))
        except store.StoreError:
            used = 0
        out[kind] = {"used": used, "limit": None if unlimited else USER_LIMITS[kind]}
    return out


async def charge(request: Request, kind: str) -> str | None:
    """上限内なら1回分を加算して None を返す。超過なら利用者向けの理由文を返す。"""
    user = current_user(request)
    # 本人確認が偽装可能な状態では、管理者を名乗っても上限を免除しない
    if is_admin_request(request):
        return None

    # 先に判定し、通った場合だけ加算する。
    # 加算してから判定すると、上限で拒否された呼び出しまで全体枠を消費し、
    # 一人の連打で他の利用者の枠が尽きてしまう。
    try:
        if await store.get_int(_usage_key(kind, user)) >= USER_LIMITS[kind]:
            return (f"本日の{KIND_LABEL[kind]}は上限（{USER_LIMITS[kind]}回）に達しました。"
                    "明日（日本時間 0時）にリセットされます。")
        if await store.get_int(_usage_key(kind, "*")) >= GLOBAL_LIMITS[kind]:
            logger.warning("global limit reached kind=%s", kind)
            return f"本日は{KIND_LABEL[kind]}の全体上限に達しました。明日またお試しください。"
        await store.incr(_usage_key(kind, user), USAGE_TTL)
        await store.incr(_usage_key(kind, "*"), USAGE_TTL)
    except store.StoreError as e:
        # 集計できなくても利用自体は止めない（最終的な歯止めは前払い残高）
        logger.warning("usage counting failed kind=%s: %s", kind, e)
    return None


def limit_json(message: str, status: int = 429) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


LIMIT_PAGE = """<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ご利用制限 | 事業評価AI</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--text:#e6e8ec;--muted:#9aa3b2;--line:rgba(255,255,255,.12);--accent:#6366f1}
@media (prefers-color-scheme:light){:root{--bg:#f5f6f8;--card:#fff;--text:#1a1d23;--muted:#666e7d;--line:rgba(0,0,0,.12)}}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
background:var(--bg);color:var(--text);font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;line-height:1.8}
.box{max-width:480px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px}
h1{font-size:1.25rem;margin:0 0 12px}p{margin:0 0 18px;color:var(--muted)}
a{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;border-radius:8px;padding:10px 20px}
</style></head><body><div class="box"><h1>__TITLE__</h1>
<p>__MSG__</p>__EXTRA__<a href="/">トップへ戻る</a></div></body></html>"""


def render_notice(title: str, message: str, extra: str = "") -> str:
    return (LIMIT_PAGE
            .replace("__TITLE__", html.escape(title))
            .replace("__MSG__", html.escape(message))
            .replace("__EXTRA__", extra))


def limit_page(message: str, status: int = 429) -> HTMLResponse:
    return HTMLResponse(render_notice("ご利用を一時的に制限しています", message), status_code=status)


# ============================================================
# モデル設定
#   - 検索が必要なセクション（SEARCH_MODELS）は Perplexity Agent API 経由
#   - それ以外は OpenRouter 経由（ZDR 強制）
#   - 要件: 学習利用なし / 非中華モデル
# ============================================================
HAIKU = "anthropic/claude-haiku-5.5"
SONNET = "anthropic/claude-sonnet-5.5"
LUNA = "openai/gpt-6-luna"

# レポートへの追加質問用。1問ごとにレポート全文（約15kトークン）を送るため、
# 単価の低い Haiku を既定にする（Sonnet にすると1問あたり約20倍）。
CHAT_MODEL = HAIKU

# Perplexity Agent API のモデルID（provider/model、バージョンはハイフン区切り）
SEARCH_MODELS = {
    "competitors": "anthropic/claude-haiku-5-5",
    "rival":       "anthropic/claude-haiku-5-5",
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
    "competitors":     8192,
    "rival":           8192,
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
    """Web検索付きで競合を分析する（失敗しても評価全体は止めない）"""
    try:
        return await call_section(prompt_competitors(business_idea_summary), "competitors")
    except Exception as e:  # 競合分析の失敗で全セクションを落とさない
        logger.exception("competitors failed")
        return f"⚠️ 競合分析に失敗しました: {e}"


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
    inputs = (problem, solution, business_model, phase, position, full_commit_members,
              committable_members, q2_1_continuity, q2_2_credentials, q2_3_application,
              q2_4_ai_usage, q3_1_network, q3_2_doors, extra_context)
    if sum(len(x) for x in inputs) > MAX_EVAL_CHARS:
        return limit_page(f"入力が長すぎます（合計{MAX_EVAL_CHARS:,}文字まで）。内容を絞ってお試しください。", 413)
    if EVAL_SLOTS.locked():
        return limit_page("ただいま評価の依頼が集中しています。1〜2分ほど待ってからお試しください。")
    # 先に混雑・サイズを判定し、通った場合だけ回数を消費する
    over = await charge(request, "evaluate")
    if over:
        return limit_page(over)

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
    total = len(SECTION_ORDER["startup" if mode == "startup" else "coo"]) + 2

    async def generate():
        async with EVAL_SLOTS:
            async for chunk in generate_inner():
                yield chunk

    async def generate_inner():
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
async def intake_start(request: Request, url: str = Form(...), mode: str = Form(default="coo")):
    """URL を調査し、最初の質問を返す"""
    try:
        url = normalize_url(url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    over = await charge(request, "research")
    if over:
        return limit_json(over)
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
async def intake_reply(request: Request, state: str = Form(...), question: str = Form(...), answer: str = Form(...)):
    """回答を受け取り、次の質問または充足判定を返す"""
    if len(state) + len(question) + len(answer) > MAX_EVAL_CHARS:
        return limit_json("入力が長すぎます。内容を絞ってください。", 413)
    over = await charge(request, "chat")
    if over:
        return limit_json(over)
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


REPORT_CHAT_SYSTEM = """あなたは事業投資評価の専門家です。
利用者が受け取った評価レポートについて、追加の質問に答えます。

回答の原則:
- レポートに書かれている内容を根拠に答えること。どのセクションの記述かを示す
- レポートに無い事柄を聞かれたら、推測で補わず「レポートでは扱っていない」と述べた上で、
  一般論ではなくこの事業の条件に即して何を調べれば判断できるかを示すこと
- 反証や懸念を述べる場合は、この事業に固有の根拠と定量的インパクトを伴うものに限ること
- 「一般的に」「多くの場合」などの一般論的ヘッジは使わないこと
- Markdown で、簡潔に答えること"""

REPORT_CHAT_MAX_HISTORY = 12


@app.post("/chat")
async def report_chat(
    request: Request,
    report: str = Form(...),
    question: str = Form(...),
    history: str = Form(default="[]"),
):
    """評価レポートについての追加質問に答える"""
    if len(report) > MAX_REPORT_CHARS or len(history) > MAX_REPORT_CHARS:
        return limit_json("レポートが大きすぎるため質問できません。", 413)
    if len(question) > MAX_QUESTION_CHARS:
        return limit_json(f"質問が長すぎます（{MAX_QUESTION_CHARS:,}文字まで）。", 413)
    over = await charge(request, "chat")
    if over:
        return limit_json(over)
    try:
        past = json.loads(history)[-REPORT_CHAT_MAX_HISTORY:]
    except json.JSONDecodeError:
        past = []

    messages = [
        {"role": "system", "content": REPORT_CHAT_SYSTEM},
        {"role": "user", "content": f"## 評価レポート全文\n{report}"},
        {"role": "assistant", "content": "レポートを読みました。質問をどうぞ。"},
    ]
    for turn in past:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    client = get_client()

    def _call() -> str:
        kwargs = dict(
            model=CHAT_MODEL, max_tokens=3072, messages=messages,
            extra_body={"reasoning": {"enabled": False}},
        )
        if OPENROUTER_ZDR:
            kwargs["extra_body"]["provider"] = {"zdr": True}
        r = client.chat.completions.create(**kwargs)
        usage = getattr(r, "usage", None)
        if usage is not None:
            logger.info("chat in=%s out=%s", usage.prompt_tokens, usage.completion_tokens)
        return r.choices[0].message.content or ""

    try:
        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(None, _call)
    except Exception as e:
        logger.exception("report_chat failed")
        return JSONResponse(status_code=502, content={"error": str(e)})

    if not answer.strip():
        return JSONResponse(status_code=502, content={"error": "空の応答が返されました"})
    return JSONResponse({"answer": answer})


@app.get("/report", response_class=HTMLResponse)
async def saved_report(request: Request):
    """保存済みレポートを表示する（中身はブラウザの localStorage から復元）"""
    return templates.TemplateResponse(
        request, "result.html",
        {"mode": "startup", "business_idea": {}, "results": {}, "models": MODELS, "restore": True},
    )


@app.get("/competitor", response_class=HTMLResponse)
async def competitor_page(request: Request):
    """競合1社をURLで指定して KBF/KSF を分析する画面"""
    return templates.TemplateResponse(request, "competitor.html")


@app.post("/competitor/analyze")
async def competitor_analyze(
    request: Request,
    url: str = Form(...),
    own_business: str = Form(default=""),
):
    """指定URLの企業を調べ、その企業の KBF / KSF を返す"""
    try:
        url = normalize_url(url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    if len(own_business) > 4000:
        return limit_json("自社の説明が長すぎます（4,000文字まで）。", 413)
    over = await charge(request, "research")
    if over:
        return limit_json(over)
    try:
        markdown = await call_section(prompt_rival(url, own_business), "rival")
    except Exception as e:
        logger.exception("competitor analyze failed: %s", url)
        return JSONResponse(status_code=502, content={"error": str(e)})
    return JSONResponse({"url": url, "markdown": markdown})


@app.post("/autofill")
async def autofill(request: Request, url: str = Form(...), mode: str = Form(default="coo")):
    """URL から公開情報を調査し、フォームの下書きを JSON で返す"""
    try:
        url = normalize_url(url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    over = await charge(request, "research")
    if over:
        return limit_json(over)
    try:
        return JSONResponse(await autofill_from_url(url, mode))
    except Exception as e:
        logger.exception("autofill failed: %s", url)
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.get("/usage")
async def usage(request: Request):
    """現在の利用者の、本日の使用回数と上限"""
    return {"user": current_user(request), "usage": await usage_snapshot(request)}


@app.get("/no-access", response_class=HTMLResponse)
async def no_access(request: Request):
    """未承認の利用者に申請方法を案内する"""
    email = current_user(request)
    if await access.is_approved(email):
        return RedirectResponse("/", status_code=303)
    pending = await store.hgetall_json(access.K_PENDING)
    return templates.TemplateResponse(request, "no_access.html", {
        "email": email,
        "already_requested": email in pending,
    })


@app.post("/request")
async def request_access_route(request: Request, note: str = Form(default="")):
    """利用申請を受け付ける"""
    email = current_user(request)
    try:
        message = await access.request_access(email, note)
    except store.StoreError:
        return JSONResponse(status_code=503, content={"error": "ただいま受け付けできません。時間をおいてお試しください。"})
    return JSONResponse({"message": message})


@app.get("/invite/{token}", response_class=HTMLResponse)
async def invite(request: Request, token: str):
    """招待リンク / QR からの自動承認"""
    email = current_user(request)
    try:
        ok, message = await access.redeem_invite(token, email)
    except store.StoreError:
        return HTMLResponse(render_notice("ただいま確認できません",
                                          "少し時間をおいてお試しください。"), status_code=503)
    if ok:
        return HTMLResponse(render_notice("ご利用いただけます",
                                          f"{email} を登録しました。", ""), status_code=200)
    return HTMLResponse(render_notice("ご利用いただけません", message), status_code=403)


def require_admin(request: Request):
    return is_admin_request(request)


ADMIN_SETUP_HINT = (
    "管理画面を使うには、Cloudflare Access のトークン検証を有効にしてください。"
    "CF_ACCESS_TEAM_DOMAIN と CF_ACCESS_AUD を設定すると利用できます。"
)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """承認の管理画面"""
    if not require_admin(request):
        if access.is_admin(current_user(request)) and not identity_verified(request):
            return HTMLResponse(render_notice("管理画面を利用できません", ADMIN_SETUP_HINT),
                                status_code=503)
        return HTMLResponse(render_notice("権限がありません",
                                          "この画面は管理者のみ利用できます。"), status_code=403)
    return templates.TemplateResponse(request, "admin.html", {
        "me": current_user(request),
        "store_backend": store.describe(),
        "access_mode": cfaccess.describe(),
        "auto_domains": sorted(access.AUTO_APPROVE_DOMAINS),
        "open_access": access.OPEN_ACCESS,
    })


@app.get("/admin/data")
async def admin_data(request: Request):
    if not require_admin(request):
        return JSONResponse(status_code=403, content={"error": "権限がありません"})
    try:
        return JSONResponse({
            "pending": await access.list_pending(),
            "approved": await access.list_approved(),
        })
    except store.StoreError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})


@app.post("/admin/approve")
async def admin_approve(request: Request, email: str = Form(...)):
    if not require_admin(request):
        return JSONResponse(status_code=403, content={"error": "権限がありません"})
    target = email.strip().lower()
    if "@" not in target:
        return JSONResponse(status_code=400, content={"error": "メールアドレスを指定してください"})
    try:
        await access.approve(target)
    except store.StoreError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})
    return JSONResponse({"ok": True, "email": target})


@app.post("/admin/revoke")
async def admin_revoke(request: Request, email: str = Form(...)):
    if not require_admin(request):
        return JSONResponse(status_code=403, content={"error": "権限がありません"})
    target = email.strip().lower()
    if access.is_admin(target):
        return JSONResponse(status_code=400, content={"error": "管理者は解除できません"})
    try:
        await access.revoke(target)
    except store.StoreError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})
    return JSONResponse({"ok": True, "email": target})


@app.post("/admin/reject")
async def admin_reject(request: Request, email: str = Form(...)):
    if not require_admin(request):
        return JSONResponse(status_code=403, content={"error": "権限がありません"})
    try:
        await store.hdel(access.K_PENDING, email.strip().lower())
    except store.StoreError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})
    return JSONResponse({"ok": True})


@app.post("/admin/invite")
async def admin_invite(
    request: Request,
    days: int = Form(default=7),
    max_uses: int = Form(default=50),
    label: str = Form(default=""),
):
    """招待リンクを発行する（QRは画面側で生成）"""
    if not require_admin(request):
        return JSONResponse(status_code=403, content={"error": "権限がありません"})
    try:
        rec = await access.create_invite(days, max_uses, label)
    except store.StoreError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})
    base = str(request.base_url).rstrip("/")
    return JSONResponse({**rec, "url": f"{base}/invite/{rec['token']}"})


@app.get("/health")
async def health():
    """死活監視。認証と保存先の状態も返す"""
    store_ok = await store.healthy()
    return {
        "status": "ok" if store_ok else "degraded",
        "access": cfaccess.describe(),
        "store": store.describe(),
        "store_reachable": store_ok,
        "models": MODELS,
    }
