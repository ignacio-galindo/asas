"""Asas audit: a tamper-evident, append-only audit log in the host's own database.

Every event is hash-chained to the one before it within its organisation, so
editing, deleting or inserting a row out of band shows up on verify at exactly
that row. Events are appended inside the caller's transaction, so the audit
row commits or rolls back with the change it describes. Extracted from a
production platform's audit module, generalised to plain
string identifiers and to SQLite as well as Postgres.

Host contract (table-owning variant, no seed):

- :func:`migrate` - the package-owned Alembic chain, including the database
  triggers that refuse UPDATE and DELETE.
- :func:`build_routers` - read-only ``/audit/events`` and ``/audit/verify``.
- :func:`configure_org_resolver` - optional multi-tenancy hook.
- :func:`append`, :func:`verify`, :func:`list_events` - take an explicit ``Session``.

Quick start::

    import asas_audit

    asas_audit.migrate(engine)
    asas_audit.append(session, action="invoice.approved", resource_type="invoice",
                      resource_id=42, actor="user:7", payload={"amount": "120.00"})
    session.commit()
    assert asas_audit.verify(session).is_intact
"""

from .chain import ChainBreak, VerifyReport, verify_rows
from .migrate import migrate
from .models import AuditEvent, AuditHead
from .router import build_routers
from .service import (
    MAX_APPEND_ATTEMPTS,
    AuditContentionError,
    append,
    configure_org_resolver,
    list_events,
    verify,
)

__version__ = "0.1.0"

__all__ = [
    "AuditContentionError",
    "AuditEvent",
    "AuditHead",
    "ChainBreak",
    "MAX_APPEND_ATTEMPTS",
    "VerifyReport",
    "append",
    "build_routers",
    "configure_org_resolver",
    "list_events",
    "migrate",
    "verify",
    "verify_rows",
    "__version__",
]
