# asas-ratelimit

Rate limiting: a token-bucket engine over in-memory counters by default. The host
declares named `Rule`s at boot and calls `check(rule, key)` on the hot path — it
either passes or raises a FastAPI-native 429 with a `Retry-After` header.

No DB writes, and no Redis unless you ask for it: a single-instance deployment
gets exact limits from process memory. A scaled-out one gets per-instance limits
(N times looser) until it wires a shared store; see [Scaling out](#scaling-out).

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

## Scaling out

Behind a load balancer each replica keeps its own buckets, so a 5-per-hour
sign-in limit on four replicas lets an attacker try twenty times. Point every
replica at one Redis and they share one set of buckets:

```python
import redis
import asas_ratelimit as ratelimit

# pip install 'asas-ratelimit[redis]'
client = redis.Redis.from_url(settings.redis_url, socket_timeout=0.25, socket_connect_timeout=0.25)
ratelimit.configure(enabled=settings.rate_limit_enabled, store=ratelimit.RedisStore(client))
```

That is the whole change: rules, `check` and `allow` stay as they are.

- **Atomic across processes.** The refill and the spend run as one Lua script
  on the server, so two replicas can never both take the last token.
- **One clock.** Time comes from Redis `TIME`, not the replica, so a replica
  whose clock runs ahead cannot refill buckets early. `configure(clock=...)`
  only drives the in-memory store.
- **Idle buckets expire.** Each key's TTL is the time it needs to refill to
  capacity, the point where a missing bucket (counted as full) is the same
  thing. An attacker cycling keys costs memory for one full refill at most.
- **Namespaced.** Keys live under `prefix` (default `asas:ratelimit:`), so
  several apps or environments can share a server; give each its own prefix.
  `clear_counters()`/`reset()` delete only keys under it, and on a shared
  store that means every replica's counters.
- **Unknown rules, the kill switch and `limit=0` hard blocks** are decided
  before the store is asked, so they behave the same on every store, Redis
  outage included.

**When Redis is down.** `RedisStore(client, fail_open=True)` is the default:
requests are allowed and one warning is logged per outage (plus a note when
it recovers). A rate limiter is a guard on sign-in, not part of it; failing
closed would turn a cache outage into a sign-in outage for every user, which
is a worse result than a few minutes of unthrottled attempts. A host that
would rather lock the endpoint passes `fail_open=False`: requests get a 429
with a Retry-After of one token interval until Redis answers again. Either
way, set a short `socket_timeout` on the client: the limiter sits on the
request path, and a Redis that hangs instead of refusing would otherwise hang
the request.

Another backend is a class with two methods, `take(rule, key)` (refill and
spend one token atomically, returning `(allowed, retry_after_seconds)`) and
`clear()`; see `BucketStore`.

The Redis tests run when a server answers at `TEST_REDIS_URL` (default
`redis://localhost:6379/0`) and skip otherwise; they write under a per-run key
prefix and delete it afterwards.

See the repo README for the full contract. Extracted from Teamy (anti-abuse
TEAMY-334, extraction epic TEAMY-466 / design record 0017).
