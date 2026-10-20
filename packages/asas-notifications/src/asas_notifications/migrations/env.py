"""Alembic environment for the package-owned chain (DR 0017 §5).

Only ever run programmatically via ``asas_notifications.migrate(...)`` — the host passes
its engine through ``config.attributes``; there is no ini file and no autogenerate.
The version table is package-scoped so this chain never collides with the host's.

The bind may also be a ``Connection`` (DR 0005 §5.4): an async host runs the chain
on the sync face of its own connection (``await conn.run_sync(migrate)``), and the
schema option runs it on a connection whose ``search_path`` names the schema. Only
an engine is connected here; a connection is used as it is.
"""

from alembic import context
from sqlalchemy.engine import Connection

VERSION_TABLE = "alembic_version_asas_notifications"

bind = context.config.attributes["engine"]


def _run(connection: Connection) -> None:
    if connection.in_transaction():
        # Only ``migrate()`` hands a connection over, and it refuses one already
        # in a transaction, so a transaction open here is its own adopt check
        # reading the catalogue. Ending it lets Alembic own the transactions,
        # which it must: ``0003`` and ``0004`` commit around CONCURRENTLY index
        # builds, and Alembic can only do that in a transaction it began.
        connection.commit()
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=VERSION_TABLE,
        # SQLite can't ALTER TABLE for most changes; harmless elsewhere.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if isinstance(bind, Connection):
    _run(bind)
else:
    with bind.connect() as connection:
        _run(connection)
