"""共有ストア。

Fly のマシンは複数あり、停止・再起動・デプロイでメモリが消える。
承認リストや利用回数のように複数マシンで共有したい状態は Upstash Redis
（REST API）に置く。未設定の環境ではプロセス内メモリで代替するため、
ローカル開発やテストは設定なしで動く。
"""

import asyncio
import json
import logging
import os
import time

import httpx

logger = logging.getLogger("business_eval.store")

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
ENABLED = bool(UPSTASH_URL and UPSTASH_TOKEN)


class StoreError(RuntimeError):
    """共有ストアへの読み書きに失敗した。"""


# ============================================================
# メモリ実装（共有ストア未設定時のフォールバック）
# ============================================================
class _Memory:
    def __init__(self) -> None:
        self._v: dict[str, object] = {}
        self._exp: dict[str, float] = {}

    def _sweep(self, key: str) -> None:
        exp = self._exp.get(key)
        if exp is not None and exp <= time.time():
            self._v.pop(key, None)
            self._exp.pop(key, None)

    async def get(self, key):
        self._sweep(key)
        return self._v.get(key)

    async def set(self, key, value, ttl=None):
        self._v[key] = value
        if ttl:
            self._exp[key] = time.time() + ttl
        else:
            self._exp.pop(key, None)

    async def delete(self, *keys):
        for k in keys:
            self._v.pop(k, None)
            self._exp.pop(k, None)

    async def incr(self, key, ttl=None):
        self._sweep(key)
        n = int(self._v.get(key, 0)) + 1
        self._v[key] = n
        if ttl and key not in self._exp:
            self._exp[key] = time.time() + ttl
        return n

    async def sadd(self, key, member):
        self._sweep(key)
        s = self._v.setdefault(key, set())
        before = len(s)
        s.add(member)
        return len(s) - before

    async def srem(self, key, member):
        self._sweep(key)
        s = self._v.get(key)
        if isinstance(s, set) and member in s:
            s.discard(member)
            return 1
        return 0

    async def sismember(self, key, member):
        self._sweep(key)
        s = self._v.get(key)
        return isinstance(s, set) and member in s

    async def smembers(self, key):
        self._sweep(key)
        s = self._v.get(key)
        return sorted(s) if isinstance(s, set) else []

    async def hset(self, key, field, value):
        self._sweep(key)
        self._v.setdefault(key, {})[field] = value

    async def hdel(self, key, field):
        self._sweep(key)
        h = self._v.get(key)
        if isinstance(h, dict):
            h.pop(field, None)

    async def hgetall(self, key):
        self._sweep(key)
        h = self._v.get(key)
        return dict(h) if isinstance(h, dict) else {}

    async def keys_count(self) -> int:
        return len(self._v)


_memory = _Memory()


# ============================================================
# Upstash Redis REST
# ============================================================
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    base_url=UPSTASH_URL,
                    headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                    timeout=10,
                )
    return _client


async def _cmd(*args):
    """Upstash にコマンドを1件送る。失敗時は StoreError。"""
    client = await _http()
    payload = [str(a) for a in args]
    last: Exception | None = None
    for attempt in range(2):  # 一時的なネットワーク断で落とさない
        try:
            resp = await client.post("/", json=payload)
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                raise StoreError(f"{args[0]}: {data['error']}")
            if resp.status_code != 200:
                raise StoreError(f"{args[0]}: HTTP {resp.status_code}")
            return data.get("result")
        except StoreError:
            raise
        except Exception as e:  # 接続・タイムアウト・JSON不正
            last = e
            if attempt == 0:
                await asyncio.sleep(0.2)
    raise StoreError(f"{args[0]}: {last}")


class _Upstash:
    async def get(self, key):
        return await _cmd("GET", key)

    async def set(self, key, value, ttl=None):
        if ttl:
            await _cmd("SET", key, value, "EX", int(ttl))
        else:
            await _cmd("SET", key, value)

    async def delete(self, *keys):
        if keys:
            await _cmd("DEL", *keys)

    async def incr(self, key, ttl=None):
        n = int(await _cmd("INCR", key))
        if ttl and n == 1:  # 最初の加算時にだけ寿命を設定する
            await _cmd("EXPIRE", key, int(ttl))
        return n

    async def sadd(self, key, member):
        return int(await _cmd("SADD", key, member) or 0)

    async def srem(self, key, member):
        return int(await _cmd("SREM", key, member) or 0)

    async def sismember(self, key, member):
        return bool(await _cmd("SISMEMBER", key, member))

    async def smembers(self, key):
        return sorted(await _cmd("SMEMBERS", key) or [])

    async def hset(self, key, field, value):
        await _cmd("HSET", key, field, value)

    async def hdel(self, key, field):
        await _cmd("HDEL", key, field)

    async def hgetall(self, key):
        raw = await _cmd("HGETALL", key) or []
        # HGETALL は field, value の並びで返る
        if isinstance(raw, dict):
            return raw
        return {raw[i]: raw[i + 1] for i in range(0, len(raw) - 1, 2)}


_upstash = _Upstash()


def backend():
    return _upstash if ENABLED else _memory


def describe() -> str:
    return "upstash" if ENABLED else "memory"


# ============================================================
# 公開 API（JSON 値の出し入れを含む）
# ============================================================
async def get_json(key: str, default=None):
    raw = await backend().get(key)
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


async def set_json(key: str, value, ttl: int | None = None) -> None:
    await backend().set(key, json.dumps(value, ensure_ascii=False), ttl)


async def delete(*keys: str) -> None:
    await backend().delete(*keys)


async def incr(key: str, ttl: int | None = None) -> int:
    return await backend().incr(key, ttl)


async def get_int(key: str) -> int:
    raw = await backend().get(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def sadd(key: str, member: str) -> int:
    return await backend().sadd(key, member)


async def srem(key: str, member: str) -> int:
    return await backend().srem(key, member)


async def sismember(key: str, member: str) -> bool:
    return await backend().sismember(key, member)


async def smembers(key: str) -> list[str]:
    return await backend().smembers(key)


async def hset_json(key: str, field: str, value) -> None:
    await backend().hset(key, field, json.dumps(value, ensure_ascii=False))


async def hdel(key: str, field: str) -> None:
    await backend().hdel(key, field)


async def hgetall_json(key: str) -> dict:
    raw = await backend().hgetall(key)
    out = {}
    for field, value in raw.items():
        if isinstance(value, (dict, list)):
            out[field] = value
            continue
        try:
            out[field] = json.loads(value)
        except (TypeError, ValueError):
            out[field] = {}
    return out


async def healthy() -> bool:
    """共有ストアに到達できるか。未設定なら常に True（メモリ動作）。"""
    if not ENABLED:
        return True
    try:
        await _cmd("PING")
        return True
    except StoreError as e:
        logger.warning("store unhealthy: %s", e)
        return False


def reset_memory_for_tests() -> None:
    _memory.__init__()  # type: ignore[misc]
