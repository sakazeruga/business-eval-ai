"""利用上限のテスト。外部APIは呼ばない（上限0か入力超過で先に弾かれる経路のみ）。

実行: python tests/test_limits.py （リポジトリ直下から）
"""
import asyncio
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

# 1ユーザー評価=2回, 全体=3回, 調査=0回。ADMIN は別枠。承認ゲートは無効化して上限だけを見る
os.environ.update({
    "LIMIT_EVALUATE_PER_USER": "2", "LIMIT_EVALUATE_GLOBAL": "3",
    "LIMIT_RESEARCH_PER_USER": "0", "LIMIT_CHAT_PER_USER": "1",
    "ADMIN_EMAILS": "owner@example.com", "OPEN_ACCESS": "true",
})
import main as M  # noqa: E402
import store  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(M.app)
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)) if detail and not cond else ""))
    if not cond:
        fails += 1


def H(email):
    return {"cf-access-authenticated-user-email": email}


class Req:
    def __init__(self, email):
        self.headers = {"cf-access-authenticated-user-email": email}


def charge(email, kind):
    return asyncio.run(M.charge(Req(email), kind))


def used(email, kind):
    return asyncio.run(store.get_int(M._usage_key(kind, email)))


# --- charge() の単体 ---
store.reset_memory_for_tests()
check("1回目は通る", charge("a@x.com", "evaluate") is None)
check("2回目は通る", charge("a@x.com", "evaluate") is None)
r = charge("a@x.com", "evaluate")
check("3回目はユーザー上限で拒否", r and "上限（2回）" in r, r)
check("別ユーザーは通る", charge("b@x.com", "evaluate") is None)
r = charge("c@x.com", "evaluate")
check("全体上限(3)に達すると他ユーザーも拒否", r and "全体上限" in r, r)
check("管理者は上限を受けない", all(charge("owner@example.com", "evaluate") is None for _ in range(10)))
check("管理者の利用は全体枠を消費しない", used("*", "evaluate") == 3, used("*", "evaluate"))
check("メールの大文字小文字を区別しない", charge("OWNER@Example.com", "evaluate") is None)

# --- HTTP 層 ---
store.reset_memory_for_tests()
res = c.post("/autofill", data={"url": "example.com", "mode": "coo"}, headers=H("a@x.com"))
check("調査の上限0 → 429 JSON", res.status_code == 429 and "error" in res.json(), (res.status_code, res.text[:80]))

res = c.post("/chat", data={"report": "x", "question": "q" * (M.MAX_QUESTION_CHARS + 1)}, headers=H("a@x.com"))
check("質問が長すぎる → 413", res.status_code == 413, res.status_code)
check("サイズ超過では回数を消費しない", used("a@x.com", "chat") == 0, used("a@x.com", "chat"))

res = c.post("/chat", data={"report": "r" * (M.MAX_REPORT_CHARS + 1), "question": "q"}, headers=H("a@x.com"))
check("レポートが巨大 → 413", res.status_code == 413, res.status_code)

store.reset_memory_for_tests()
res = c.post("/evaluate", data={"mode": "coo", "problem": "p" * (M.MAX_EVAL_CHARS + 1)}, headers=H("a@x.com"))
check("評価入力が巨大 → 413 HTML", res.status_code == 413 and "<html" in res.text, res.status_code)
check("巨大入力では回数を消費しない", used("a@x.com", "evaluate") == 0, used("a@x.com", "evaluate"))

# 評価の上限超過（実行に入る前に弾かれる）
charge("a@x.com", "evaluate")
charge("a@x.com", "evaluate")
res = c.post("/evaluate", data={"mode": "coo", "problem": "x"}, headers=H("a@x.com"))
check("評価の上限超過 → 429 HTML", res.status_code == 429 and "上限（2回）" in res.text, res.status_code)
check("エラーページはHTMLエスケープ済み", "<script" not in res.text)

# 同時実行の混雑
store.reset_memory_for_tests()
orig = M.EVAL_SLOTS
sem = asyncio.Semaphore(1)
sem._value = 0  # 満席状態
M.EVAL_SLOTS = sem
res = c.post("/evaluate", data={"mode": "coo", "problem": "x"}, headers=H("a@x.com"))
check("満席 → 429（混雑）", res.status_code == 429 and "集中" in res.text, res.status_code)
check("満席で弾かれた場合は回数を消費しない", used("a@x.com", "evaluate") == 0, used("a@x.com", "evaluate"))
M.EVAL_SLOTS = orig

# /usage
store.reset_memory_for_tests()
charge("a@x.com", "evaluate")
u = c.get("/usage", headers=H("a@x.com")).json()
check("/usage が使用回数と上限を返す", u["usage"]["evaluate"] == {"used": 1, "limit": 2}, u)
u = c.get("/usage", headers=H("owner@example.com")).json()
check("管理者の limit は null", u["usage"]["evaluate"]["limit"] is None, u)

# 共有ストアが落ちても利用は止めない（集計だけ諦める）
async def boom(*a, **k):
    raise store.StoreError("boom")


orig_incr = store.incr
store.incr = boom
check("ストア障害時でも上限判定で止めない", charge("a@x.com", "evaluate") is None)
store.incr = orig_incr

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
