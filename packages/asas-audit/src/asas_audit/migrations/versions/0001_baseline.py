"""Baseline: the event table, the per-organisation head, and append-only triggers.

The triggers make append-only hold against raw SQL, not only against this
package's API: UPDATE and DELETE on ``asas_audit_event`` are refused by the
database on both engines, and TRUNCATE as well on Postgres (SQLite has none).
The head table is updated on every append, so it carries no such trigger; the
verifier checks it against the events instead.

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MESSAGE = "asas_audit_event is append-only"


def upgrade() -> None:
    op.create_table(
        "asas_audit_event",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("org_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("hash_prev", sa.String(64), nullable=True),
        sa.Column("hash_current", sa.String(64), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_asas_audit_event_event_id"),
    )
    op.create_index("ix_asas_audit_event_org_id", "asas_audit_event", ["org_id"])
    op.create_index("ix_asas_audit_event_action", "asas_audit_event", ["action"])
    op.create_index(
        "ix_asas_audit_event_resource", "asas_audit_event", ["org_id", "resource_type", "resource_id"]
    )
    op.create_table(
        "asas_audit_head",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("last_hash", sa.String(64), nullable=False),
        sa.Column("events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"""
            CREATE FUNCTION asas_audit_forbid() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION '{_MESSAGE}'; END $$;
            """
        )
        op.execute(
            "CREATE TRIGGER asas_audit_event_no_change BEFORE UPDATE OR DELETE ON asas_audit_event "
            "FOR EACH ROW EXECUTE FUNCTION asas_audit_forbid()"
        )
        op.execute(
            "CREATE TRIGGER asas_audit_event_no_truncate BEFORE TRUNCATE ON asas_audit_event "
            "FOR EACH STATEMENT EXECUTE FUNCTION asas_audit_forbid()"
        )
    else:
        for verb in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER asas_audit_event_no_{verb.lower()} BEFORE {verb} ON asas_audit_event "
                f"BEGIN SELECT RAISE(ABORT, '{_MESSAGE}'); END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS asas_audit_event_no_truncate ON asas_audit_event")
        op.execute("DROP TRIGGER IF EXISTS asas_audit_event_no_change ON asas_audit_event")
        op.execute("DROP FUNCTION IF EXISTS asas_audit_forbid()")
    else:
        op.execute("DROP TRIGGER IF EXISTS asas_audit_event_no_update")
        op.execute("DROP TRIGGER IF EXISTS asas_audit_event_no_delete")
    op.drop_table("asas_audit_head")
    op.drop_table("asas_audit_event")
