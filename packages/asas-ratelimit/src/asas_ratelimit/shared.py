"""One budget across every replica, when the host offers a shared store.

The in-process engine is exact for one instance and N times too loose for N,
and every restart refills every bucket. For a guessing limit (sign-in,
password reset, OTP) that is the whole point lost. This module lets the host
inject a SHARED bucket, keeps the package free of any store dependency, and
keeps the in-process engine as the answer whenever the shared one cannot give
one:

- :func:`configure_shared_bucket` takes ``async (bucket_key, capacity,
  refill_per_second) -> (allowed, retry_after) | None``. None means "no answer"
  (store down, timing out, not configured) and the in-process bucket decides,
  so an outage loosens the limit per instance and never opens it or refuses
  everyone.
- :func:`allow_async` / :func:`check_async` are the async hot path. The rule's
  own capacity and refill are what the store is asked to enforce, so the
  shared and local answers use one arithmetic.
- The bucket key handed to the store is ``<prefix>:<rule>:<sha256(key)>``. The
  caller's key is usually an email address or a client IP; the store never
  sees either.
- The kill switch and a hard block (``limit=0``) never reach the store.

:data:`REDIS_TOKEN_BUCKET_LUA` and :func:`redis_shared_bucket` are a ready
implementation for a Redis the host already runs: one atomic script on the
SERVER's clock (a replica's own monotonic clock means nothing to another), the
same refill arithmetic as the in-process engine, and a TTL on every key so an
instance running ``noeviction`` stays clean. It is duck-typed on the client's
``eval``; this package imports no Redis library.

:func:`client_address` is the keying half people get wrong behind a proxy.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Awaitable, Callable, Optional, Tuple

from fastapi import HTTPException

log = logging.getLogger(__name__)

SharedBucket = Callable[[str, int, float], Awaitable[Optional[Tuple[bool, float]]]]

_shared: Optional[SharedBucket] = None
_prefix = "asas-ratelimit:v1"


def configure_shared_bucket(fn: Optional[SharedBucket], *, prefix: str = "asas-ratelimit:v1") -> None:
    """Inject (or with None, remove) the shared bucket. ``prefix`` scopes the
    keys, e.g. by environment, so one store serving two deployments never lets
    one spend the other's budget."""
    global _shared, _prefix
    _shared = fn
    _prefix = prefix


def bucket_key(rule_name: str, key: str) -> str:
    """The key the shared store sees: scoped, and a hash of the caller's key."""
    return f"{_prefix}:{rule_name}:{hashlib.sha256(key.encode()).hexdigest()[:32]}"


async def allow_async(rule_name: str, key: str) -> Tuple[bool, float]:
    """``(allowed, retry_after_seconds)`` from the shared bucket when one is
    configured and answers, from the in-process bucket otherwise."""
    from . import _enabled_now, allow, rules

    rule = rules().get(rule_name)
    if _shared is None or not _enabled_now() or rule is None or rule.limit <= 0 or rule.capacity <= 0:
        return allow(rule_name, key)
    try:
        answer = await _shared(bucket_key(rule_name, key), rule.capacity, rule.refill_per_second)
    except Exception:  # noqa: BLE001 - the in-process bucket still decides
        log.warning("shared rate-limit bucket failed; using the in-process bucket", exc_info=True)
        answer = None
    if answer is None:
        return allow(rule_name, key)
    allowed, retry_after = answer
    return bool(allowed), float(retry_after)


async def check_async(rule_name: str, key: str) -> None:
    """The async form of :func:`check`: spend one token or raise 429 with
    ``Retry-After``."""
    allowed, retry_after = await allow_async(rule_name, key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — try again later.",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def client_address(request: Any, proxy_hops: int = 0) -> str:
    """The caller's address for keying: the ``X-Forwarded-For`` entry
    ``proxy_hops`` from the END (the one your trusted proxy appended), or the
    socket peer when there is no such entry.

    Never the FIRST entry, which is whatever the caller chose to send. And
    behind an ingress the socket peer is the ingress itself, so ``proxy_hops=0``
    there puts every user in one bucket: set it to the number of proxies you
    run.
    """
    if proxy_hops > 0:
        forwarded = [
            part.strip()
            for part in request.headers.get("x-forwarded-for", "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= proxy_hops:
            return forwarded[-proxy_hops]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


#: One token bucket, spent atomically on the server's clock. Numbers come back
#: as strings because Lua numbers are truncated to integers on the way out.
REDIS_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local held = redis.call('HMGET', KEYS[1], 'tokens', 'stamp')
local tokens = tonumber(held[1])
local stamp = tonumber(held[2])
if tokens == nil or stamp == nil then
  tokens = capacity
  stamp = now
end
tokens = math.min(capacity, tokens + math.max(0, now - stamp) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'stamp', tostring(now))
redis.call('EXPIRE', KEYS[1], ttl)
return {allowed, tostring(retry)}
"""


def redis_shared_bucket(client: Any) -> SharedBucket:
    """A :data:`SharedBucket` over an async Redis client the host already has
    (``redis.asyncio.Redis`` or anything with the same ``eval``). A failing
    call answers None, so the in-process bucket decides; the host's client
    owns timeouts and connection pooling."""

    async def spend(key: str, capacity: int, refill_per_second: float) -> Optional[Tuple[bool, float]]:
        ttl = max(1, int(capacity / refill_per_second) + 1)  # an absent bucket IS a full one
        try:
            allowed, retry = await client.eval(
                REDIS_TOKEN_BUCKET_LUA, 1, key, capacity, repr(float(refill_per_second)), ttl
            )
        except Exception:  # noqa: BLE001
            log.warning("redis rate-limit bucket failed; using the in-process bucket", exc_info=True)
            return None
        return bool(int(allowed)), float(retry)

    return spend
