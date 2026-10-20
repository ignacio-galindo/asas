"""The outbox row carries its tenant: ``notification_delivery.org_id``.

Row level security keys a table on a column of its own. ``notification`` has
``org_id``; its outbox rows did not, so a host that walled the package's tables
off by tenant had to write a policy that reached through a subquery into
``notification``, and a host using ``asas_tenancy.enable_rls`` could not protect
the outbox at all. The column is copied from the notification at emit (it never
changes afterwards: a notification does not move between orgs), and this
revision copies it for the rows already there.

**The backfill must see every row.** A host that already put a FORCE policy on
these tables (one that wired its own isolation before ``migrate`` offered it)
would have this ``UPDATE`` filtered down to nothing, because a migration binds no
tenant, and every existing row would keep a NULL org and stay invisible to its
tenant's dispatcher. So
FORCE is lifted for the statement on whichever of the two tables carries it and
put back in the same transaction; on Postgres the DDL is transactional, so a
failure rolls the lift back with everything else. This works for the role that
owns the tables, which is the role that runs ``migrate``.

**The column stays nullable in this revision**, deliberately. During a rolling
deploy the processes still on 0.19 keep inserting outbox rows without it, and a
NOT NULL column would fail every emit they make (and the producer transaction
around it). ``notify`` always writes it from 0.20.0; a later revision backfills
whatever the old processes wrote in the window and tightens the column.

**A delivery row with no notification fails the revision**, loudly and with its
count, rather than being deleted. Only a SQLite database with foreign keys off can
hold one, and what to do with it is the host's decision: such a row can never be
dispatched (the dispatcher joins to its notification), so deleting it loses
nothing, but this package does not delete rows it cannot account for.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "notification_delivery"
_INDEX = "ix_notification_delivery_org_id"


def _forced(table: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    return bool(
        bind.execute(
            sa.text(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:t)"
            ),
            {"t": table},
        ).scalar()
    )


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("org_id", sa.String(length=255), nullable=True))

    forced = [t for t in ("notification", _TABLE) if _forced(t)]
    for table in forced:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"UPDATE {_TABLE} SET org_id = (SELECT n.org_id FROM notification n"
        f" WHERE n.id = {_TABLE}.notification_id) WHERE org_id IS NULL"
    )
    orphans = op.get_bind().execute(
        sa.text(f"SELECT count(*) FROM {_TABLE} WHERE org_id IS NULL")
    ).scalar()
    for table in forced:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if orphans:
        raise RuntimeError(
            f"asas-notifications 0012: {orphans} {_TABLE} row(s) have no "
            f"notification, so they have no org to copy. They can never be "
            f"dispatched; delete them (DELETE FROM {_TABLE} WHERE notification_id "
            f"NOT IN (SELECT id FROM notification)) and re-run migrate()."
        )

    op.create_index(_INDEX, _TABLE, ["org_id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("org_id")
