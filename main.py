"""
事業評価AIサービス - FastAPI メインアプリケーション
OpenRouter 経由でモデルを呼び出す（Phase 2 でモデル切替が容易）
"""

import asyncio
import json
import logging
import os

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


@app.get("/health")
async def health():
    return {"status": "ok", "models": MODELS}
