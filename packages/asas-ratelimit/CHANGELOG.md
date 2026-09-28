# Changelog — `asas-ratelimit`

Versions follow semver, and the git tag matches this file: `asas-ratelimit/v0.11.1`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure and the historical tag mapping: [`RELEASING.md`](../../RELEASING.md).

## 0.11.1 — 2026-09-28

A **pluggable bucket store**, so a host running several replicas can enforce
one limit instead of one per replica (N times looser). Nothing changes for a
host that does not opt in: the in-memory store stays the default, with the
same behavior, and no call site moves.

- **`configure(store=...)`** wires a store once at boot. Like `clock`, it is
  only touched when passed, so a kill-switch toggle never swaps a shared
  store back to memory; `store=None` restores the default. A value missing
  `take`/`clear` raises `TypeError` at boot.
- **`BucketStore`** is the protocol: `take(rule, key)` is the whole "refill,
  then spend one token" step as one atomic operation, plus `clear()`. The
  engine still decides unknown rules, the kill switch and the `limit=0` hard
  block before a store is asked, so those behave the same on every store.
- **`MemoryStore`** is the existing process-memory engine behind that
  protocol (same bucket math, same prune bound).
- **`RedisStore(client, prefix=..., fail_open=True)`**, behind the new
  `[redis]` extra (redis-py imported lazily). One Lua script does the refill
  and the spend server-side, so replicas cannot race a read-modify-write;
  time comes from Redis `TIME`, so replica clock skew cannot mint tokens;
  each bucket expires once it would have refilled to capacity; keys live
  under a non-empty `prefix` (default `asas:ratelimit:`), and `clear()`
  deletes only those.
- **Failure policy is the host's choice.** When Redis cannot answer,
  `fail_open=True` (the default) allows the request and logs one warning per
  outage, because a limiter must not take sign-in down with it;
  `fail_open=False` denies with a Retry-After of one token interval.
- `reset()`/`clear_counters()` clear the configured store; on a shared store
  that is every replica's counters (they remain test hooks).

## 0.11.0 — 2026-08-25

- Licensed under **Apache 2.0** (was proprietary/all-rights-reserved). `LICENSE` and `NOTICE` ship inside the wheel and the metadata carries `License-Expression: Apache-2.0` (Teamy TEAMY-797).
- Added `tests/test_host_contract.py`: `__all__` declared and resolving, contract names callable rather than shadowed by a submodule, module exports declared deliberately (Teamy TEAMY-798).

## Before 2026-08-25

Earlier releases were cut as **repo-wide** tags (`v0.1.0` … `v0.15.0`) under the
lockstep scheme in DR 0017, which decayed: from `v0.11.0` onward the repo tag no
longer matched any package's own version, so `asas-ratelimit @ v0.15.0` did not install
`asas-ratelimit` 0.15.0. `RELEASING.md` carries the full tag-to-version table for
decoding an old pin. Individual changes are in the git history.
