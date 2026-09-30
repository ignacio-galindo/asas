"""Asas sync: mirror a remote paginated collection into your own tables, incrementally.

For any product that keeps a local copy of an ERP, HRIS or CRM collection (to
list, search and join it without asking the remote each time). The remote is
behind :class:`RemoteCollection`, the rows are yours (``upsert``,
``on_deleted``), and this package owns the cursor, the seen-marks and the rules
that make the copy trustworthy: walk orders that survive unstable tie order,
the offset ceiling, a watermark that never passes the walk's start, deletion
only after two misses (verified by key where the walk is not exact), resumable
capped passes, and one pass per collection at a time.

Extracted from the ad-recruiter platform's Oracle Fusion thin index (D303/D309).

Host contract (table-owning variant, router-less, no seed):

- :func:`migrate` - the package-owned Alembic chain.
- :func:`run_pass` / :func:`reconcile` - async; take the host's session
  factory, so each page commits in its own short transaction.
- :func:`cursor_status` - the cursor row, for an admin card.
"""

from .engine import (
    PassResult,
    ReconcileResult,
    RemoteCollection,
    RemotePage,
    SyncBusyError,
    SyncSpec,
    SyncStuckError,
    cursor_status,
    reconcile,
    run_pass,
)
from .migrate import migrate
from .models import SyncCursor, SyncSeen

__version__ = "0.1.0"

__all__ = [
    "PassResult",
    "ReconcileResult",
    "RemoteCollection",
    "RemotePage",
    "SyncBusyError",
    "SyncCursor",
    "SyncSeen",
    "SyncSpec",
    "SyncStuckError",
    "cursor_status",
    "migrate",
    "reconcile",
    "run_pass",
    "__version__",
]
