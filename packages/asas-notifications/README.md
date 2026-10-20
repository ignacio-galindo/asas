# asas-notifications

A generic notification engine (DR 0003): producers call `notify` inside their
own transaction — the insert IS the enqueue, so a notification exists iff the
domain change committed — passing the **application action** that caused the
emit (`action="job.publish"`, a reference declared nowhere) and the two
classification **axes** that decide a channel (topic × importance). Behavior
attaches to the axes, never to individual actions; the DB stores only
deviations. DR 0003 had four: `reason` (why this recipient) went in 0.17.0 with
the preference layer it was reserved for, and `nature` (what it asks of you)
went in 0.18.0 because it was presentation and the host renders its own feed.
The in-app feed is the `notification` row itself (`build_router` serves it);
external channels go through the `notification_delivery` outbox and registered
**channel adapters** (email, chat, …).

Engine rules baked in (from Teamy's WXL-209 epic):

- **Actor exclusion** — you are never notified of your own action.
- **Visibility filtering** — recipients pass through the host's registered filter,
  so a notification never leaks a private record.
- **Data-driven routing** — per channel, most specific wins: topic policy row →
  axis policy row → the built-in fallback (`low` is in-app only — ambient
  activity never emails; `high` adds email). Empty policy tables
  reproduce the fallback exactly. Unread ambient rows can **coalesce** per
  (org, recipient, action, entity) so an edit burst stays one bell entry.
- **Duplicate-safe dispatch** — each outbox row is claimed with a rows-affected
  CAS before the adapter send; overlapping passes (hook vs job vs second
  instance) lose the CAS and skip. Stale claims from crashed passes reclaim.
  At-least-once overall; failures retry to an attempt cap, `SkipDelivery` marks
  a graceful no-retry skip.

Table-owning + router variant of the Asas host contract (5 tables; org/user
refs are opaque strings — no host FKs):

- **`migrate(engine)`** — package Alembic chain
  (`alembic_version_asas_notifications`, adopt-or-create).
- **`build_router(get_session)`** — the `/me/notifications` feed API; the host
  applies auth at include time.
- **`configure_context_resolver(fn)`** — `(session) -> (user_id, org_id) | None`;
  must return `None` cheaply outside a request (it is consulted on read paths
  too). When it supplies an org, the package scopes every feed/read/archive
  query to it — defense in depth on top of the host's own tenancy layer; a
  cross-org id probe 404s like a missing row.
- **`configure_recipient_filter(fn)`** — `(session, user_ids, entity_type,
  entity_id, record) -> user_ids` allowed to know the subject exists. Runs for
  **every** `notify` that names an `entity_type`. `record` is the subject row
  when the producer had it and `None` when it did not — a generic producer may
  hold only the type and the id — so the filter gets both and decides: use the
  row, resolve it from the id, or return `user_ids` unchanged for an entity
  type that needs no gating.
- **`register_adapter`** — channel adapters are the host's;
  `dispatch_pending(engine)` is one outbox pass and the host owns the cadences
  (after-commit hook, boot sweep, periodic job). (`register_kind` survives 0.16
  as a deprecated shim — one release, DR 0003 I-3.)

```python
import asas_notifications as notifications
from asas_notifications import NotificationTopic
from sqlmodel import Session, select

# boot (host wiring)
notifications.migrate(engine)
notifications.configure_context_resolver(current_user_org)
notifications.configure_recipient_filter(visible_recipients)
notifications.register_adapter("email", MyEmailAdapter())
with Session(engine) as s:  # seed your topics once — emits into an unseeded
    if not s.exec(select(NotificationTopic)              # topic fail loud
                  .where(NotificationTopic.key == "approvals")).first():
        s.add(NotificationTopic(key="approvals", name="Approvals"))
        s.commit()
app.include_router(notifications.build_router(get_session),
                   dependencies=[Depends(require_user)])

# producers (inside their own transaction)
notifications.notify(session, recipient_user_ids, "workflow.request_approval",
                     topic="approvals", importance="high",
                     title="Budget change",
                     record=project, entity_type="project",
                     entity_id=project.id, actor_user_id=actor.id)

# dispatch cadence of your choosing
notifications.dispatch_pending(engine)
```

## Async hosts, a schema of your own, and row level security (0.20.0)

**An `AsyncSession` host** imports the async twins (DR 0005). Every function is
the sync one, run on the caller's connection and inside the caller's
transaction through SQLAlchemy's greenlet bridge: a rollback takes the
notification and its outbox rows with it, nothing blocks the loop, and there is
no second engine or driver. The bridge flushes before it returns and hands back
frozen `NotificationRecord`s, so the two traps of a hand-written bridge (lost
deliveries, detached rows) are closed in the package. Install
`asas-notifications[async]`.

```python
from asas_notifications import aio as notifications

await notifications.migrate(async_engine)          # its own connection, no sync engine
rows = await notifications.notify(session, [user_id], "order.approve",
                                  topic="orders", importance="high",
                                  title="Order approved", org_id=tenant)
await session.commit()                             # the host's commit, as ever
await notifications.dispatch_pending(async_engine, bind=pin_tenant)
```

Seams (`configure_context_resolver` and the rest) stay sync callables and are
handed `session.sync_session`, whose `info` is the async session's own.

**A named Postgres schema** for a host that keeps each module in its own:

```python
notifications.configure_schema("notifications")   # every process, at startup
notifications.migrate(engine)                      # tables + version table in it
```

The package's tables are qualified, so every statement names the schema on any
connection; `migrate` creates the schema when missing and checks for adoption
there and only there. Postgres only; leave it unset on SQLite.

**Row level security** on the tenant tables (`notification`,
`notification_delivery`), written by `asas_tenancy.enable_rls` keyed on
`org_id` as text against your own session variable. Needs `asas-tenancy`
(`asas-notifications[tenancy]`); a no-op off Postgres; takes no lock when the
policies are already in place:

```python
notifications.migrate(engine, tenant_guc="procure.tenant_id")

pin = lambda conn: asas_tenancy.set_tenant_guc(conn, tenant, guc="procure.tenant_id")
notifications.dispatch_pending(engine, bind=pin)   # or aio.dispatch_pending(async_engine, bind=pin)
```

`bind` runs on every transaction a dispatch pass opens. A pass over a protected
outbox with no `bind` (and no `tenant_pinned=True` for an engine whose
connections already carry the tenant) raises instead of reading an empty queue
and reporting success; a role that bypasses row level security dispatches
across tenants as before. The configuration tables stay readable to every
tenant (their platform rows are shared); see `placement.TENANT_TABLES` for why.

## Feed state: two independent axes

A notification carries **`read_at`** (seen) and **`archived_at`** (dealt with),
and they never imply each other. Reading does not archive, archiving does not
mark read, and un-archiving does not make a row unread again. That separation is
the point: a host showing actionable notifications needs a row to survive being
read and to leave only when the recipient acts on it or files it away — an
"unread means outstanding" model empties itself as the recipient browses.

`GET /me/notifications` filters compose freely:

| Param | Values | Default |
| --- | --- | --- |
| `state` | `open` (un-archived) · `archived` · `all` | `open` |
| `unread_only` | bool | `false` |

There is no presentation filter: `nature` left the package in 0.18.0, and a host
that wants one keeps it on a row of its own. `?state=open&unread_only=true` is
"still needs me", `?state=archived` is the history, and `?unread_only=true` is
the classic feed. `total` reflects the filters; **`unread_count` never does** — it is unread-and-un-archived on every
response, so a badge fed from any list call agrees with every other.

Writes: `POST /{id}/read`, `/read-all`, `/{id}/archive`, `/{id}/unarchive`, and
`/archive-read` (bulk-files the read rows, never an unread one). `resolved_at`
exists on the row but is deliberately unwritten — Teamy weighed auto-clearing
action rows from engine events and chose the archive gesture instead.

See the repo README for the full contract. Extracted from Teamy (notifications
epic WXL-209/WXL-222 + TEAMY-475 dispatch hardening; extraction epic TEAMY-466 /
design record 0017; archive axis + inbox filters TEAMY-693).
