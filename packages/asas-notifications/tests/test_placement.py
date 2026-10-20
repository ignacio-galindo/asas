"""Where the tables live, and who may read their rows: the schema option, the
tenant isolation hook, and the dispatcher's refusal of a tenant-blind pass.

Postgres only. The isolation tests also need a role the policies apply to (not
a superuser, not BYPASSRLS): with any other they skip and say why, and with
``ASAS_REQUIRE_RLS=1`` (CI) that skip is a failure, asas-tenancy's rule, because
an isolation suite that silently stopped running proves nothing.
"""

import os

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
    NotificationImportance,
)

from conftest import on_async_engine

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"),
    reason="schemas and row level security are Postgres features",
)

SCHEMA = "asas_notif_placed"
GUC = "procure.tenant_id"


def _emit(session, recipients, org, **kw):
    kw.setdefault("topic", "general")
    kw.setdefault("importance", "high")
    kw.setdefault("title", "Hello")
    return notifications.notify(session, recipients, "act.placed", org_id=org, **kw)


def _tables_in(engine, schema):
    return set(sa.inspect(engine).get_table_names(schema=schema))


# ── the schema option ────────────────────────────────────────────────────────


@pytest.fixture()
def placed(engine):
    """The package configured into :data:`SCHEMA`, on a database where it does
    not exist yet. Conftest resets the configuration after the test."""
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    notifications.configure_schema(SCHEMA)
    yield engine
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def test_migrate_builds_the_chain_inside_the_schema(placed):
    """Every table and the version table land in the named schema, none in
    public, and a second run is a no-op."""
    notifications.migrate(placed)
    notifications.migrate(placed)
    assert _tables_in(placed, SCHEMA) == {
        "notification",
        "notification_delivery",
        "notification_topic",
        "notification_importance",
        "notification_channel_policy",
        "alembic_version_asas_notifications",
    }
    assert not _tables_in(placed, "public") & {"notification", "alembic_version_asas_notifications"}


def test_a_same_named_table_elsewhere_is_not_adopted(placed):
    """The adopt check looks in the schema and only there. An unrelated
    ``public.notification`` would make a plain ``migrate`` refuse to adopt; it
    does not exist as far as a placed chain is concerned."""
    with placed.begin() as conn:
        conn.execute(sa.text("CREATE TABLE public.notification (x integer)"))
    notifications.migrate(placed)
    assert "notification_delivery" in _tables_in(placed, SCHEMA)
    cols = {c["name"] for c in sa.inspect(placed).get_columns("notification", schema="public")}
    assert cols == {"x"}  # untouched


def test_the_hosts_connection_keeps_its_search_path(placed):
    """migrate runs on the host's connection with the schema as its only path
    entry, and hands the connection back with the path it came with."""
    with placed.connect() as conn:
        before = conn.execute(sa.text("SHOW search_path")).scalar()
        conn.commit()
        notifications.migrate(conn)
        assert conn.execute(sa.text("SHOW search_path")).scalar() == before


def test_the_package_runs_in_the_schema_on_any_connection(placed):
    """Emit, feed and dispatch all name the schema themselves: the connection's
    search_path is the default and never mentions it."""
    notifications.migrate(placed)
    service.configure_context_resolver(None)  # the feed scopes on the user alone
    adapter = channels.LoggingAdapter()
    notifications.register_adapter("email", adapter)
    with Session(placed) as s:
        rows = _emit(s, ["7"], "org-a")
        s.commit()
        page, total = service.list_feed(s, "7")
    assert total == 1 and page[0].id == rows[0].id
    assert notifications.dispatch_pending(placed) == 1
    assert [p.org_id for p in adapter.sent] == ["org-a"]
    with placed.connect() as conn:
        count = conn.execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.notification")).scalar()
    assert count == 1


def test_the_async_face_runs_in_the_schema(placed):
    """The same through the twins: migrate on the host's async engine, emit in
    its transaction, dispatch on its engine."""
    adapter = channels.LoggingAdapter()
    notifications.register_adapter("email", adapter)

    async def body(engine):
        await aio.migrate(engine)
        async with AsyncSession(engine) as session:
            rows = await aio.notify(
                session, ["7"], "act.placed", topic="general", importance="high",
                title="Hello", org_id="org-a",
            )
            await session.commit()
        return rows, await aio.dispatch_pending(engine)

    rows, handled = on_async_engine(placed, body)
    assert handled == 1 and adapter.sent[0].notification_id == rows[0].id
    assert "notification" in _tables_in(placed, SCHEMA)


@pytest.mark.parametrize("name", ["x; DROP TABLE y", "Notifications"])
def test_a_bad_schema_name_is_refused(name):
    """Injection, and a name that only keeps its case quoted: Postgres folds it
    wherever it is spelled bare, a ``regclass`` cast included."""
    with pytest.raises(ValueError, match="schema name"):
        notifications.configure_schema(name)


# ── the tenant isolation hook ────────────────────────────────────────────────


@pytest.fixture()
def protected(engine):
    """Migrated with RLS on the tenant tables, on a role the policies bind."""
    import asas_tenancy
    from asas_tenancy.conformance import is_required

    reason = asas_tenancy.unenforced_reason(engine)
    if reason:
        if is_required():
            pytest.fail(f"ASAS_REQUIRE_RLS is set but {reason}")
        pytest.skip(reason)
    notifications.migrate(engine, tenant_guc=GUC)
    return engine


def _pin(conn, tenant):
    import asas_tenancy

    asas_tenancy.set_tenant_guc(conn, tenant, guc=GUC)


def _emit_as(engine, tenant, recipients, **kw):
    with Session(engine) as s:
        _pin(s, tenant)
        rows = _emit(s, recipients, tenant, **kw)
        s.flush()
        # Read before the commit: the pin is transaction-local, so after it a
        # refresh of these rows runs unbound and the policy hides them.
        ids = [r.id for r in rows]
        s.commit()
        return ids


def test_the_tenant_tables_are_protected(protected):
    import asas_tenancy

    asas_tenancy.assert_tables_protected(protected, notifications.TENANT_TABLES)


def test_a_tenant_sees_its_own_rows_and_nothing_else(protected):
    """Both tables, read through the policy: bound to A, A's rows; unbound,
    nothing at all (fail closed)."""
    a = _emit_as(protected, "A", ["7"])
    _emit_as(protected, "B", ["8"])
    with Session(protected) as s:
        _pin(s, "A")
        assert [n.id for n in s.exec(select(Notification))] == a
        assert {d.org_id for d in s.exec(select(NotificationDelivery))} == {"A"}
    with Session(protected) as s:
        assert s.exec(select(Notification)).all() == []
        assert s.exec(select(NotificationDelivery)).all() == []


def test_a_write_for_another_tenant_is_refused(protected):
    """WITH CHECK: an emit stamped for B on a connection bound to A fails at the
    database, never lands where A could later be shown it."""
    with Session(protected) as s:
        _pin(s, "A")
        with pytest.raises(sa.exc.DBAPIError, match="row-level security"):
            _emit(s, ["7"], "B")
            s.flush()


def test_a_tenant_blind_pass_is_refused_not_reported_empty(protected):
    """The trap: with no tenant bound the outbox reads empty, and a pass that
    returned 0 would look like a quiet queue. It raises instead, and the row is
    still pending."""
    notifications.register_adapter("email", channels.LoggingAdapter())
    _emit_as(protected, "A", ["7"])
    with pytest.raises(RuntimeError, match="no tenant bound"):
        notifications.dispatch_pending(protected)
    with pytest.raises(RuntimeError, match="no tenant bound"):
        on_async_engine(protected, aio.dispatch_pending)
    with Session(protected) as s:
        _pin(s, "A")
        assert s.exec(select(NotificationDelivery)).one().status == DeliveryStatus.pending


def test_a_bound_pass_sends_its_tenant_only(protected):
    """Sync for A, async for B, each with the same binder shape: each pass sees
    and sends its own tenant's outbox and leaves the other's alone."""
    adapter = channels.LoggingAdapter()
    notifications.register_adapter("email", adapter)
    _emit_as(protected, "A", ["7"])
    _emit_as(protected, "B", ["8"])

    assert notifications.dispatch_pending(protected, bind=lambda c: _pin(c, "A")) == 1
    assert [p.org_id for p in adapter.sent] == ["A"]

    async def body(engine):
        return await aio.dispatch_pending(engine, bind=lambda c: _pin(c, "B"))

    assert on_async_engine(protected, body) == 1
    assert [p.org_id for p in adapter.sent] == ["A", "B"]
    for tenant in ("A", "B"):
        with Session(protected) as s:
            _pin(s, tenant)
            assert s.exec(select(NotificationDelivery)).one().status == DeliveryStatus.sent


def test_async_notify_runs_under_the_hosts_pin(protected):
    """The twin runs on the host's own connection, so the tenant the host pinned
    there is the one the policy checks: no second connection to forget it on."""
    import asas_tenancy

    async def body(engine):
        async with AsyncSession(engine) as session:
            await asas_tenancy.async_set_tenant_guc(session, "A", guc=GUC)
            rows = await aio.notify(
                session, ["7"], "act.placed", topic="general", importance="high",
                title="Hello", org_id="A",
            )
            await session.commit()
        return rows

    rows = on_async_engine(protected, body)
    with Session(protected) as s:
        _pin(s, "A")
        assert [d.notification_id for d in s.exec(select(NotificationDelivery))] == [rows[0].id]


def test_the_hook_is_idempotent_and_heals_a_stale_policy(protected):
    """A second run changes nothing; a run with a different variable replaces
    the policy rather than leaving the old one in force."""

    def quals():
        with protected.connect() as conn:
            return dict(
                conn.execute(
                    sa.text(
                        "SELECT c.relname, pg_get_expr(p.polqual, p.polrelid) FROM pg_policy p"
                        " JOIN pg_class c ON c.oid = p.polrelid"
                        " WHERE c.relname IN ('notification', 'notification_delivery')"
                    )
                ).all()
            )

    notifications.migrate(protected, tenant_guc=GUC)
    assert all(GUC in q for q in quals().values()) and len(quals()) == 2
    notifications.migrate(protected, tenant_guc="app.other_tenant")
    assert all("app.other_tenant" in q for q in quals().values()) and len(quals()) == 2


def test_routing_reads_each_orgs_own_rung_under_a_config_policy(protected):
    """A host that walls the importance catalogue as well (shared platform rows,
    per org overrides) must still route each org on its own override. The
    catalogue used to be cached whole, so whichever tenant filled the cache
    decided what every other tenant's override was for a TTL."""
    import asas_tenancy
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with protected.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            asas_tenancy.enable_rls(
                "notification_importance", column="org_id", column_type="text",
                guc=GUC, shared_rows=True,
            )
    with Session(protected) as s:
        _pin(s, "B")
        s.add(NotificationImportance(org_id="B", key="low", name="Low", emails_by_default=True))
        s.commit()

    _emit_as(protected, "A", ["7"], importance="low")  # fills the cache as A
    b = _emit_as(protected, "B", ["8"], importance="low")
    with Session(protected) as s:
        _pin(s, "B")
        channels_for_b = [
            d.channel
            for d in s.exec(select(NotificationDelivery).where(NotificationDelivery.notification_id == b[0]))
        ]
    assert channels_for_b == ["email"]
