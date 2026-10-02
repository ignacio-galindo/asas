# Changelog — `asas-ratelimit`

Versions follow semver, and the git tag matches this file: `asas-ratelimit/v0.12.0`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure and the historical tag mapping: [`RELEASING.md`](../../RELEASING.md).

## 0.12.0 — 2026-09-29

- **Optional shared bucket** (`configure_shared_bucket`), so N replicas share one
  budget and a restart no longer refills every bucket. The host injects an async
  store; `allow_async` / `check_async` spend from it with the rule's own capacity
  and refill, and fall back to the in-process bucket whenever it answers None or
  raises, so an outage loosens the limit per instance and never opens it. The
  kill switch and a hard block never reach the store. Keys handed to the store
  are `<prefix>:<rule>:<sha256(key)>`, so it never holds an email or an IP.
- **`redis_shared_bucket(client)`** and `REDIS_TOKEN_BUCKET_LUA`: a ready
  adapter over a Redis the host already runs, one atomic script on the server's
  clock with a TTL on every key. Duck-typed on `eval`; no Redis import.
- **`client_address(request, proxy_hops)`**: the proxy-safe key, reading
  `X-Forwarded-For` from the end and never the first entry.
- The sync `allow` / `check` are unchanged. Upstreamed from a
  production platform, where the shared bucket runs across replicas.

## 0.11.0 — 2026-08-25

- Licensed under **Apache 2.0** (was proprietary/all-rights-reserved). `LICENSE` and `NOTICE` ship inside the wheel and the metadata carries `License-Expression: Apache-2.0` (Teamy TEAMY-797).
- Added `tests/test_host_contract.py`: `__all__` declared and resolving, contract names callable rather than shadowed by a submodule, module exports declared deliberately (Teamy TEAMY-798).

## Before 2026-08-25

Earlier releases were cut as **repo-wide** tags (`v0.1.0` … `v0.15.0`) under the
lockstep scheme in DR 0017, which decayed: from `v0.11.0` onward the repo tag no
longer matched any package's own version, so `asas-ratelimit @ v0.15.0` did not install
`asas-ratelimit` 0.15.0. `RELEASING.md` carries the full tag-to-version table for
decoding an old pin. Individual changes are in the git history.
