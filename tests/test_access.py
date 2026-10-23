"""承認フローのテスト。外部APIは呼ばない。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

os.environ.update({
    "ADMIN_EMAILS": "owner@example.com",
    "AUTO_APPROVE_DOMAINS": "gs.example.jp",
    "LIMIT_EVALUATE_PER_USER": "2",
})

import access  # noqa: E402
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


store.reset_memory_for_tests()

# --- 未承認はゲートで止まる ---
r = c.get("/", headers=H("new@x.com"), follow_redirects=False)
check("未承認のGETは申請画面へ誘導", r.status_code == 303 and r.headers["location"] == "/no-access", (r.status_code, r.headers.get("location")))

r = c.post("/evaluate", data={"mode": "coo", "problem": "x"}, headers=H("new@x.com"))
check("未承認のPOSTは403", r.status_code == 403 and "許可" in r.json()["error"], (r.status_code, r.text[:80]))

r = c.get("/no-access", headers=H("new@x.com"))
check("申請画面は未承認でも開ける", r.status_code == 200 and "利用申請" in r.text, r.status_code)

r = c.get("/health")
check("ヘルスチェックは承認不要", r.status_code == 200)

# --- 申請 → 承認 ---
r = c.post("/request", data={"note": "G'sの受講生です"}, headers=H("new@x.com"))
check("申請を受け付ける", r.status_code == 200 and "受け付け" in r.json()["message"], r.text[:80])

r = c.post("/request", data={"note": ""}, headers=H("new@x.com"))
check("二重申請は受付済みと返す", "受付済み" in r.json()["message"], r.text[:80])

r = c.get("/admin/data", headers=H("new@x.com"), follow_redirects=False)
check("未承認の管理APIは申請画面へ", r.status_code == 303, r.status_code)

r = c.get("/admin/data", headers=H("owner@example.com"))
d = r.json()
check("管理者は申請一覧を見られる", r.status_code == 200 and d["pending"][0]["email"] == "new@x.com", d)
check("申請メモが残る", d["pending"][0]["note"] == "G'sの受講生です", d["pending"][0])

r = c.post("/admin/approve", data={"email": "NEW@x.com"}, headers=H("owner@example.com"))
check("承認できる（大文字小文字を正規化）", r.json().get("email") == "new@x.com", r.text[:80])

r = c.get("/", headers=H("new@x.com"), follow_redirects=False)
check("承認後はサービスを使える", r.status_code == 200, r.status_code)

r = c.get("/admin/data", headers=H("new@x.com"))
check("承認済みでも管理APIは403", r.status_code == 403, r.status_code)
r = c.get("/admin", headers=H("new@x.com"))
check("承認済みでも管理画面は403", r.status_code == 403 and "権限" in r.text, r.status_code)
r = c.post("/admin/approve", data={"email": "hacker@x.com"}, headers=H("new@x.com"))
check("一般利用者は承認操作できない", r.status_code == 403, r.status_code)
r = c.post("/admin/invite", data={"days": "7"}, headers=H("new@x.com"))
check("一般利用者は招待を発行できない", r.status_code == 403, r.status_code)

d = c.get("/admin/data", headers=H("owner@example.com")).json()
check("承認すると申請一覧から消える", d["pending"] == [], d["pending"])
check("利用中一覧に載る", any(x["email"] == "new@x.com" for x in d["approved"]), d["approved"])

# --- 解除 ---
c.post("/admin/revoke", data={"email": "new@x.com"}, headers=H("owner@example.com"))
r = c.get("/", headers=H("new@x.com"), follow_redirects=False)
check("解除すると使えなくなる", r.status_code == 303, r.status_code)

r = c.post("/admin/revoke", data={"email": "owner@example.com"}, headers=H("owner@example.com"))
check("管理者は解除できない", r.status_code == 400, r.status_code)

# --- 管理者とドメイン自動承認 ---
r = c.get("/", headers=H("owner@example.com"), follow_redirects=False)
check("管理者は承認なしで使える", r.status_code == 200, r.status_code)

r = c.get("/", headers=H("student@gs.example.jp"), follow_redirects=False)
check("自動承認ドメインは通る", r.status_code == 200, r.status_code)
d = c.get("/admin/data", headers=H("owner@example.com")).json()
check("自動承認も一覧に記録される",
      any(x["email"] == "student@gs.example.jp" and x.get("via") == "domain" for x in d["approved"]), d["approved"])

# --- 招待リンク ---
r = c.post("/admin/invite", data={"days": "7", "max_uses": "2", "label": "テスト期"},
           headers=H("owner@example.com"))
inv = r.json()
check("招待を発行できる", r.status_code == 200 and "/invite/" in inv["url"], r.text[:120])
check("発行時の上限が反映される", inv["max_uses"] == 2 and inv["used"] == 0, inv)
token = inv["token"]

r = c.get(f"/invite/{token}", headers=H("a@invited.com"))
check("招待で自動承認される", r.status_code == 200 and "ご利用いただけます" in r.text, r.status_code)
r = c.get("/", headers=H("a@invited.com"), follow_redirects=False)
check("招待された人はサービスを使える", r.status_code == 200, r.status_code)

c.get(f"/invite/{token}", headers=H("b@invited.com"))
r = c.get(f"/invite/{token}", headers=H("c@invited.com"))
check("招待の人数上限で止まる", r.status_code == 403 and "上限" in r.text, r.status_code)

r = c.get("/invite/notarealtoken", headers=H("d@invited.com"))
check("無効な招待は拒否", r.status_code == 403 and "無効" in r.text, r.status_code)

d = c.get("/admin/data", headers=H("owner@example.com")).json()
check("招待経由はviaに記録される",
      any(x["email"] == "a@invited.com" and "invite:テスト期" in (x.get("via") or "") for x in d["approved"]),
      d["approved"])

# --- 期限切れ ---
import asyncio  # noqa: E402
rec = asyncio.run(access.get_invite(token))
rec["expires_at"] = "2020-01-01T00:00:00+09:00"
asyncio.run(store.set_json(access.K_INVITE + token, rec))
r = c.get(f"/invite/{token}", headers=H("e@invited.com"))
check("期限切れの招待は拒否", r.status_code == 403 and "期限" in r.text, r.status_code)

# --- 利用上限が共有ストア経由で効く ---
store.reset_memory_for_tests()
asyncio.run(access.approve("limit@x.com"))
r = c.post("/autofill", data={"url": "example.com"}, headers=H("limit@x.com"))
check("承認済みなら上限判定まで進む", r.status_code != 403, r.status_code)

u = c.get("/usage", headers=H("owner@example.com")).json()
check("管理者のlimitはnull", u["usage"]["evaluate"]["limit"] is None, u)

# --- 共有ストア障害時は開放しない ---
async def boom(*a, **k):
    raise store.StoreError("boom")


orig = store.sismember
store.sismember = boom
r = c.get("/", headers=H("unknown@x.com"), follow_redirects=False)
check("ストア障害時はGETを通さない(503)", r.status_code == 503, r.status_code)
r = c.post("/chat", data={"report": "r", "question": "q"}, headers=H("unknown@x.com"))
check("ストア障害時はPOSTを通さない(503)", r.status_code == 503, r.status_code)
store.sismember = orig
r = c.get("/", headers=H("owner@example.com"), follow_redirects=False)
check("復旧後は通る", r.status_code == 200, r.status_code)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
