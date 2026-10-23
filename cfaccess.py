"""Cloudflare Access のトークン検証。

Cloudflare Access は認証済みリクエストに
  - cf-access-authenticated-user-email（メールアドレス）
  - Cf-Access-Jwt-Assertion（署名付きトークン。Cookie CF_Authorization にも入る）
を付ける。

前者は「ヘッダーがあるか」しか確認できず、オリジン（*.fly.dev）が
インターネットから直接叩ける以上、攻撃者が自分で付けられてしまう。
実際に偽装でき、管理画面に入れる状態だった。

そこで署名付きトークンを Cloudflare の公開鍵で検証する。
公開鍵はチーム固有の JWKS から取得し、一定時間キャッシュする。
"""

import logging
import os
import time

import httpx
import jwt
from jwt import PyJWKClient

logger = logging.getLogger("business_eval.cfaccess")

# 例: "yourteam.cloudflareaccess.com"（https:// は付けても付けなくてもよい）
TEAM_DOMAIN = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/")
TEAM_DOMAIN = TEAM_DOMAIN.removeprefix("https://").removeprefix("http://")
# Access アプリケーションの Application Audience (AUD) Tag
AUD = os.getenv("CF_ACCESS_AUD", "").strip()

VERIFY_ENABLED = bool(TEAM_DOMAIN and AUD)
ISSUER = f"https://{TEAM_DOMAIN}" if TEAM_DOMAIN else ""
CERTS_URL = f"{ISSUER}/cdn-cgi/access/certs" if ISSUER else ""

_jwk_client: PyJWKClient | None = None
_jwk_created = 0.0
_JWK_TTL = 3600  # 鍵の入れ替えに追従するため1時間で作り直す


def _client() -> PyJWKClient:
    global _jwk_client, _jwk_created
    if _jwk_client is None or time.time() - _jwk_created > _JWK_TTL:
        _jwk_client = PyJWKClient(CERTS_URL, cache_keys=True, lifespan=_JWK_TTL)
        _jwk_created = time.time()
    return _jwk_client


class InvalidAccessToken(Exception):
    """Cloudflare Access のトークンが無効。"""


def extract_token(request) -> str:
    return (
        request.headers.get("cf-access-jwt-assertion")
        or request.cookies.get("CF_Authorization")
        or ""
    )


def verify(token: str) -> str:
    """トークンを検証し、確認済みのメールアドレスを返す。失敗時は InvalidAccessToken。"""
    if not token:
        raise InvalidAccessToken("トークンがありません")
    try:
        key = _client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=AUD,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise InvalidAccessToken(str(e)) from e
    except (httpx.HTTPError, OSError) as e:  # JWKS を取得できない
        raise InvalidAccessToken(f"公開鍵を取得できません: {e}") from e

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise InvalidAccessToken("メールアドレスが含まれていません")
    return email


def describe() -> str:
    return f"verified({TEAM_DOMAIN})" if VERIFY_ENABLED else "unverified"
