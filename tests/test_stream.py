"""評価ストリーミングの回帰テスト。外部APIは呼ばずスタブに差し替える。

実行: python tests/test_stream.py （リポジトリ直下から）
"""
import asyncio
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
os.environ.setdefault("OPEN_ACCESS", "true")  # 承認ゲートは別テストで検証する

import main as M  # noqa: E402
import store  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

calls = []
prompts_seen: dict[str, str] = {}
ASSUMPTION_MARK = "TEST-ASSUMPTION-MARK"


async def fake_call_section(prompt_text, key):
    await asyncio.sleep(0.05)
    calls.append(key)
    prompts_seen[key] = prompt_text
    return f"### {key}\n本文 {key}"


async def fake_assumptions(context, mode):
    return {"display": f"### 前提数値\n{ASSUMPTION_MARK}",
            "context": f"## 前提数値（全セクション共通・確定）\n{ASSUMPTION_MARK}"}


QUERIES = {"competitors": ["業務 自動化 SaaS"], "why_now": ["業務 DX 市場規模 2025"]}
MEMO_MARK = "TEST-RESEARCH-MEMO"
perplexity_inputs: list[str] = []


async def fake_search_plan(context):
    return {**QUERIES, "dropped": 0}


async def fake_perplexity(section_key, instructions, input_text):
    await asyncio.sleep(0.1)  # 前提数値より遅く終わる想定
    perplexity_inputs.append(instructions + "\n" + input_text)
    return f"- {MEMO_MARK}", {"https://example.com/a": "A社"}


# 本物の OpenRouter / Perplexity は呼ばない。research / fetch_competitors は本物を通す
M.call_section = fake_call_section
M.build_assumptions = fake_assumptions
M.build_search_plan = fake_search_plan
M.perplexity_agent = fake_perplexity

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)) if detail and not cond else ""))
    if not cond:
        fails += 1


c = TestClient(M.app)
EXPECT = {
    "coo": ["coo_diagnosis", "vrio", "cashflow", "cross_swot"],
    "startup": ["why_me", "why_now", "vrio", "marketing", "operations",
                "cashflow", "npv_exit", "cross_swot"],
}

for mode, expect in EXPECT.items():
    store.reset_memory_for_tests()
    calls.clear()
    perplexity_inputs.clear()
    res = c.post("/evaluate", data={"mode": mode, "problem": "テスト課題SECRET-IDEA",
                                    "solution": "秘密の仕組みSECRET-SOLUTION"},
                 headers={"cf-access-authenticated-user-email": "t@x.com"})
    body = res.text
    filled = re.findall(r'fillSection\("([a-z_]+)"', body)
    research_keys = ["competitors", "why_now"] if mode == "startup" else ["competitors"]
    check(f"{mode}: Perplexity は調査ごとに1回だけ呼ばれる",
          len(perplexity_inputs) == len(research_keys), len(perplexity_inputs))
    check(f"{mode}: Perplexity に事業構想を送らない",
          perplexity_inputs and not any("SECRET" in x or "t@x.com" in x for x in perplexity_inputs),
          perplexity_inputs)
    check(f"{mode}: Perplexity に検索語を送る",
          all(any(q in x for x in perplexity_inputs) for k in research_keys for q in QUERIES[k]),
          perplexity_inputs)
    check(f"{mode}: 調査結果をもとに書く（競合・Why Now）",
          all(MEMO_MARK in prompts_seen.get(k, "") for k in research_keys),
          {k: MEMO_MARK in prompts_seen.get(k, "") for k in research_keys})
    check(f"{mode}: 送った検索語をレポートに表示する",
          all(f"`{q}`" in body for k in research_keys for q in QUERIES[k]))
    check(f"{mode}: 出典をレポートに表示する", "https://example.com/a" in body)
    check(f"{mode}: 200で完走", res.status_code == 200, res.status_code)
    check(f"{mode}: 全セクションが流れる", set(expect) <= set(filled), filled)
    check(f"{mode}: 前提数値カードが流れる", "assumptions" in filled, filled)
    check(f"{mode}: 前提数値は評価セクションより先", filled.index("assumptions") < filled.index(expect[0])
          if "assumptions" in filled and expect[0] in filled else False, filled)
    numeric = [k for k in ("marketing", "cashflow", "npv_exit", "cross_swot") if k in prompts_seen]
    check(f"{mode}: 数値系セクションに前提数値が渡る",
          numeric and all(ASSUMPTION_MARK in prompts_seen[k] for k in numeric),
          {k: ASSUMPTION_MARK in prompts_seen[k] for k in numeric})
    prompts_seen.clear()
    check(f"{mode}: クロスSWOTは最後", filled and filled[-1] == "cross_swot", filled)
    check(f"{mode}: finishStreaming が呼ばれる", "finishStreaming()" in body)
    check(f"{mode}: HTMLが閉じている", body.rstrip().endswith("</html>"))
    check(f"{mode}: 回数が1だけ消費される",
          asyncio.run(store.get_int(M._usage_key("evaluate", "t@x.com"))) == 1)
    check(f"{mode}: 同時実行枠が解放される",
          not M.EVAL_SLOTS.locked() and M.EVAL_SLOTS._value == M.MAX_CONCURRENT_EVALS,
          M.EVAL_SLOTS._value)

check("全実行後も枠が満席にならない", M.EVAL_SLOTS._value == M.MAX_CONCURRENT_EVALS)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
