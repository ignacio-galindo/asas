"""Append and verify. Every function takes the host's ``Session`` explicitly.

**The append rides the caller's transaction.** An audit row commits or rolls
back with the business change it describes; ``append`` flushes and never
commits. That is the whole reason an audit log belongs in the application's own
database rather than beside it.

**Concurrent appends serialize on the chain head, by compare-and-set.** The
tempting design, ``SELECT ... FOR UPDATE`` on the newest event, forks the
chain: a waiter woken after the holder commits still reads the OLD tail under
its statement snapshot, so two appends chain onto the same parent and verify
later reports a break that is not tampering at all. It also has nothing to lock
while the chain is empty. Instead each organisation has one head row, and an
append moves it with ``UPDATE ... WHERE last_hash = <what I read>``. On
Postgres (READ COMMITTED) a concurrent writer blocks on that row, re-evaluates
the WHERE against the committed version, matches nothing, re-reads and retries;
on SQLite the database lock serializes writers outright. The first event of a
chain inserts the head with ``ON CONFLICT DO NOTHING`` so two first appends
cannot both win. Portable, and no advisory-lock dialect branch.

A host running REPEATABLE READ or SERIALIZABLE gets a serialization error on
the contended append instead of a retry; that is the isolation level's
contract, and its own retry loop is where it belongs.

Async hosts call these through their session's sync bridge::

    await async_session.run_sync(lambda s: asas_audit.append(s, ...))
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from sqlalchemy import insert, select, update
from sqlmodel import Session

from .chain import VerifyReport, chain_payload, compute_hash, normalize_payload, normalize_time, verify_rows
from .models import AuditEvent, AuditHead

#: How many times a contended append re-reads the head before giving up. Each
#: retry means another writer landed first, so this is a bound on pathological
#: contention, not a normal path.
MAX_APPEND_ATTEMPTS = 50

_org_resolver: Optional[Callable[[Session], Optional[str]]] = None


class AuditContentionError(RuntimeError):
    """The head moved on every attempt: something is appending in a tight loop."""


def configure_org_resolver(fn: Optional[Callable[[Session], Optional[str]]]) -> None:
    """Optional multi-tenancy hook: how to read the current organisation from a
    session (for the routers, and for ``append`` when no ``org_id`` is passed).
    Unset, everything is the platform's single chain."""
    global _org_resolver
    _org_resolver = fn


def _org(session: Session, org_id: Optional[Any]) -> str:
    if org_id is None and _org_resolver is not None:
        org_id = _org_resolver(session)
    return "" if org_id is None else str(org_id)


def _insert_head_once(session: Session, org: str, last_hash: str) -> bool:
    """Create the head for a chain's first event; False when another writer
    created it first (the caller then retries as a normal append)."""
    dialect = session.get_bind().dialect.name
    values = {"org_id": org, "last_hash": last_hash, "events": 1}
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:  # pragma: no cover - the package supports the two engines CI runs
        stmt = insert(AuditHead).values(**values)
        session.execute(stmt)
        return True
    stmt = dialect_insert(AuditHead).values(**values).on_conflict_do_nothing(index_elements=["org_id"])
    return session.execute(stmt).rowcount == 1


def append(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: Any,
    actor: str,
    payload: Optional[dict[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
    org_id: Optional[Any] = None,
) -> AuditEvent:
    """Append one event to its organisation's chain and return it (flushed, not
    committed). ``resource_id`` and ``org_id`` may be ints, UUIDs or strings."""
    org = _org(session, org_id)
    moment = normalize_time(occurred_at or datetime.now(timezone.utc))
    body = normalize_payload(payload)
    event_id = str(uuid.uuid4())
    fields = {
        "event_id": event_id,
        "org_id": org,
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "payload": body,
        "occurred_at": moment,
    }
    for _ in range(MAX_APPEND_ATTEMPTS):
        # Columns, not the ORM entity: a retry must see the committed head, and
        # an entity would come back from the identity map as it was first read.
        head = session.execute(
            select(AuditHead.last_hash, AuditHead.events).where(AuditHead.org_id == org)
        ).first()
        prev = head.last_hash if head is not None else None
        new_hash = compute_hash(prev, chain_payload(**fields))
        if head is None:
            if not _insert_head_once(session, org, new_hash):
                continue
        else:
            moved = session.execute(
                update(AuditHead)
                .where(AuditHead.org_id == org, AuditHead.last_hash == prev)
                .values(last_hash=new_hash, events=head.events + 1)
            )
            if moved.rowcount != 1:
                continue
        event = AuditEvent(**fields, hash_prev=prev, hash_current=new_hash)
        session.add(event)
        session.flush()
        session.execute(
            update(AuditHead).where(AuditHead.org_id == org).values(last_seq=event.seq)
        )
        return event
    raise AuditContentionError(
        f"the audit head for {org or 'the platform'!r} moved on every one of "
        f"{MAX_APPEND_ATTEMPTS} attempts"
    )


def verify(session: Session, org_id: Optional[Any] = None) -> VerifyReport:
    """Walk one organisation's whole chain and report every break."""
    org = _org(session, org_id)
    # The head FIRST, then the events it covers (see AuditHead.last_seq).
    head = session.execute(
        select(AuditHead.last_hash, AuditHead.events, AuditHead.last_seq).where(AuditHead.org_id == org)
    ).first()
    stmt = select(AuditEvent).where(AuditEvent.org_id == org)
    if head is not None:
        stmt = stmt.where(AuditEvent.seq <= head.last_seq)
    rows = session.execute(stmt.order_by(AuditEvent.seq)).scalars()
    return verify_rows(
        org,
        rows,
        head_hash=head.last_hash if head is not None else None,
        head_events=head.events if head is not None else 0,
    )


def list_events(
    session: Session,
    *,
    org_id: Optional[Any] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[Any] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    before_seq: Optional[int] = None,
    limit: int = 50,
) -> Sequence[AuditEvent]:
    """Newest first, keyset-paged on ``seq`` (pass the last ``seq`` you saw as
    ``before_seq``), so a page never shifts while events are being appended."""
    stmt = select(AuditEvent).where(AuditEvent.org_id == _org(session, org_id))
    if resource_type is not None:
        stmt = stmt.where(AuditEvent.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(AuditEvent.resource_id == str(resource_id))
    if actor is not None:
        stmt = stmt.where(AuditEvent.actor == actor)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    if before_seq is not None:
        stmt = stmt.where(AuditEvent.seq < before_seq)
    stmt = stmt.order_by(AuditEvent.seq.desc()).limit(max(1, min(limit, 500)))
    return session.execute(stmt).scalars().all()
