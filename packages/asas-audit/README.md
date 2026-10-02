# asas-audit

A tamper-evident, append-only audit log that lives in your application's own
database. Each event is hash-chained to the previous one in its organisation,
so editing, deleting or inserting a row behind the application's back shows up
on verify, at exactly that row.

```python
import asas_audit

# boot
asas_audit.migrate(engine)
app.include_router(*asas_audit.build_routers(get_session))  # behind your own auth

# anywhere a business change happens, in the SAME transaction
asas_audit.append(session, action="invoice.approved", resource_type="invoice",
                  resource_id=invoice.id, actor=f"user:{user.id}",
                  payload={"amount": str(invoice.amount)}, org_id=org.id)
session.commit()

asas_audit.verify(session, org_id=org.id).as_sentence()
# "The audit log for organisation '12' is intact across 1834 events."
```

## What it gets right, so you do not have to

- **The audit row commits with the change.** `append` flushes inside your
  transaction and never commits, so a rolled-back change leaves no history and
  a committed one always does.
- **Concurrency never forks the chain.** Locking the newest row with
  `SELECT ... FOR UPDATE` looks right and is not: a waiter woken after the
  holder commits still reads the old tail and chains onto the same parent,
  which verify later reports as tampering. It also has nothing to lock when the
  chain is empty. Appends here move a per-organisation head row by
  compare-and-set, which re-evaluates against the committed version on
  Postgres and is serialized by the database lock on SQLite.
- **A truncated tail is caught.** Deleting the newest events leaves a shorter
  chain that is internally consistent. The head records how long the chain is
  and where it ends, and verify checks it.
- **Append-only is enforced by the database.** Triggers refuse `UPDATE` and
  `DELETE` on the event table on both engines, and `TRUNCATE` on Postgres, so
  raw SQL cannot quietly rewrite history either.
- **The hash re-derives on every engine.** Time is hashed as naive UTC with
  microseconds, which is what SQLite and Postgres both hand back, and the
  payload is stored exactly as it was hashed. A naive port of the pattern gets
  false breaks from both.
- **Verify is safe while appends run.** It reads the head first and then only
  the events that head covers.

## Host contract

- `migrate(engine)`: package Alembic chain (`alembic_version_asas_audit`),
  with the family's shared adopt-or-create runner: a database that already
  holds exactly these tables is stamped, and an unrelated table of the same
  name is refused. An existing audit table of your own is NOT adoptable; its
  rows were hashed under another encoding and would verify as broken.
- `build_routers(get_session)`: read-only `GET /audit/events` (newest first,
  keyset paged by `before_seq`) and `GET /audit/verify`. There is no write
  route on purpose.
- `configure_org_resolver(fn)`: `(session) -> org id or None`. Unset, there is
  one platform chain.
- `append`, `verify`, `list_events`: take an explicit `Session`.

Organisation and resource ids are stored as strings, so pass ints, UUIDs or
strings. An async host calls through its session's sync bridge:

```python
await async_session.run_sync(lambda s: asas_audit.append(s, ...))
```

Extracted from a production platform's audit module and generalised.
