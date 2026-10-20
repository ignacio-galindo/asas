"""Migration ``0012``: the outbox row carries its tenant.

The column is what lets row level security key ``notification_delivery`` on a
column of its own. These pin the backfill, including the case it exists for on
a host that walled the tables off itself before ``migrate`` offered to.
"""

import os

import pytest
import sqlalchemy as sa
from alembic import command

import asas_notifications as notifications
from asas_notifications.migrate import _config


def _at_0011_with_an_undelivered_row(engine):
    command.upgrade(_config(engine), "0011")
    with engine.begin() as conn:
        nid = conn.execute(
            sa.text(
                "INSERT INTO notification (org_id, user_id, importance, title, created_at)"
                " VALUES ('org-a', '7', 'high', 'Hello', CURRENT_TIMESTAMP) RETURNING id"
            )
        ).scalar()
        conn.execute(
            sa.text(
                "INSERT INTO notification_delivery (notification_id, channel, status, attempts)"
                " VALUES (:n, 'email', 'pending', 0)"
            ),
            {"n": nid},
        )


def _delivery_orgs(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT org_id FROM notification_delivery")).scalars().all()


def test_existing_outbox_rows_get_their_notifications_org(engine):
    _at_0011_with_an_undelivered_row(engine)
    notifications.migrate(engine)
    assert _delivery_orgs(engine) == ["org-a"]
    col = next(
        c for c in sa.inspect(engine).get_columns("notification_delivery") if c["name"] == "org_id"
    )
    assert col["nullable"] is False


@pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"),
    reason="row level security is a Postgres feature",
)
def test_the_backfill_sees_through_a_hosts_own_forced_policy(engine):
    """A host that FORCEd its own policies on these tables (ad-recruiter's
    shape, the delivery policy reaching through ``notification``) would have an
    unbound backfill filtered to nothing and the NOT NULL fail blaming the data.
    The revision lifts FORCE for the statement and puts it back."""
    import asas_tenancy
    from asas_tenancy.conformance import is_required

    reason = asas_tenancy.unenforced_reason(engine)
    if reason:
        if is_required():
            pytest.fail(f"ASAS_REQUIRE_RLS is set but {reason}")
        pytest.skip(reason)
    _at_0011_with_an_undelivered_row(engine)
    with engine.begin() as conn:
        for table in ("notification", "notification_delivery"):
            conn.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        conn.execute(
            sa.text(
                "CREATE POLICY notification_tenant_isolation ON notification"
                " USING (org_id = current_setting('app.tenant_id', true))"
            )
        )
        conn.execute(
            sa.text(
                "CREATE POLICY notification_delivery_tenant_isolation ON notification_delivery"
                " USING (EXISTS (SELECT 1 FROM notification n"
                " WHERE n.id = notification_delivery.notification_id))"
            )
        )

    notifications.migrate(engine)

    with engine.begin() as conn:
        asas_tenancy.set_tenant_guc(conn, "org-a")
        assert conn.execute(sa.text("SELECT org_id FROM notification_delivery")).scalars().all() == [
            "org-a"
        ]
        forced = conn.execute(
            sa.text(
                "SELECT bool_and(relforcerowsecurity) FROM pg_class"
                " WHERE relname IN ('notification', 'notification_delivery')"
            )
        ).scalar()
    assert forced is True
