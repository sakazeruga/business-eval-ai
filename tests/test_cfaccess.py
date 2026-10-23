"""Cloudflare Access のトークン検証テスト。

実在の脆弱性（cf-access-authenticated-user-email を自分で付ければ
管理者になれた）が塞がれていることを確認する。外部APIは呼ばない。
"""
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

TEAM = "testteam.cloudflareaccess.com"
AUD = "test-aud-tag"
os.environ.update({
    "REQUIRE_CF_ACCESS": "true",
    "ADMIN_EMAILS": "owner@example.com",
    "CF_ACCESS_TEAM_DOMAIN": TEAM,
    "CF_ACCESS_AUD": AUD,
    "OPEN_ACCESS": "true",   # 承認は別テスト。ここでは本人確認だけを見る
})

import cfaccess  # noqa: E402
import main as M  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)) if detail and not cond else ""))
    if not cond:
        fails += 1


# --- 署名鍵を用意し、JWKS 取得をローカル鍵に差し替える ---
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption())
other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
other_pem = other.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())


class FakeKey:
    def __init__(self, k):
        self.key = k


class FakeJWKClient:
    def __init__(self, *a, **k):
        pass

    def get_signing_key_from_jwt(self, token):
        return FakeKey(key.public_key())


cfaccess.PyJWKClient = FakeJWKClient
cfaccess._jwk_client = None


def make_token(email="owner@example.com", aud=AUD, iss=f"https://{TEAM}",
               exp_delta=3600, signer=pem, **extra):
    now = int(time.time())
    payload = {"email": email, "aud": aud, "iss": iss,
               "iat": now, "exp": now + exp_delta, **extra}
    return jwt.encode(payload, signer, algorithm="RS256")


c = TestClient(M.app)


def H(**kw):
    return kw


check("検証が有効になっている", cfaccess.VERIFY_ENABLED)

# --- 本題: ヘッダー偽装が通らないこと ---
r = c.get("/admin", headers={"cf-access-authenticated-user-email": "owner@example.com"})
check("メールヘッダーだけの偽装は403", r.status_code == 403, r.status_code)

r = c.get("/usage", headers={"cf-access-authenticated-user-email": "owner@example.com"})
check("偽装ヘッダーでは /usage も403", r.status_code == 403, r.status_code)

r = c.post("/evaluate", data={"mode": "coo", "problem": "x"},
           headers={"cf-access-authenticated-user-email": "owner@example.com"})
check("偽装ヘッダーでは評価も403", r.status_code == 403, r.status_code)

# --- 正しいトークンなら通る ---
tok = make_token()
r = c.get("/admin", headers={"cf-access-jwt-assertion": tok})
check("正しいトークンの管理者は管理画面に入れる", r.status_code == 200, r.status_code)

r = c.get("/usage", headers={"cf-access-jwt-assertion": tok})
check("利用者は検証済みメールで識別される",
      r.json().get("user") == "owner@example.com", r.text[:90])
check("検証済み管理者は上限免除", r.json()["usage"]["evaluate"]["limit"] is None, r.text[:90])

r = c.get("/usage", cookies={"CF_Authorization": tok})
check("Cookie CF_Authorization でも通る", r.status_code == 200, r.status_code)

# --- 不正なトークンは弾く ---
cases = [
    ("署名が違う", make_token(signer=other_pem)),
    ("AUDが違う", make_token(aud="someone-elses-app")),
    ("発行者が違う", make_token(iss="https://evil.cloudflareaccess.com")),
    ("期限切れ", make_token(exp_delta=-10)),
    ("壊れた文字列", "not.a.token"),
    ("alg=none", jwt.encode({"email": "owner@example.com", "aud": AUD,
                             "iss": f"https://{TEAM}", "iat": int(time.time()),
                             "exp": int(time.time()) + 600}, None, algorithm="none")),
]
for label, bad in cases:
    r = c.get("/admin", headers={"cf-access-jwt-assertion": bad})
    check(f"{label} トークンは403", r.status_code == 403, r.status_code)

# --- 一般利用者は管理者になれない ---
r = c.get("/admin", headers={"cf-access-jwt-assertion": make_token(email="user@example.com")})
check("検証済みでも非管理者は管理画面に入れない", r.status_code == 403, r.status_code)

r = c.get("/usage", headers={"cf-access-jwt-assertion": make_token(email="user@example.com")})
check("非管理者には上限が適用される", r.json()["usage"]["evaluate"]["limit"] is not None, r.text[:90])

# --- 検証が未設定のときは管理者権限を与えない（被害を限定する） ---
os.environ["CF_ACCESS_TEAM_DOMAIN"] = ""
os.environ["CF_ACCESS_AUD"] = ""
import importlib  # noqa: E402

cfaccess2 = importlib.reload(cfaccess)
M2 = importlib.reload(M)
c2 = TestClient(M2.app)
check("未設定では検証が無効", not cfaccess2.VERIFY_ENABLED)

r = c2.get("/admin", headers={"cf-access-authenticated-user-email": "owner@example.com"})
check("未設定では管理画面を開けない(503)", r.status_code == 503 and "CF_ACCESS" in r.text, r.status_code)

r = c2.get("/usage", headers={"cf-access-authenticated-user-email": "owner@example.com"})
check("未設定では上限免除も効かない", r.json()["usage"]["evaluate"]["limit"] is not None, r.text[:90])

r = c2.get("/health")
check("/health に認証状態が出る", r.json().get("access") == "unverified", r.text[:90])

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
