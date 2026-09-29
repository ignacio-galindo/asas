"""Alembic environment for the package-owned chain (DR 0017 section 5).

Only ever run programmatically via ``asas_audit.migrate(engine)``: the host
passes its engine through ``config.attributes``; no ini file, no autogenerate.
The version table is package-scoped so this chain never collides with the host's.
"""

from alembic import context

VERSION_TABLE = "alembic_version_asas_audit"

engine = context.config.attributes["engine"]

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=VERSION_TABLE,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()
