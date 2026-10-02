# Changelog — `asas-audit`

Versions follow semver, and the git tag matches this file: `asas-audit/v0.1.0`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure: [`RELEASING.md`](../../RELEASING.md).

## 0.1.0 — 2026-09-29

- First release, extracted from a production platform's audit module (its
  audit-row-commits-with-the-change contract) and generalised:
  - organisation and resource identifiers are strings, so UUID and int hosts
    use the same version;
  - SQLite as well as Postgres, with append-only triggers on both;
  - appends serialize by compare-and-set on a per-organisation head row
    instead of a Postgres advisory lock, which is portable and still never
    forks the chain under concurrency;
  - the head also records the chain's length, so deleting the newest events
    is detected (a plain hash chain cannot see a truncated tail);
  - time and payload are normalized before hashing, so a chain re-derived from
    stored columns agrees on every engine.
- Read-only routers: `GET /audit/events` (keyset paged) and `GET /audit/verify`.
- `migrate(engine)` uses the family's shared adopt-or-create runner.
