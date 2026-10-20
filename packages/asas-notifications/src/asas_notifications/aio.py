"""The async face of the package, for hosts built on ``AsyncSession`` (DR 0005).

Import it as the module it is::

    from asas_notifications import aio as notifications

    rows = await notifications.notify(session, [user_id], "order.approve",
                                      topic="orders", importance="high",
                                      title="Order approved", org_id=tenant)

Every function here is a twin of a sync one with the same name and the same
arguments, and runs the sync function itself (R1: the logic exists once):

- **on the caller's connection, inside the caller's transaction** (R2). A
  rollback in the host takes the package's writes with it, and nothing here
  commits that ``notify`` would not commit in a sync host. (``mark_read`` and the
  other feed writes commit, exactly as their sync forms do: the router relies on
  it.)
- **without blocking the loop** (R3): the sync core runs in SQLAlchemy's
  greenlet bridge and every statement is awaited on the host's async driver. No
  second engine, no second driver.
- **flushed, and returning plain values** (R4, R5): see
  :mod:`asas_notifications._bridge`. Rows come back as :class:`NotificationRecord`.

The host seams (``configure_context_resolver``, ``configure_recipient_filter``,
``configure_locale_resolver``) are called with the SYNC face of the host's
session, ``AsyncSession.sync_session``, whose ``info`` is the async session's
own ``info``. They are sync callables and may query through that session; the
query runs on the host's connection like the package's own. Context variables
set before the call are visible inside it.

The dispatcher and ``migrate`` take an ``AsyncEngine`` (``migrate`` also takes an
``AsyncConnection``) and need nothing else.

Needs SQLAlchemy's asyncio support (``asas-notifications[async]``, which is
``sqlalchemy[asyncio]``) and the host's own async driver (asyncpg, aiosqlite).
The sync package never imports this module, so a sync host installs nothing new.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Optional, Union

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from . import placement, service
from ._bridge import in_host_transaction
from .channels import adapter_for
from .models import Notification


@dataclass(frozen=True)
class NotificationRecord:
    """A notification row as plain values, safe to read after the call returns.

    Every column of ``notification``, by the same name; a test holds the two
    in step, so a column added to the table without a field here fails there
    rather than going missing in an async host."""

    id: int
    org_id: str
    user_id: str
    action: Optional[str]
    topic: Optional[str]
    importance: str
    entity_type: Optional[str]
    entity_id: Optional[str]
    title: str
    body: Optional[str]
    link: Optional[str]
    template: Optional[str]
    data: Optional[dict]
    read_at: Optional[datetime]
    archived_at: Optional[datetime]
    resolved_at: Optional[datetime]
    locale: Optional[str]
    created_at: datetime

    @classmethod
    def of(cls, row: Notification) -> "NotificationRecord":
        return cls(**{name: getattr(row, name) for name in cls.__dataclass_fields__})


def _record(row: Optional[Notification]) -> Optional[NotificationRecord]:
    return None if row is None else NotificationRecord.of(row)


def _records(rows: Iterable[Notification]) -> list[NotificationRecord]:
    return [NotificationRecord.of(r) for r in rows]


def _page(result: tuple[list[Notification], int]) -> tuple[list[NotificationRecord], int]:
    rows, total = result
    return _records(rows), total


# ── emit and routing ─────────────────────────────────────────────────────────


async def notify(
    session: AsyncSession, recipients: Iterable[Any], action: Optional[str] = None, **kwargs: Any
) -> list[NotificationRecord]:
    """Async twin of :func:`asas_notifications.notify`; same arguments. The rows
    and their outbox rows are flushed into the caller's transaction and commit
    or roll back with it."""
    return await in_host_transaction(
        session, service.notify, list(recipients), action, convert=_records, **kwargs
    )


async def resolve_channels(
    session: AsyncSession, org: Optional[Any], **kwargs: Any
) -> dict[str, bool]:
    """Async twin of :func:`asas_notifications.resolve_channels`."""
    return await in_host_transaction(session, service.resolve_channels, org, **kwargs)


# ── feed and read state ──────────────────────────────────────────────────────


async def unread_count(session: AsyncSession, user_id: Any) -> int:
    """Async twin of :func:`asas_notifications.service.unread_count`."""
    return await in_host_transaction(session, service.unread_count, user_id)


async def list_feed(
    session: AsyncSession, user_id: Any, **kwargs: Any
) -> tuple[list[NotificationRecord], int]:
    """Async twin of :func:`asas_notifications.service.list_feed`."""
    return await in_host_transaction(
        session, service.list_feed, user_id, convert=_page, **kwargs
    )


async def mark_read(
    session: AsyncSession, user_id: Any, notification_id: int
) -> Optional[NotificationRecord]:
    """Async twin of :func:`asas_notifications.service.mark_read` (commits)."""
    return await in_host_transaction(
        session, service.mark_read, user_id, notification_id, convert=_record
    )


async def mark_all_read(session: AsyncSession, user_id: Any) -> int:
    """Async twin of :func:`asas_notifications.service.mark_all_read` (commits)."""
    return await in_host_transaction(session, service.mark_all_read, user_id)


async def archive(
    session: AsyncSession, user_id: Any, notification_id: int
) -> Optional[NotificationRecord]:
    """Async twin of :func:`asas_notifications.service.archive` (commits)."""
    return await in_host_transaction(
        session, service.archive, user_id, notification_id, convert=_record
    )


async def unarchive(
    session: AsyncSession, user_id: Any, notification_id: int
) -> Optional[NotificationRecord]:
    """Async twin of :func:`asas_notifications.service.unarchive` (commits)."""
    return await in_host_transaction(
        session, service.unarchive, user_id, notification_id, convert=_record
    )


async def archive_read(session: AsyncSession, user_id: Any) -> int:
    """Async twin of :func:`asas_notifications.service.archive_read` (commits)."""
    return await in_host_transaction(session, service.archive_read, user_id)


async def current_user_id(session: AsyncSession) -> Optional[Any]:
    """Async twin of :func:`asas_notifications.service.current_user_id`."""
    return await in_host_transaction(session, service.current_user_id)


async def current_org_id(session: AsyncSession) -> Optional[Any]:
    """Async twin of :func:`asas_notifications.service.current_org_id`."""
    return await in_host_transaction(session, service.current_org_id)


# ── dispatcher ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def _begin(engine: AsyncEngine, bind: Optional[Callable[[Any], Any]]):
    """:func:`service._begin` on an async engine: the pin runs on the sync face
    of the connection, so one ``bind`` serves both dispatchers."""
    async with engine.begin() as conn:
        if bind is not None:
            await conn.run_sync(bind)
        yield conn


async def _deliver(r) -> dict:
    """:func:`service._deliver` for the loop. A sync adapter's ``send`` (SMTP, an
    HTTP client) blocks, so it runs on a worker thread; an adapter whose ``send``
    is a coroutine is awaited. The outcome is the shared :func:`service._outcome`."""
    adapter = adapter_for(r.channel)
    if adapter is None:
        return service._no_adapter()
    try:
        sent = await asyncio.to_thread(adapter.send, service._payload(r))
        if asyncio.iscoroutine(sent):
            await sent
    except Exception as exc:  # noqa: BLE001 — any send error is a retryable failure
        return service._outcome(r, exc)
    return service._outcome(r, None)


async def has_pending(conn: AsyncConnection) -> bool:
    """Async twin of :func:`asas_notifications.service.has_pending`."""
    return await conn.run_sync(service.has_pending)


async def dispatch_pending(
    engine: AsyncEngine,
    *,
    limit: int = 100,
    bind: Optional[Callable[[Any], Any]] = None,
    tenant_pinned: bool = False,
) -> int:
    """Async twin of :func:`asas_notifications.dispatch_pending`, on the host's
    own ``AsyncEngine``: the same claims, the same outcomes, the same refusal of
    a tenant-blind pass.

    ``bind`` is the same SYNC callable the sync dispatcher takes, called with the
    sync face of each transaction's connection; ``asas_tenancy.set_tenant_guc``
    works as it is::

        await notifications.dispatch_pending(
            engine, bind=lambda c: tenancy.set_tenant_guc(c, tenant, guc="procure.tenant_id")
        )
    """
    async with _begin(engine, bind) as conn:
        rows = await conn.run_sync(service._open_pass, limit, bind, tenant_pinned)

    handled = 0
    for r in rows:
        async with _begin(engine, bind) as conn:
            if not await conn.run_sync(service._claim, r.id):
                continue  # another pass owns this row
        values = await _deliver(r)
        async with _begin(engine, bind) as conn:
            await conn.run_sync(service._finish, r.id, **values)
        handled += 1
    return handled


# ── schema ───────────────────────────────────────────────────────────────────


async def migrate(
    bind: Union[AsyncEngine, AsyncConnection],
    *,
    schema: Optional[str] = placement._UNSET,
    tenant_guc: Optional[str] = None,
) -> None:
    """Async twin of :func:`asas_notifications.migrate`, with no second engine:
    the chain runs on the sync face of one of the host's own connections (R7).
    An ``AsyncEngine`` lends a connection for the run. An ``AsyncConnection``
    must not be inside a transaction, for the reason the sync form gives: the
    chain commits as it goes."""
    if isinstance(bind, AsyncConnection):
        await bind.run_sync(placement.migrate, schema=schema, tenant_guc=tenant_guc)
        return
    async with bind.connect() as conn:
        await conn.run_sync(placement.migrate, schema=schema, tenant_guc=tenant_guc)
