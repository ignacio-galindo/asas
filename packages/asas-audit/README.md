# asas-audit

**An unfalsifiable record of who did what.** Every business action becomes a
permanent entry carrying the fingerprint of the one before it, so the log is a
chain: editing an entry, deleting one, and inserting one after the fact are all
detectable, and the report names the row where the history stops adding up.

```python
import asas_audit as audit

# boot
audit.migrate(engine)
app.include_router(audit.build_router(get_session, tenant=current_tenant))

# inside the transaction that makes the change
audit.append(
    session,
    org_id=tenant_id, actor=user.subject,
    action="invoice.approved", resource_type="invoice", resource_id=invoice.id,
    payload={"amount": 1200, "approver_note": note},
)
# ...the caller commits. One transaction, both rows.

audit.history(session, org_id=tenant_id, resource_type="invoice", resource_id=id)
report = audit.verify(session, org_id=tenant_id)   # .is_intact, .first_break
```

Table-owning + router variant of the Asas host contract: `migrate` yes,
`build_router` yes, **no `seed`** (an audit log with seeded rows would be a record
of things that did not happen), and no write route (appending is something a
host's own service does inside its own transaction).

## The three guarantees

**1. The entry commits with the change it describes.** `append` puts the row on
the caller's own session and does not commit, so one transaction decides the fate
of both. There is no state in which the change happened and the record did not,
nor the reverse. A second session, or a helpful `commit()` inside `append`, would
break it into two fates, so both are tested against.

**2. Tampering is detectable, in all three of its forms.** An **edited** row no
longer hashes to its stored fingerprint. A **deleted** one leaves the survivors'
links not meeting. An **inserted** one has no valid link to what precedes it and
cannot be given one without the previous row's fingerprint. `verify` walks the
chain and reports each divergence with both hashes, so a reader can see it rather
than take the verdict on trust. Look at `first_break`: one alteration usually
produces one break, because the walk chains off what is *stored* rather than off
what it expected.

**3. Edits and deletes are refused by the database.** Not merely absent from this
package's code: a trigger rejects both for anyone, which is the layer that
survives a compromised application or somebody at a psql prompt. Postgres only,
stated rather than left to be inferred from a skipped test. `TRUNCATE` is
deliberately not covered (a row-level trigger cannot see it, and it needs table
ownership, so a guard there would only stop someone who already owns the table).

## Concurrency, which is the hard part

Read `service.py` before changing anything in the append path.

Appending means reading the newest entry for this tenant and chaining off it, so
two concurrent appends must not read the same tail. The instinct is
`SELECT ... FOR UPDATE` on the tail row, **and it does not work**: a waiter that
took the row lock before the winner's `INSERT` holds a lock on a row that is no
longer the tail, so it proceeds, chains off a stale fingerprint, and forks the
chain. Nothing fails at the time. It surfaces later as a verification break that
looks exactly like tampering over data nobody touched, and by then there is no way
to tell which branch is the real history.

What works is a lock that exists independently of any row, taken **before** the
tail is read: a per-tenant advisory lock held for the transaction. Per tenant,
because chains are per tenant and one tenant's write rate must not serialise
another's.

**Never hold that lock across slow work.** Every append in the tenant queues
behind it, so hashing a large payload, a network call, or a password KDF in the
same transaction turns a per-tenant lock into a per-tenant stall. Compute first,
append last.

`tests/test_concurrency.py` asserts the property with eight simultaneous writers,
and also demonstrates the failure: with the lock disabled, the same eight writers
fork the chain on every run.

SQLite needs none of this. It serialises writers at the database, which is the
same guarantee by a different mechanism, so the append path branches on the
dialect rather than requiring Postgres.

## Two things a host has to know

**Use `expire_on_commit=False` on your session factory.** The tenant pin is
transaction-local, deliberately, so a pooled connection cannot carry one request's
tenant into the next. But SQLAlchemy's default expires attributes on commit, so
reading any field of a row afterwards triggers a refresh, that refresh runs in a
new transaction with nothing pinned, the policy filters the row out, and you get
`ObjectDeletedError` about a row sitting right there in the table. Neither layer
is misbehaving; it is what two correct behaviours do together.
`test_reading_after_commit_needs_expire_on_commit_false` pins it so you meet it as
a test rather than as a mystery.

**Re-pin after a commit.** Same reason: a second query in the same request runs in
a new transaction, and an unpinned read returns nothing, which looks like missing
data rather than like a missing pin.

## Identity, and why it is strings

`org_id` and `resource_id` are `str`, not `int`, so a host that keys tenants by
UUID and one that keys them by integer can both adopt this without forking it. The
chain payload stringifies every field anyway, so a narrower column would buy
nothing and cost exactly that. There are no foreign keys to host tables, for the
same reason nothing else in the family has them, and `actor` is the host's own
principal string rather than an FK: the actor may be a system, and an account
that is later deleted must not take its history with it.

`seq` and `id` answer different questions. `seq` is the chain order, assigned by
the database, which is what `verify` walks. `id` is the event identity, a UUID
string, which is what goes in a URL and into the hash, so the fingerprint does not
depend on the sequence number the database happened to assign.

## Adopting an existing chain

A host that already keeps its own hash-chained audit table can hand it over
without rewriting a stored fingerprint, which the append-only trigger would
refuse anyway and which would prove nothing if it succeeded. Every row names the
**encoding** that produced its hash (`audit_event.encoding`), and `verify`
recomputes each row with its own. New rows always use `CURRENT_ENCODING`
(`"asas-audit/1"`, the bytes this package has always written). Your old rows use
an encoding you register:

```python
import asas_audit as audit

# at import time, before any verify
audit.register_encoding(audit.ChainEncoding("myapp/1", keys={"org_id": "tenant_id"}))
```

An encoding can rename keys of the hashed dict and choose how `occurred_at` is
spelled, and nothing else: the JSON rules and the SHA-256 link are shared by
every encoding, so a registered one cannot weaken what the chain proves. Keep the
default `timestamp` (`canonical_timestamp`) if your writer hashed aware UTC
values: it gives the same string as `isoformat()` for those, and unlike a bare
`isoformat()` it survives a driver that returns the moment naive (SQLite) or in
the server's zone (psycopg on a Postgres whose `TimeZone` is not UTC). Pass your
own module-level function only if your writer hashed another offset.

**What has to be true of the old rows**, or they will not verify:

- `hash_current = sha256(hash_prev || json.dumps(d, sort_keys=True,
  separators=(",", ":"), default=str))`, with `hash_prev` NULL on each tenant's
  first row, chained per tenant in ascending `seq`.
- `d` holds exactly the eight chain fields (`id`, `org_id`, `actor`, `action`,
  `resource_type`, `resource_id`, `payload`, `occurred_at`), possibly under other
  key names, with every id as `str(value)`. The stored tenant value must be the
  same string you pass to `verify`.
- The stored `payload` reads back to a dict that dumps to the same JSON as the one
  hashed. Values your writer stringified through `default=str` must have been
  stored as that string. JSONB can break this for a float such as `1e20`, which
  comes back as an integer; such a row fails your current verifier too.

**The procedure**, in your own migration chain:

1. Stop the legacy writer. Its advisory lock is not this package's, so the two
   writing at once fork the chain.
2. Rename and retype the table to this package's shape (below), and add
   `encoding varchar(32) NOT NULL DEFAULT '<your legacy name>'`. The default
   labels every existing row in the DDL itself, which neither the trigger nor row
   level security can interfere with, where an UPDATE would be refused by one and
   silently filtered by the other.
3. Re-create the tenant policy on the retyped column with
   `asas_tenancy.enable_rls("audit_event", column="org_id",
   column_type="character varying")`.
4. Call `audit.migrate(engine)`. It adopts the table (the baseline is stamped, not
   run), finds your `encoding` column and leaves it, default included: that
   default only ever applies to a row written by code that does not know the
   column exists, which on your table is the legacy writer.
5. Write through `audit.append` from then on. Its first row links to your last
   fingerprint like any other row.

| your column (typical) | this package | change |
| --- | --- | --- |
| `tenant_id uuid` + FK | `org_id varchar(64)` | rename; `USING tenant_id::text`; drop the FK and the policy first, both pin the type |
| `created_at timestamptz` | `occurred_at timestamptz` | rename |
| `id uuid` | `id varchar(64)`, unique | `USING id::text`; staying the primary key is fine |
| `resource_id uuid` | `resource_id varchar(64)` | `USING resource_id::text` |
| `seq bigint`, sequence default | `seq`, chain order | keep; it need not be the primary key, only increasing |
| `payload jsonb` | `payload` JSON | keep; JSONB reads and writes through the JSON type |
| `hash_prev`, `hash_current bytea` | same | keep |
| `action varchar(128)` | `action` | keep; wider than the package's 100 is harmless |
| (none) | `encoding varchar(32)` | add, defaulting to your legacy name |

Your own append-only trigger stays and keeps protecting the table, because the
baseline that would have created this package's is stamped rather than run. The
package's `ix_audit_event_actor` index is not created either; add it if you filter
history by actor. `tests/test_legacy_encoding.py` runs this recipe on Postgres
against a table built the way such a host builds it.

**What the verifier reports.** An adopted chain verifies as one history:
`events_checked` counts both kinds of row, and every `ChainBreak` carries the
`encoding` it was checked under (so does each event served by the router). A row
naming an encoding this process has not registered raises
`UnknownEncodingError` rather than reporting a break, because it is a missing
registration, not tampering. An encoding that does not reproduce your bytes shows
up as every legacy row reporting "edited" at once while this package's rows pass,
which is the signature of a wrong encoding rather than of an edit.

## Dependency on `asas-tenancy`

This package protects its own table through `asas_tenancy.enable_rls` inside its
own chain, so this table and the host's are covered by **one** definition of the
policy rather than two that can drift. It is the first inter-package dependency in
the family and a deliberate one: an audit log is the table where cross-tenant
visibility would be worst, since leaking it leaks the shape of another tenant's
whole operation rather than one record of it.

It is declared **by name** (`asas-tenancy>=0.1`), not by git URL. A URL would
hardcode the upstream remote, which a consumer mirroring this repo internally
rewrites, and would pin one tenancy tag per audit release. A name-only requirement
is satisfied by the consumer's own top-level pin, which is how every package here
is already installed, and `asas add audit` adds both.

## Running the tests

```bash
cd packages/asas-tenancy && pip install -e .        # the sibling, first
cd ../asas-audit && pip install -e '.[dev]' && pytest -q
```

SQLite by default. The trigger, the advisory lock and the isolation assertions
need Postgres, and the isolation ones need a role the policies actually apply to:

```bash
createdb -O asas_rls asas_audit_test
TEST_DATABASE_URL=postgresql+psycopg://asas_rls@localhost/asas_audit_test \
  ASAS_REQUIRE_RLS=1 pytest -q
```

A full run is `62 passed, 14 skipped` on SQLite and `76 passed` on Postgres as a
`NOBYPASSRLS` role.

See the repo README for the full host contract.
