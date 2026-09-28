"""audit_event.encoding: which canonical encoding produced each row's hash.

The verifier recomputes every row with the encoding the row names, which is what
lets a host hand over a hash chain it already keeps: its old rows carry the name
of a legacy encoding it registers, and this package's rows carry
``CURRENT_ENCODING``.

**The backfill is the column default, not an UPDATE.** Every row that exists
before this revision was written by this package at 0.1.0, whose bytes are
exactly ``CURRENT_ENCODING``, so ``ADD COLUMN ... DEFAULT`` labels them all
correctly in the DDL itself. That matters for more than speed: an UPDATE here
would be refused by the append-only trigger, and under ``FORCE ROW LEVEL
SECURITY`` with no tenant pinned it would match no rows at all and succeed
silently. The DDL form is subject to neither.

**An adopting host adds the column itself.** A host taking over its own older
chain renames its columns to this package's names in its own migration and adds
``encoding`` there with its legacy name as the default, so its old rows are
labelled by the same DDL mechanism before this revision runs. This revision then
finds the column present and leaves it alone, default included: the default only
ever applies to a row written by code that does not know the column exists, and
on an adopted table that is the legacy writer. The README's adoption section is
the procedure.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen here rather than imported: a revision must mean the same thing forever,
# and CURRENT_ENCODING moves when the package adds an encoding. What 0.1.0 wrote
# is this name, and always will be.
_V1 = "asas-audit/1"


def _has_encoding_column() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns("audit_event")
    return any(c["name"] == "encoding" for c in columns)


def upgrade() -> None:
    if _has_encoding_column():
        return
    op.add_column(
        "audit_event",
        sa.Column("encoding", sa.String(32), nullable=False, server_default=_V1),
    )


def downgrade() -> None:
    with op.batch_alter_table("audit_event") as batch_op:
        batch_op.drop_column("encoding")
