"""The one way an async call reaches the sync core (DR 0005 §5.2).

``AsyncSession.run_sync`` hands the sync core the session's own sync face, bound
to the caller's connection and transaction: the package's statements run where
the host's do, under whatever the host bound on that connection (its tenant pin,
its ``SET LOCAL`` settings), and SQLAlchemy's greenlet bridge awaits every one of
them on the async driver, so the event loop is never blocked.

Two traps every hand-written bridge has fallen into are closed here, once:

1. **Flush before control returns** (R5). ``notify`` flushes once for the
   notification ids and then adds the delivery rows, leaving the last flush to
   the caller's unit of work. Across a bridge the caller may never flush that
   session again, and then the notification commits with no outbox rows: no
   email, no error, nothing to retry.
2. **Only plain values come back** (R4). ``convert`` runs inside the bridge,
   while the session is live, and turns ORM rows into frozen records. A row read
   after the bridge returns may be expired, and a lazy load from outside the
   greenlet raises rather than querying.

This module opens no transaction and commits nothing; the host owns both, as it
does for a sync call. It is private and per package for now: the repository's
rule is that a shared kernel appears when a third package repeats identical code
(see DR 0005, open question 1).
"""

from typing import Any, Callable, Optional, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


async def in_host_transaction(
    session: AsyncSession,
    fn: Callable[..., Any],
    /,
    *args: Any,
    convert: Optional[Callable[[Any], T]] = None,
    **kwargs: Any,
) -> Any:
    """Run ``fn(sync_session, *args, **kwargs)`` on the caller's connection,
    flush, and return ``convert(result)`` (or the result as it is)."""

    def call(sync_session):
        result = fn(sync_session, *args, **kwargs)
        sync_session.flush()
        return convert(result) if convert is not None else result

    return await session.run_sync(call)
