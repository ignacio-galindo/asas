"""Where the package's tables live, and who may read their rows.

Two host options on top of the plain ``migrate(engine)`` contract, both off by
default, so a host that passes neither gets exactly the chain it always got:

- **a named Postgres schema** (:func:`configure_schema`, ``migrate(schema=...)``)
  for a host that keeps each module in a schema of its own. The package's five
  tables and its version table are created there, and every statement the package
  runs names it, whatever the connection's ``search_path`` says.
- **row level security on the tenant tables** (``migrate(tenant_guc=...)``):
  ``notification`` and ``notification_delivery``, keyed on their ``org_id``
  against the host's own session variable, written by ``asas_tenancy.enable_rls``
  so the policy is the one the host's own tables carry.

Why ``migrate.py`` itself is untouched: it is one of seven copies the repository
keeps byte-identical (``tests/test_migrate_parity.py``), and both options are
about the connection the chain runs on rather than about the chain. So they are
applied here, around it, and the shared copy stays shared.
"""

import re
from typing import Any, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from . import service
from .migrate import migrate as _migrate_chain
from .models import (
    Notification,
    NotificationChannelPolicy,
    NotificationDelivery,
    NotificationImportance,
    NotificationTopic,
)

#: Every table the package owns, in foreign-key order.
_MODELS = (
    Notification,
    NotificationDelivery,
    NotificationTopic,
    NotificationImportance,
    NotificationChannelPolicy,
)

#: The tables that hold a tenant's own rows: who was told what, and what is
#: still to be sent to them. ``migrate(tenant_guc=...)`` protects these.
#:
#: The three configuration tables are left out on purpose. They carry PLATFORM
#: rows (``org_id NULL``) that every tenant reads, and ``asas_tenancy``'s shared
#: row policy lets no ordinary connection write one, so protecting them would make
#: seeding a topic or a rung at boot need a role that bypasses the policy. A host
#: that wants them walled as well can still call ``enable_rls(...,
#: shared_rows=True)`` on them itself; routing reads them per org, so it stays
#: right under such a policy (see ``service._importance_rows``).
TENANT_TABLES = ("notification", "notification_delivery")

#: Lower case only: Postgres folds an unquoted name to lower case, and a schema
#: that needs quoting to keep its case is one that some host query (or a
#: ``regclass`` cast) will spell unquoted and miss.
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")

_schema: Optional[str] = None
_UNSET: Any = object()


def configure_schema(name: Optional[str]) -> None:
    """Place the package's tables in the Postgres schema ``name`` (``None``: the
    connection's default, which is the behaviour before 0.20.0).

    Call it once at startup, before the package runs its first statement, in
    every process that uses the package (the API, the worker, the one that runs
    ``migrate``). It qualifies the package's own ``Table`` objects, so the ORM's
    writes, the feed's reads and the dispatcher's core SQL all name the schema
    on any connection; nothing depends on ``search_path``. Postgres only: SQLite
    has no schemas to put them in, so a host that runs on both configures it on
    Postgres and leaves it unset on SQLite.

    Statement caches are keyed on the table objects, so a schema changed after
    an engine has already run the package's statements would be served the old
    SQL by that engine. The mapper's own caches are reset here; engines are the
    host's, which is why this belongs at startup.
    """
    global _schema
    if name is not None and not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"Not a usable schema name: {name!r}")
    tables = [m.__table__ for m in _MODELS]
    # Resolve every foreign key while the tables are still where the string
    # spelling ("notification.id") says they are; it is memoised on the key, so
    # the reference survives the move.
    for table in tables:
        for fk in table.foreign_keys:
            fk.column
    for table in tables:
        metadata = table.metadata
        metadata._remove_table(table.name, table.schema)
        table.schema = name
        table.fullname = f"{name}.{table.name}" if name else table.name
        metadata._add_table(table.name, name, table)
    for model in _MODELS:
        mapper = model.__mapper__
        mapper._compiled_cache.clear()
        # The mapper memoises an annotated COPY of its table for every query
        # that selects the entity, and the copy keeps the schema it was made
        # with: without this a SELECT names the new schema in its WHERE and the
        # old one in its FROM.
        mapper._reset_memoizations()
    service.config_cache_clear()
    _schema = name


def configured_schema() -> Optional[str]:
    """The schema :func:`configure_schema` set, or ``None``."""
    return _schema


def migrate(
    bind: Union[Engine, Connection],
    *,
    schema: Optional[str] = _UNSET,
    tenant_guc: Optional[str] = None,
) -> None:
    """Bring this package's tables to the current schema. Idempotent; call at boot
    alongside the host's own migrations.

    ``bind`` is an ``Engine`` or a ``Connection``. The chain commits as it goes,
    because two of its revisions (``0003``, ``0004``) build indexes CONCURRENTLY,
    which Postgres refuses inside a transaction block; so a connection must be
    handed over OUTSIDE a transaction, and one inside a transaction is refused
    rather than half migrated. That is how an async host runs this with no
    second engine: ``async with engine.connect() as conn: await
    conn.run_sync(migrate)``, or :func:`asas_notifications.aio.migrate`.

    ``schema`` (default: what :func:`configure_schema` set) creates the schema if
    it is missing and runs the chain inside it, version table included. The
    adopt check looks there too, and only there.

    ``tenant_guc`` (``"app.tenant_id"``, ``"procure.tenant_id"``) turns row level
    security on for :data:`TENANT_TABLES`: enabled, FORCED, and a policy that
    compares ``org_id`` with that session variable as text. It needs
    ``asas-tenancy`` (``asas-notifications[tenancy]``) and is a no-op off
    Postgres, as every ``asas_tenancy`` policy helper is. It only takes a table
    lock when something differs: a run that finds the policies already in place
    reads the catalogue and returns, so a rolling deploy whose old processes are
    still sending is not made to wait on DDL at every boot.

    **What the policy asks of the host.** Every connection that touches these
    tables binds the tenant first: requests as they already do, and the
    dispatcher through ``dispatch_pending(bind=...)``, which refuses to run a pass
    that would see nothing. The feed's writes (``mark_read`` and the rest) commit
    and then refresh the row, so the refresh runs in a NEW transaction: a host
    whose pin is transaction-local re-pins on ``after_begin``, or the refresh finds
    nothing.

    **For maintainers.** A later revision that rewrites rows of these tables
    runs under FORCE on a protected database, where a migration's unbound
    ``UPDATE`` matches nothing; it lifts FORCE for the statement, as ``0012``
    does.
    """
    if schema is _UNSET:
        schema = _schema
    if isinstance(bind, Connection):
        _placed(bind, schema, tenant_guc)
    elif schema is None and tenant_guc is None:
        _migrate_chain(bind)
    else:
        with bind.connect() as conn:
            _placed(conn, schema, tenant_guc)


def _placed(conn: Connection, schema: Optional[str], tenant_guc: Optional[str]) -> None:
    if conn.in_transaction():
        raise ValueError(
            "migrate() was handed a connection inside a transaction. The chain "
            "commits as it goes (revisions 0003 and 0004 build indexes "
            "CONCURRENTLY, which Postgres refuses in a transaction block), so pass "
            "a connection outside one: `with engine.connect() as conn: "
            "migrate(conn)`, or `async with engine.connect() as conn: await "
            "conn.run_sync(migrate)`."
        )
    postgres = conn.dialect.name == "postgresql"
    previous = None
    try:
        if schema is not None:
            previous = _enter_schema(conn, schema)
        _migrate_chain(conn)
        if conn.in_transaction():
            conn.commit()
        if tenant_guc is not None and postgres:
            with conn.begin():
                _protect(conn, tenant_guc)
    finally:
        if previous is not None:
            # The connection may be the host's, and pooled: it leaves with the
            # search_path it came with, on failure as well as on success.
            if conn.in_transaction():
                conn.rollback()
            conn.execute(
                sa.text("SELECT set_config('search_path', :p, false)"), {"p": previous}
            )
            conn.commit()


def _enter_schema(conn: Connection, schema: str) -> str:
    """Create ``schema`` if it is missing and make it the connection's whole
    ``search_path``; return the path to restore."""
    if conn.dialect.name != "postgresql":
        raise ValueError(
            f"migrate(schema={schema!r}) needs Postgres; {conn.dialect.name} has "
            "no schemas to place the tables in. Leave the schema unset there."
        )
    if not _IDENTIFIER.fullmatch(schema):
        raise ValueError(f"Not a usable schema name: {schema!r}")
    quoted = conn.dialect.identifier_preparer.quote_schema(schema)
    exists = conn.execute(
        sa.text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": schema}
    ).first()
    if exists is None:
        # Checked first rather than CREATE SCHEMA IF NOT EXISTS: that form still
        # needs CREATE on the database, which a role that was handed an existing
        # schema usually does not have.
        conn.execute(sa.text(f"CREATE SCHEMA {quoted}"))
    previous = conn.execute(sa.text("SELECT current_setting('search_path')")).scalar()
    # The revisions name their tables unqualified, so the schema is the only
    # entry on the path while they run: the adopt check, the version table and
    # every CREATE land there, and a same-named table elsewhere cannot be
    # mistaken for this package's. Session level, not SET LOCAL, because the
    # chain commits as it goes and a local setting would end with the first
    # commit.
    conn.execute(sa.text("SELECT set_config('search_path', :p, false)"), {"p": quoted})
    conn.commit()
    return previous


def _protect(conn: Connection, guc: str) -> None:
    try:
        import asas_tenancy
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise ModuleNotFoundError(
            "migrate(tenant_guc=...) writes its policies with asas-tenancy, which "
            "is an optional extra: install asas-tenancy beside this package."
        ) from exc
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with Operations.context(MigrationContext.configure(conn)):
        for table in TENANT_TABLES:
            state = _protection(conn, table, guc)
            if state == "current":
                continue
            if state == "stale":
                asas_tenancy.disable_rls(table)
            asas_tenancy.enable_rls(table, column="org_id", column_type="text", guc=guc)


def _protection(conn: Connection, table: str, guc: str) -> str:
    """``current`` (enabled, forced, and the policy reads ``guc``), ``stale``
    (a policy of that name exists but something differs), or ``absent``.
    Catalogue reads only, so a boot that finds everything in place locks
    nothing."""
    import asas_tenancy

    row = conn.execute(
        sa.text(
            "SELECT c.relrowsecurity AND c.relforcerowsecurity,"
            " pg_get_expr(p.polqual, p.polrelid)"
            " FROM pg_class c"
            " LEFT JOIN pg_policy p ON p.polrelid = c.oid AND p.polname = :policy"
            " WHERE c.oid = to_regclass(:table)"
        ),
        {"policy": asas_tenancy.policy_name(table), "table": table},
    ).first()
    if row is None or row[1] is None:
        return "absent"
    return "current" if row[0] and f"'{guc}'" in row[1] else "stale"
