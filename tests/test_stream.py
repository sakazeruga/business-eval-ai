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


async def fake_call_section(prompt_text, key):
    await asyncio.sleep(0.05)
    calls.append(key)
    return f"### {key}\n本文 {key}"


async def fake_competitors(summary):
    return "### 競合\n- A社"


M.call_section = fake_call_section
M.fetch_competitors = fake_competitors

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
    res = c.post("/evaluate", data={"mode": mode, "problem": "テスト課題"},
                 headers={"cf-access-authenticated-user-email": "t@x.com"})
    body = res.text
    filled = re.findall(r'fillSection\("([a-z_]+)"', body)
    check(f"{mode}: 200で完走", res.status_code == 200, res.status_code)
    check(f"{mode}: 全セクションが流れる", set(expect) <= set(filled), filled)
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
