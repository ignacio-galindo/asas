"""Redis bucket store: one set of buckets shared by every replica.

Needs the ``[redis]`` extra (redis-py), imported lazily so the in-memory
default never needs the dependency at import time. The host owns the client
(URL, TLS, pool, timeouts) and passes it in; the store owns the bucket math,
which runs as a single server-side Lua script so the refill and the spend are
one atomic step no matter how many processes call it.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Tuple

if TYPE_CHECKING:
    from . import Rule

log = logging.getLogger(__name__)

# KEYS[1] = bucket key. ARGV = capacity, refill per second.
# The clock is the server's TIME, never the caller's: replicas with skewed
# clocks share one timeline, so a replica running ahead cannot mint tokens.
# A negative elapsed (failover to a server whose clock is behind) refills
# nothing. The key expires once it would have refilled to capacity, which is
# exactly when a missing bucket (counted as full) becomes equivalent to it.
_TAKE_LUA = """
if redis.replicate_commands then pcall(redis.replicate_commands) end
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'stamp')
local tokens = tonumber(state[1])
local stamp = tonumber(state[2])
if tokens == nil or stamp == nil then
  tokens = capacity
  stamp = now
end
local elapsed = now - stamp
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', string.format('%.17g', tokens),
           'stamp', string.format('%.6f', now))
local ttl_ms = math.ceil((capacity - tokens) / rate * 1000)
if ttl_ms < 1 then ttl_ms = 1 end
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return {allowed, string.format('%.17g', retry)}
"""

_GLOB_SPECIAL = "\\*?[]"


class RedisStore:
    """Buckets in Redis, shared by every process that uses the same server
    and ``prefix``.

    ``client`` is a ``redis.Redis`` the host builds. Give it a short
    ``socket_timeout``: the limiter sits on the request path, and a hung
    Redis must fail fast into the failure policy rather than hang sign-in.

    ``prefix`` namespaces every key (``<prefix><len>:<rule>:<key>``, the
    length making rule names that contain ``:`` unambiguous); ``clear()``
    deletes only keys under it. It must be non-empty, so ``clear()`` can
    never mean the whole database.

    ``fail_open`` is the policy when Redis cannot answer (unreachable,
    timeout, any Redis error). ``True``, the default, allows the request and
    logs a warning once per outage: a rate limiter is a guard, and a guard
    that takes sign-in down with it turns a cache outage into a full outage.
    ``False`` denies instead, with a Retry-After of one token interval, for
    hosts that would rather lock an endpoint than leave it unguarded.
    """

    def __init__(self, client: Any, *, prefix: str = "asas:ratelimit:", fail_open: bool = True) -> None:
        try:
            from redis.exceptions import RedisError  # deferred: only this store needs redis-py
        except ImportError as exc:  # pragma: no cover (only without the extra)
            raise ImportError(
                "RedisStore needs the [redis] extra: pip install 'asas-ratelimit[redis]'"
            ) from exc
        if not prefix:
            raise ValueError("RedisStore prefix must be non-empty")
        self._client = client
        self._script = client.register_script(_TAKE_LUA)
        self._errors = RedisError
        self.prefix = prefix
        self.fail_open = fail_open
        self._down = False
        self._down_lock = threading.Lock()

    def key(self, rule_name: str, key: str) -> str:
        """The Redis key holding ``(rule_name, key)``'s bucket."""
        return f"{self.prefix}{len(rule_name)}:{rule_name}:{key}"

    def take(self, rule: "Rule", key: str) -> Tuple[bool, float]:
        try:
            allowed, retry = self._script(
                keys=[self.key(rule.name, key)],
                args=[rule.capacity, repr(float(rule.refill_per_second))],
            )
        except self._errors as exc:
            return self._unavailable(rule, exc)
        self._recovered()
        return bool(int(allowed)), float(retry)

    def clear(self) -> None:
        """Delete every bucket under ``prefix``. Raises if Redis is down: this
        is a test hook, and a silently skipped cleanup leaks state."""
        pattern = "".join("\\" + c if c in _GLOB_SPECIAL else c for c in self.prefix) + "*"
        batch = []
        for k in self._client.scan_iter(match=pattern, count=500):
            batch.append(k)
            if len(batch) >= 500:
                self._client.delete(*batch)
                batch.clear()
        if batch:
            self._client.delete(*batch)

    def _unavailable(self, rule: "Rule", exc: Exception) -> Tuple[bool, float]:
        with self._down_lock:
            first, self._down = not self._down, True
        if first:
            log.warning(
                "rate-limit store unreachable (%s: %s); failing %s until it recovers",
                type(exc).__name__, exc, "open (allowing)" if self.fail_open else "closed (denying)",
            )
        if self.fail_open:
            return True, 0.0
        return False, 1.0 / rule.refill_per_second

    def _recovered(self) -> None:
        if self._down:
            with self._down_lock:
                was_down, self._down = self._down, False
            if was_down:
                log.info("rate-limit store reachable again; limits enforced")
