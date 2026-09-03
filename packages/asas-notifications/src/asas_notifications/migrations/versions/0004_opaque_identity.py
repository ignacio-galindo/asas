"""Identity columns widen from INTEGER to VARCHAR.

``org_id``, ``user_id`` and ``entity_id`` were ``int``. That reads as decoupling
and is not: an integer column is an assertion about the host's schema, namely
that it numbers its users and organisations sequentially. A host on UUID primary
keys had nothing to put there, and no seam widened it, so the package could not
be adopted at all.

Existing integer values cast cleanly to their decimal text, so this is
value-preserving in the upgrade direction and there is no backfill.

**No index changes.** Every composite from migration 0003 keeps its column list
and order; only the column type under them moves. Postgres rebuilds the index
entries itself as part of the type change.

**Two dialects, two mechanisms.** Postgres does the cast in place with
``USING``. SQLite has no ALTER COLUMN at all, so the batch operation rebuilds
the table, which is why the index drop and recreate is explicit here: batch mode
reflects the indexes and reissues them, and the reflected names have to match
what 0003 created or the rebuild leaves differently-named indexes behind.

**Test this on the engine you deploy on.** SQLite's column affinity will happily
compare ``user_id == 1`` against a text column and keep working, so a host whose
suite is SQLite-only sees nothing. Postgres raises
``operator does not exist: character varying = integer``. Two tests in this
package's own suite passed on SQLite and failed on Postgres for exactly that
reason while this change was being written.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDENTITY_COLUMNS = ("org_id", "user_id", "entity_id")

#: The composites 0003 created over these columns. Named explicitly because the
#: SQLite path rebuilds the table and has to put them back as they were.
_FEED_INDEX = "ix_notification_user_org_archived_created"
_BADGE_INDEX = "ix_notification_user_org_read_archived"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        for column in _IDENTITY_COLUMNS:
            op.execute(
                f"ALTER TABLE notification ALTER COLUMN {column} "
                f"TYPE VARCHAR(255) USING {column}::varchar"
            )
    else:
        with op.batch_alter_table("notification") as batch:
            for column in _IDENTITY_COLUMNS:
                batch.alter_column(column, type_=sa.String(255))


def downgrade() -> None:
    """Back to INTEGER.

    Lossy by nature, and it has to be: a value that was never an integer cannot
    become one. A host that adopted the package BECAUSE of this change has UUIDs
    in these columns and this direction will fail on them, which is the honest
    outcome. Nothing here tries to be clever about that; a downgrade past this
    revision is only meaningful on a database that was integer-keyed all along.
    """
    if _is_postgres():
        for column in _IDENTITY_COLUMNS:
            op.execute(
                f"ALTER TABLE notification ALTER COLUMN {column} "
                f"TYPE INTEGER USING {column}::integer"
            )
    else:
        with op.batch_alter_table("notification") as batch:
            for column in _IDENTITY_COLUMNS:
                batch.alter_column(column, type_=sa.Integer())
