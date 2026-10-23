"""利用許可の管理。

Cloudflare Access は「本人かどうか」を確認するだけで、Access のポリシーを
緩める（例: Google アカウントなら誰でも）と、誰でも入れてしまう。
そこで「使ってよいか」はアプリ側で持ち、Cloudflare の画面を触らずに
承認できるようにする。

許可の出し方は3通りで、併用できる。
  1. 申請 → 管理画面でワンタップ承認
  2. 招待リンク / QR（期限と人数の上限つき）
  3. ドメイン一致で自動承認（AUTO_APPROVE_DOMAINS）
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import store

logger = logging.getLogger("business_eval.access")

JST = timezone(timedelta(hours=9))

K_APPROVED = "access:approved"      # set of email
K_PENDING = "access:pending"        # hash email -> {requested_at, note}
K_INVITE = "access:invite:"         # + token -> {expires_at, max_uses, used, label}
K_PROFILE = "access:profile:"       # + email -> {approved_at, via}

ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
AUTO_APPROVE_DOMAINS = {
    d.strip().lower().lstrip("@")
    for d in os.getenv("AUTO_APPROVE_DOMAINS", "").split(",")
    if d.strip()
}
# 承認を求めず誰でも使わせる場合に true（検証用。既定は false）
OPEN_ACCESS = os.getenv("OPEN_ACCESS", "false").lower() == "true"

MAX_PENDING = 200
INVITE_MAX_DAYS = 90
INVITE_MAX_USES = 500


def now_jst() -> datetime:
    return datetime.now(JST)


def is_admin(email: str) -> bool:
    return email in ADMIN_EMAILS


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1] if "@" in email else ""


async def is_approved(email: str) -> bool:
    """この利用者が使ってよいか。"""
    if OPEN_ACCESS or is_admin(email):
        return True
    if AUTO_APPROVE_DOMAINS and domain_of(email) in AUTO_APPROVE_DOMAINS:
        # 初回アクセス時に記録しておくと、管理画面で誰が使っているか分かる
        if not await store.sismember(K_APPROVED, email):
            await approve(email, via="domain")
        return True
    return await store.sismember(K_APPROVED, email)


async def approve(email: str, via: str = "admin") -> None:
    await store.sadd(K_APPROVED, email)
    await store.hdel(K_PENDING, email)
    await store.set_json(K_PROFILE + email, {"approved_at": now_jst().isoformat(), "via": via})
    logger.info("access approved email=%s via=%s", email, via)


async def revoke(email: str) -> None:
    await store.srem(K_APPROVED, email)
    await store.delete(K_PROFILE + email)
    logger.info("access revoked email=%s", email)


async def request_access(email: str, note: str = "") -> str:
    """利用申請を登録する。利用者向けの結果メッセージを返す。"""
    if await is_approved(email):
        return "すでに利用できます。"
    pending = await store.hgetall_json(K_PENDING)
    if email in pending:
        return "申請は受付済みです。承認までお待ちください。"
    if len(pending) >= MAX_PENDING:
        return "申請が混み合っています。しばらくしてからお試しください。"
    await store.hset_json(K_PENDING, email, {
        "requested_at": now_jst().isoformat(),
        "note": note[:200],
    })
    logger.info("access requested email=%s", email)
    return "申請を受け付けました。承認されるとご利用いただけます。"


async def list_pending() -> list[dict]:
    items = await store.hgetall_json(K_PENDING)
    rows = [{"email": e, **(v or {})} for e, v in items.items()]
    return sorted(rows, key=lambda r: r.get("requested_at", ""), reverse=True)


async def list_approved() -> list[dict]:
    emails = await store.smembers(K_APPROVED)
    rows = []
    for e in emails:
        prof = await store.get_json(K_PROFILE + e, {}) or {}
        rows.append({"email": e, **prof})
    return sorted(rows, key=lambda r: r.get("approved_at", ""), reverse=True)


# ============================================================
# 招待リンク / QR
# ============================================================
async def create_invite(days: int = 7, max_uses: int = 50, label: str = "") -> dict:
    days = max(1, min(int(days), INVITE_MAX_DAYS))
    max_uses = max(1, min(int(max_uses), INVITE_MAX_USES))
    token = secrets.token_urlsafe(16)
    expires = now_jst() + timedelta(days=days)
    rec = {
        "token": token,
        "created_at": now_jst().isoformat(),
        "expires_at": expires.isoformat(),
        "max_uses": max_uses,
        "used": 0,
        "label": label[:60],
    }
    # 期限が切れたら自動で消えるようにする（猶予1日）
    await store.set_json(K_INVITE + token, rec, ttl=(days + 1) * 86400)
    logger.info("invite created token=%s days=%s max_uses=%s", token[:6], days, max_uses)
    return rec


async def get_invite(token: str) -> dict | None:
    return await store.get_json(K_INVITE + token)


async def redeem_invite(token: str, email: str) -> tuple[bool, str]:
    """招待を使って承認する。(成功したか, 利用者向けメッセージ) を返す。"""
    rec = await get_invite(token)
    if not rec:
        return False, "この招待リンクは無効か、期限が切れています。"
    try:
        expires = datetime.fromisoformat(rec["expires_at"])
    except (KeyError, ValueError):
        return False, "この招待リンクは無効です。"
    if now_jst() > expires:
        await store.delete(K_INVITE + token)
        return False, "この招待リンクは期限が切れています。"

    if await is_approved(email):
        return True, "すでに利用できます。"

    if int(rec.get("used", 0)) >= int(rec.get("max_uses", 0)):
        return False, "この招待リンクは利用上限に達しています。"

    rec["used"] = int(rec.get("used", 0)) + 1
    await store.set_json(K_INVITE + token, rec)
    await approve(email, via=f"invite:{rec.get('label') or token[:6]}")
    return True, "ご利用いただけます。"
