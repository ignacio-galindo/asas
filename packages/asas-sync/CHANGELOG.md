# Changelog — `asas-sync`

Versions follow semver, and the git tag matches this file: `asas-sync/v0.1.0`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure: [`RELEASING.md`](../../RELEASING.md).

## 0.1.0 — 2026-09-30

- First release, extracted from the ad-recruiter platform's Oracle Fusion thin
  index and made independent of Oracle: a `RemoteCollection` protocol, a
  `SyncSpec`, and two package tables (`asas_sync_cursor`, `asas_sync_seen`).
- `run_pass`: key-ordered and stamp-ordered walks, the offset-ceiling
  re-anchor, stuck detection, a watermark capped by the walk's start and the
  greatest stamp read, per-page commits, resumable capped passes.
- `reconcile`: two-miss deletion, nothing marked by an incomplete walk,
  verify-by-key for inexact walks, a `purpose="reconcile"` hint so the host
  can fetch only the key and stamp.
- A compare-and-set lease per collection and organisation.
