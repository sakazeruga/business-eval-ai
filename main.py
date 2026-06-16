"""
事業評価AIサービス - FastAPI メインアプリケーション
OpenRouter 経由でモデルを呼び出す（Phase 2 でモデル切替が容易）
"""

import asyncio
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from openai import OpenAI

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

# ============================================================
# モデル設定
# ============================================================
MODELS = {
    "competitors":     "perplexity/sonar",
    "why_me":          "anthropic/claude-haiku-4-5",
    "vrio":            "anthropic/claude-haiku-4-5",
    "marketing":       "anthropic/claude-haiku-4-5",
    "cashflow":        "anthropic/claude-haiku-4-5",
    "npv_exit":        "deepseek/deepseek-r1",
    "cross_swot":      "anthropic/claude-haiku-4-5",
    "coo_diagnosis":   "anthropic/claude-haiku-4-5",
    "why_now":         "google/gemini-3.5-flash",      # Gemini 3.5 Flash
    "operations":      "openai/gpt-4o",
}

MAX_TOKENS = {
    "competitors":     1500,
    "why_now":         4096,   # 2048 から拡張（空応答の原因だった可能性）
    "operations":      3072,
    "npv_exit":        6000,
}
DEFAULT_MAX_TOKENS = 4096

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
APP_TITLE = "Business Evaluation AI"


def get_client() -> OpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY が設定されていません")
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers={"X-Title": APP_TITLE},
    )


async def call_section(prompt_text: str, section_key: str) -> str:
    """単一セクションを OpenRouter 経由で評価する（非同期）"""
    client = get_client()
    model = MODELS.get(section_key, "anthropic/claude-haiku-4-5")
    max_tokens = MAX_TOKENS.get(section_key, DEFAULT_MAX_TOKENS)

    def _sync_call() -> str:
        if model.startswith("anthropic/"):
            messages = [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": SYSTEM_BASE,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
                {"role": "user", "content": prompt_text},
            ]
        else:
            messages = [
                {"role": "user", "content": f"{SYSTEM_BASE}\n\n{prompt_text}"}
            ]

        kwargs: dict = dict(model=model, max_tokens=max_tokens, messages=messages)

        # Claude: Extended Thinking を明示的に無効化（思考トークン課金を防ぐ）
        if model.startswith("anthropic/"):
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content

        # 空応答を無音で通すのではなくエラーとして表面化
        if not content:
            finish = getattr(response.choices[0], "finish_reason", "unknown")
            return f"⚠️ [{model}] 空の応答が返されました（finish_reason: {finish}）\nモデルIDまたはアカウント残高を確認してください。"

        return content

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_call)


async def fetch_competitors(business_idea_summary: str) -> str:
    """Perplexity Sonar で競合を自動検索する"""
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
    return await call_section(prompt, "competitors")


async def evaluate_startup(context: str) -> dict[str, str]:
    """スタートアップ評価モード: 全セクションを並列実行する"""
    section_prompts = {
        "why_me":     prompt_why_me(context),
        "why_now":    prompt_why_now(context),
        "vrio":       prompt_vrio(context),
        "marketing":  prompt_marketing(context),
        "operations": prompt_operations(context),
        "cashflow":   prompt_cashflow(context),
        "npv_exit":   prompt_npv_exit(context),
    }

    results_list = await asyncio.gather(
        *[call_section(p, k) for k, p in section_prompts.items()],
        return_exceptions=True,
    )

    results = {}
    for key, result in zip(section_prompts.keys(), results_list):
        results[key] = str(result) if isinstance(result, Exception) else result

    # クロスSWOT は全セクション結果を要約して渡す
    summary = "\n\n".join(f"=== {k.upper()} ===\n{v[:800]}" for k, v in results.items())
    results["cross_swot"] = await call_section(
        prompt_cross_swot(f"{context}\n\n## 各セクション評価サマリー\n{summary}"),
        "cross_swot",
    )
    return results


async def evaluate_coo(context: str) -> dict[str, str]:
    """COOターンアラウンドモード"""
    section_prompts = {
        "coo_diagnosis": prompt_coo_diagnosis(context),
        "vrio":          prompt_vrio(context),
        "cashflow":      prompt_cashflow(context),
    }

    results_list = await asyncio.gather(
        *[call_section(p, k) for k, p in section_prompts.items()],
        return_exceptions=True,
    )

    results = {}
    for key, result in zip(section_prompts.keys(), results_list):
        results[key] = str(result) if isinstance(result, Exception) else result

    summary = "\n\n".join(f"=== {k.upper()} ===\n{v[:800]}" for k, v in results.items())
    results["cross_swot"] = await call_section(
        prompt_cross_swot(f"{context}\n\n## 各セクション評価サマリー\n{summary}"),
        "cross_swot",
    )
    return results


# ============================================================
# ルーティング
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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

    competitors = await fetch_competitors(idea_summary)
    context_with_competitors = context + f"\n\n## 主要競合（AI自動調査）\n{competitors}"

    results = (
        await evaluate_startup(context_with_competitors)
        if mode == "startup"
        else await evaluate_coo(context_with_competitors)
    )
    results["competitors"] = competitors

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "mode": mode,
            "business_idea": business_idea,
            "results": results,
            "models": MODELS,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "models": MODELS}
