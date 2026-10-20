"""The async twins (DR 0005): one implementation, served on the caller's
``AsyncSession`` connection and transaction.

Runs on both legs of the matrix through the async driver of the same database
(aiosqlite, asyncpg; see ``conftest.async_url``). What each test pins is one of
the DR's requirements, named in its docstring, and in particular the two traps
every hand-written bridge fell into: deliveries lost to a missing flush, and ORM
rows read after the bridge returned.
"""

import inspect
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

import asas_notifications as notifications
from asas_notifications import aio, channels, service
from asas_notifications.models import (
    DeliveryStatus,
    Notification,
    NotificationDelivery,
)

from conftest import on_async_engine


def _emit_kwargs(**kw):
    kw.setdefault("topic", "general")
    kw.setdefault("importance", "high")
    kw.setdefault("title", "Hello")
    return kw


# ── R6: nothing to forget ────────────────────────────────────────────────────


def _session_first_functions():
    for name, fn in vars(service).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        if fn.__module__ != service.__name__:
            continue
        params = list(inspect.signature(fn).parameters.values())
        if params and params[0].annotation is Session:
            yield name


def test_every_public_session_function_has_an_async_twin():
    """R6: a public function that takes a ``Session`` and has no coroutine of the
    same name in ``aio`` fails here, not in an async host in production."""
    names = sorted(_session_first_functions())
    assert "notify" in names and "list_feed" in names  # the scan is finding them
    missing = [
        n for n in names if not inspect.iscoroutinefunction(getattr(aio, n, None))
    ]
    assert not missing, f"asas_notifications.aio has no async twin for {missing}"


@pytest.mark.parametrize("name", ["dispatch_pending", "migrate", "has_pending"])
def test_the_engine_and_connection_entry_points_have_twins(name):
    assert inspect.iscoroutinefunction(getattr(aio, name))


def test_the_record_carries_every_notification_column():
    """R4's read model and the table cannot drift: a column added to
    ``notification`` without a field on the record fails here."""
    columns = [c.name for c in Notification.__table__.columns]
    assert list(aio.NotificationRecord.__dataclass_fields__) == columns


# ── R2 and R5: the caller's transaction, flushed ─────────────────────────────


def test_notify_commits_with_the_host_and_flushes_its_deliveries(migrated):
    """R2 and R5. The outbox rows are on the host's connection before the host
    flushes anything (a connection-level read never autoflushes), and they
    commit with the host's own commit."""

    async def body(engine):
        async with AsyncSession(engine) as session:
            rows = await aio.notify(session, ["7"], "act.commit", **_emit_kwargs())
            conn = await session.connection()
            pending = (
                await conn.execute(
                    sa.select(sa.func.count()).select_from(NotificationDelivery.__table__)
                )
            ).scalar()
            await session.commit()
        return rows, pending

    rows, pending = on_async_engine(migrated, body)
    assert pending == 1
    with Session(migrated) as s:
        stored = s.exec(select(Notification)).one()
        delivery = s.exec(select(NotificationDelivery)).one()
    assert (stored.id, stored.user_id, stored.org_id) == (rows[0].id, "7", "1")
    assert (delivery.notification_id, delivery.org_id, delivery.channel) == (
        stored.id,
        "1",
        "email",
    )


def test_notify_rolls_back_with_the_host(migrated):
    """R2: the host's rollback takes the package's writes with it, deliveries
    included, exactly as for a sync host."""

    async def body(engine):
        async with AsyncSession(engine) as session:
            await aio.notify(session, ["7"], "act.rollback", **_emit_kwargs())
            await session.rollback()

    on_async_engine(migrated, body)
    with Session(migrated) as s:
        assert s.exec(select(Notification)).all() == []
        assert s.exec(select(NotificationDelivery)).all() == []


def test_records_are_safe_after_the_session_is_gone(migrated):
    """R4: the session commits (expiring every ORM row it holds) and closes; the
    records came out of the bridge as plain values and still read."""

    async def body(engine):
        async with AsyncSession(engine) as session:  # expire_on_commit=True
            rows = await aio.notify(
                session, ["7", "8"], "act.records", **_emit_kwargs(body="b")
            )
            await session.commit()
        return rows

    rows = on_async_engine(migrated, body)
    assert [(r.user_id, r.title, r.body, r.importance) for r in rows] == [
        ("7", "Hello", "b", "high"),
        ("8", "Hello", "b", "high"),
    ]
    with pytest.raises(AttributeError):
        rows[0].title = "changed"  # frozen


def test_the_feed_through_the_twins(migrated):
    """The read and write twins round-trip on one session: page, badge, read."""

    async def body(engine):
        async with AsyncSession(engine) as session:
            await aio.notify(session, ["7"], "act.feed", **_emit_kwargs(importance="low"))
            await session.commit()
            page, total = await aio.list_feed(session, "7", page_size=5)
            before = await aio.unread_count(session, "7")
            read = await aio.mark_read(session, "7", page[0].id)
            after = await aio.unread_count(session, "7")
            other = await aio.mark_read(session, "8", page[0].id)
            archived = await aio.archive_read(session, "7")
            me = await aio.current_user_id(session)
        return total, before, read, after, other, archived, me

    total, before, read, after, other, archived, me = on_async_engine(migrated, body)
    assert (total, before, after, archived) == (1, 1, 0, 1)
    assert read.read_at is not None
    assert other is None  # another recipient's row answers like a missing one
    assert me == 0  # the conftest context resolver, called with the sync face


def test_the_seams_see_the_hosts_session(migrated):
    """The recipient filter and the context resolver are handed the sync face of
    the host's session, whose ``info`` is the async session's own: the usual way
    a host carries its request context into a seam."""
    seen = {}

    def resolver(s):
        seen["resolver"] = s.info.get("org")
        return (None, s.info.get("org"))

    def only_seven(s, ids, entity_type, entity_id, record):
        seen["filter"] = s.info.get("org")
        return [i for i in ids if i == "7"]

    service.configure_context_resolver(resolver)
    notifications.configure_recipient_filter(only_seven)

    async def body(engine):
        async with AsyncSession(engine) as session:
            session.info["org"] = "org-a"
            rows = await aio.notify(
                session, ["7", "8"], "act.seams", entity_type="case", entity_id=3,
                **_emit_kwargs(),
            )
            await session.commit()
        return rows

    rows = on_async_engine(migrated, body)
    assert [(r.user_id, r.org_id) for r in rows] == [("7", "org-a")]
    assert seen == {"resolver": "org-a", "filter": "org-a"}


def test_string_ids_round_trip_through_the_twins(migrated):
    """Opaque identity (0.17.0) on the async path: a UUID host emits, reads its
    feed and owns its rows with its own keys."""
    org, user = str(uuid.uuid4()), str(uuid.uuid4())
    service.configure_context_resolver(lambda s: (user, org))

    async def body(engine):
        async with AsyncSession(engine) as session:
            rows = await aio.notify(
                session, [uuid.UUID(user)], "act.uuid", entity_type="case",
                entity_id=uuid.UUID(user), **_emit_kwargs(),
            )
            await session.commit()
            page, _ = await aio.list_feed(session, user)
            read = await aio.mark_read(session, uuid.UUID(user), rows[0].id)
        return rows, page, read

    rows, page, read = on_async_engine(migrated, body)
    assert (rows[0].user_id, rows[0].org_id, rows[0].entity_id) == (user, org, user)
    assert [r.id for r in page] == [rows[0].id]
    assert read is not None and read.id == rows[0].id


# ── R7: migrate with no second engine ────────────────────────────────────────


def test_migrate_on_an_async_engine(engine):
    """R7: the chain runs on the sync face of the host's own async connection,
    and a second run is a no-op."""
    on_async_engine(engine, aio.migrate)
    on_async_engine(engine, aio.migrate)
    with Session(engine) as s:
        version = s.execute(
            sa.text("SELECT version_num FROM alembic_version_asas_notifications")
        ).scalar()
    assert version == "0012"


def test_migrate_refuses_a_connection_inside_a_transaction(engine):
    """The chain commits as it goes (0003 and 0004 build indexes CONCURRENTLY),
    so a connection already in the host's transaction is refused with the fix,
    before anything is written, rather than failing half way inside Alembic."""

    async def body(eng):
        async with eng.begin() as conn:
            await aio.migrate(conn)

    with pytest.raises(ValueError, match="outside one"):
        on_async_engine(engine, body)
    assert not sa.inspect(engine).has_table("notification")


def test_migrate_on_the_hosts_own_connection(engine):
    """Handed a connection outside a transaction, the chain runs on it and
    commits; the connection comes back usable."""

    async def body(eng):
        async with eng.connect() as conn:
            await aio.migrate(conn)
            return (await conn.execute(sa.text("SELECT 1"))).scalar()

    assert on_async_engine(engine, body) == 1
    assert sa.inspect(engine).has_table("notification_delivery")


# ── the async dispatcher ─────────────────────────────────────────────────────


class _AsyncAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


def test_async_dispatch_sends_on_the_hosts_async_engine(migrated):
    """R3 and §5.5: the pass runs on an ``AsyncEngine``, with a sync adapter (run
    off the loop) and an async one (awaited), and marks both rows sent."""
    sync_adapter, async_adapter = channels.LoggingAdapter(), _AsyncAdapter()
    notifications.register_adapter("email", sync_adapter)
    notifications.register_adapter("chat", async_adapter)
    with Session(migrated) as s:
        rows = notifications.notify(s, ["7"], "act.send", **_emit_kwargs())
        s.add(NotificationDelivery(notification_id=rows[0].id, org_id="1", channel="chat"))
        s.commit()

    handled = on_async_engine(migrated, aio.dispatch_pending)

    assert handled == 2
    assert [p.recipient_user_id for p in sync_adapter.sent] == ["7"]
    assert [p.channel for p in async_adapter.sent] == ["chat"]
    with Session(migrated) as s:
        assert {d.status for d in s.exec(select(NotificationDelivery))} == {
            DeliveryStatus.sent
        }


def test_async_dispatch_records_failures_like_the_sync_pass(migrated):
    """The outcome is the shared one: a raising adapter leaves the row pending
    with the attempt counted and the error kept."""

    class Broken:
        async def send(self, payload):
            raise RuntimeError("smtp down")

    notifications.register_adapter("email", Broken())
    with Session(migrated) as s:
        notifications.notify(s, ["7"], "act.fail", **_emit_kwargs())
        s.commit()

    assert on_async_engine(migrated, aio.dispatch_pending) == 1
    with Session(migrated) as s:
        d = s.exec(select(NotificationDelivery)).one()
    assert (d.status, d.attempts, d.last_error) == (DeliveryStatus.pending, 1, "smtp down")


def test_the_sync_pass_refuses_an_async_adapter(migrated):
    """Called from the sync pass, an async ``send`` returns a coroutine that
    would never run. The row is NOT marked sent: it is a failed attempt naming
    the async dispatcher, and retries."""
    notifications.register_adapter("email", _AsyncAdapter())
    with Session(migrated) as s:
        notifications.notify(s, ["7"], "act.mismatch", **_emit_kwargs())
        s.commit()

    assert notifications.dispatch_pending(migrated) == 1
    with Session(migrated) as s:
        d = s.exec(select(NotificationDelivery)).one()
    assert d.status == DeliveryStatus.pending and d.attempts == 1
    assert "aio.dispatch_pending" in d.last_error


def test_the_binder_runs_on_every_transaction_of_a_pass(migrated):
    """``bind`` is called on each transaction's connection, the sync face on the
    async pass: open, claim and finish, so a transaction-local pin holds for all
    three."""
    notifications.register_adapter("email", channels.LoggingAdapter())
    with Session(migrated) as s:
        notifications.notify(s, ["7"], "act.bind", **_emit_kwargs())
        s.commit()
    calls = []

    async def body(engine):
        return await aio.dispatch_pending(engine, bind=lambda c: calls.append(type(c).__name__))

    assert on_async_engine(migrated, body) == 1
    assert calls == ["Connection"] * 3
