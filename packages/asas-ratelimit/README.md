# asas-ratelimit

In-process rate limiting: a token-bucket engine over in-memory counters. The host
declares named `Rule`s at boot and calls `check(rule, key)` on the hot path — it
either passes or raises a FastAPI-native 429 with a `Retry-After` header.

No Redis and no DB writes of its own: a single-instance deployment gets exact
limits from process memory. A scaled-out one gets per-instance limits (N× looser)
UNLESS the host injects a shared bucket, which makes it one budget across every
replica and survives restarts:

```python
import redis.asyncio as redis
import asas_ratelimit as ratelimit

ratelimit.configure_shared_bucket(
    ratelimit.redis_shared_bucket(redis.from_url(settings.redis_url)),
    prefix=f"myapp:{settings.environment}:ratelimit:v1",
)

# async hot path: shared when the store answers, in-process when it does not
await ratelimit.check_async("auth.login.ip", ratelimit.client_address(request, proxy_hops=1))
```

The store never sees the caller's key (it gets a hash), never decides when the
kill switch is off or a rule is a hard block, and never fails a request: any
failure or `None` answer falls back to the in-process bucket. Any async
`(bucket_key, capacity, refill_per_second) -> (allowed, retry_after) | None`
works as a store; the Redis adapter is one atomic Lua script on the server's
clock.

Table-less **and** router-less variant of the Asas host contract: no session
dependency, no `seed`/`migrate`/`build_routers`. The host owns its rule catalog,
deployment-posture profiles, and any FastAPI dependency glue (per-user vs per-IP
keying); the library owns the bucket math:

```python
import asas_ratelimit as ratelimit

# boot (host wiring) — read *your* settings; the library reads no configuration
ratelimit.configure(enabled=settings.rate_limit_enabled)
ratelimit.declare(ratelimit.Rule(name="auth.login.ip", limit=30, window_seconds=3600))
for name, (count, window) in ratelimit.parse_overrides(settings.rate_limit_overrides).items():
    ratelimit.declare(ratelimit.Rule(name=name, limit=count, window_seconds=window))

# hot path
ratelimit.check("auth.login.ip", client_ip)      # raises 429 + Retry-After when spent
allowed, retry_after = ratelimit.allow("auth.login.ip", client_ip)  # non-raising form
```

Unknown rule names and disabled mode always allow — a typo'd name must never lock
an endpoint (assert your names at boot). `configure(clock=...)` injects a fake
clock for tests; `reset()`/`clear_counters()` give per-test isolation.
`parse_overrides("rule=count/window,…")` parses the per-deployment override
string, logging and skipping malformed entries.

See the repo README for the full contract. Extracted from Teamy (anti-abuse
TEAMY-334, extraction epic TEAMY-466 / design record 0017).
