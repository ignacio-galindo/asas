# Changelog — `asas-audit`

Versions follow semver, and the git tag matches this file: `asas-audit/v0.1.1`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure and the historical tag mapping: [`RELEASING.md`](../../RELEASING.md).

## 0.1.1 (unreleased)

Additive: existing chains verify exactly as before, and a host that runs
`migrate()` at boot has nothing else to do.

- **Chain encodings: a host can hand over a hash chain it already keeps.** Every
  row now names the canonical encoding that produced its hash, in a new
  `audit_event.encoding` column, and `verify` recomputes each row with its own.
  New rows always use `CURRENT_ENCODING` (`"asas-audit/1"`), which is byte for
  byte what 0.1.0 wrote. A host whose older chain hashed slightly different bytes
  registers a `ChainEncoding` that reproduces them (key renames and a timestamp
  formatter, nothing else) with `register_encoding`, and its history keeps
  verifying with this package's rows appended after it. The README's "Adopting
  an existing chain" section is the procedure, including the column mapping.
- **Migration `0002`** adds `encoding` with the current name as its column
  default, so every existing row is labelled by the DDL itself. Not an UPDATE:
  the append-only trigger would refuse one, and under forced row-level security
  with no tenant pinned it would match nothing and succeed silently. If the
  column already exists (a host that added it with its legacy name as the
  default, which is the adoption procedure) the revision leaves it alone.
- A row naming an encoding nobody registered raises **`UnknownEncodingError`**
  from `verify` instead of reporting a break: it is a missing registration, not
  tampering, and a whole adopted history reporting as rewritten is the false
  alarm that gets a verifier switched off.
- `ChainBreak.encoding`, and `encoding` on each event and each break the router
  serves, so a reader checking the chain themselves knows which bytes to rebuild.
- `canonical_timestamp` is now exported: it is the formatter an adopting host
  normally keeps, because it matches `isoformat()` for aware UTC values and
  survives a driver that returns the moment naive or in the server's zone.

## 0.1.0 — unreleased

First release. Extracted from a host application's working implementation, so the
shapes below survived production rather than being a first guess at them.

- **`append(session, ...)`** records one business action on the caller's own
  session and does not commit, so the entry and the change it describes share one
  transaction and one fate. Two appends in one unit of work chain to each other
  (the tail read is flushed first) rather than both chaining to the row that
  preceded them.
- **The hash chain** (`chain.py`, pure and DB-free): `canonical_bytes`,
  `chain_payload`, `compute_hash`, `verify_rows`. Writer and verifier share one
  encoding and live in one file, because two encodings is how a chain reports
  breaks that are not there. `verify` detects edits, deletions, insertions and
  reordering, and reports each with both hashes plus a plain-words reason.
- **`canonical_timestamp`**, found by the dual-engine rule: Postgres returns an
  aware datetime and SQLite a naive one, so the same row hashed one way on write
  and another on verify, and every chain read back on SQLite reported as tampered
  with. A naive value is read as UTC, the only choice that does not make a row
  hash differently on two machines.
- **The per-tenant advisory lock, taken before the tail read.** `FOR UPDATE` on
  the tail row does not prevent the race: a waiter holding it from before the
  winner's insert chains off a stale fingerprint and forks the chain, silently.
  Asserted with eight simultaneous writers, and the failure is demonstrated too:
  with the lock disabled the same writers fork the chain on every run.
- **Append-only enforced by the database**: a trigger rejects UPDATE and DELETE
  for anyone, which is the layer that survives a compromised application.
  Postgres only; `TRUNCATE` is deliberately out of scope and says why.
- **Row-level security through `asas-tenancy`**, so this table and the host's own
  are protected by one definition of the policy. First inter-package dependency in
  the family, declared by name rather than by git URL.
- **`history(...)`** newest first, filtered by resource, actor, action and date
  range. A `resource_id` without its `resource_type` is refused: an id is unique
  only within a type.
- **`build_router`** serves the history and the verification report, with the
  fingerprints as hex so a reader can check the chain themselves. Auth is
  composition-time, the tenant is never a request parameter, and there is no write
  route.
- Identity is opaque strings throughout, so a UUID-keyed host and an
  integer-keyed one can both adopt this without a fork. `seq` uses
  `BigInteger().with_variant(Integer, "sqlite")`, because SQLite auto-increments
  only a plain `INTEGER PRIMARY KEY` and a bigint one fails its NOT NULL on every
  insert.
- Documents the `expire_on_commit=False` requirement: with the default, reading a
  row after commit refreshes it in a new transaction with no tenant pinned, and
  the policy makes the row vanish. Pinned as a test.
- Licensed **Apache 2.0**, with `LICENSE` and `NOTICE` in the wheel.
- `tests/test_host_contract.py` per TEAMY-798, plus a test that this package
  exposes no `seed` and no write route.
