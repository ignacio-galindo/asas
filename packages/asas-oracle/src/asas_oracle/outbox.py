"""What a write outbox does with each answer Fusion gives it.

A host that sends Fusion writes from an outbox (a table of pending writes and a
sweep that sends them) has to decide, for every failed send, whether to try
again, wait for the environment to be fixed, or give up on the row. This module
is that decision, as pure functions over this package's exceptions: no
database, no I/O, so the rules can be read and tested on their own.

Four outcomes for a send that raised:

``transient``  Nothing is wrong with the RECORD or the deployment: the call
               could not land right now (a timeout, a 5xx, a 429, the breaker,
               a record somebody has open). Count the attempt, back off, retry.
``blocked``    The ENVIRONMENT refuses every write of this kind (Oracle not
               configured, credentials refused, a gateway that does not carry
               the operation). Not the row's fault, so it should cost the row
               no attempt; re-check on a slow clock.
``permanent``  Fusion answered about THIS record and said no. The same body
               gets the same answer, so fail the row at once for a person.

(``sent`` is the fourth, for a host that records the outcome of every send.)

Anything unrecognised is TRANSIENT: the attempt budget bounds it, and an
unknown fault is likelier a passing one than a verdict on the record.

Backoff is exponential, capped, and jittered by a FIXED fraction per row
(:func:`jitter_fraction`), so a row's due time is the same on every pass and a
database can compute it too (:func:`jitter_sql` for Postgres).
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import httpx

from .errors import (
    OracleAlreadyExistsError,
    OracleAuthError,
    OracleGatewayError,
    OracleLockedError,
    OracleNotConfiguredError,
    OracleNotFoundError,
    OracleStateRefusedError,
    OracleUnavailableError,
    OracleUpstreamError,
)


class WriteOutcome(enum.StrEnum):
    SENT = "sent"
    TRANSIENT = "transient"
    BLOCKED = "blocked"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class Verdict:
    outcome: WriteOutcome
    #: The exception's class name, for a log line or a status page.
    reason: str
    #: The exception's own message, never Fusion's body.
    message: str
    #: The fault is Oracle's or the network's as a whole (a 5xx, a timeout, the
    #: breaker, a 429): a sweep should stop its pass rather than spend an
    #: attempt of every other row on the same outage. A locked record is about
    #: one row and is not an outage.
    outage: bool = False


#: The statuses that mean "try again later" outside the 5xx range.
TRANSIENT_STATUSES = frozenset({408, 425, 429})

#: The classes that mean the deployment, not the record, is refusing.
ENVIRONMENT_BLOCKING: tuple[type[BaseException], ...] = (
    OracleNotConfiguredError,
    OracleAuthError,
    OracleGatewayError,
)


def _message(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def classify(
    exc: BaseException, *, also_blocking: Sequence[type[BaseException]] = ()
) -> Verdict:
    """The outcome of one send that raised ``exc``.

    By exception TYPE first, then, for the generic upstream error, by its
    status. ``also_blocking`` names a host's OWN exceptions that mean "this
    environment cannot send this yet" (a test allow-list refusing, a feature
    switched off), so a host keeps its rules beside these ones."""
    reason = type(exc).__name__
    message = _message(exc)
    if isinstance(exc, (*ENVIRONMENT_BLOCKING, *also_blocking)):
        return Verdict(WriteOutcome.BLOCKED, reason, message)
    if isinstance(exc, OracleLockedError):
        return Verdict(WriteOutcome.TRANSIENT, reason, message)
    if isinstance(exc, OracleUnavailableError):
        return Verdict(WriteOutcome.TRANSIENT, reason, message, outage=True)
    if isinstance(exc, (OracleStateRefusedError, OracleNotFoundError, OracleAlreadyExistsError)):
        # A lifecycle verdict, a record Oracle no longer has, or a key that
        # collided with something that is not ours: no retry changes them. (A
        # host that mints its keys reads the record back on AlreadyExists
        # BEFORE classifying; only an unrecovered one reaches here.)
        return Verdict(WriteOutcome.PERMANENT, reason, message)
    if isinstance(exc, OracleUpstreamError):
        status = exc.status
        if status is None:
            # No response: a transport failure is an outage; a 2xx we could
            # not read may have landed, and a sweep's writes are safe to resend.
            outage = isinstance(exc.__cause__, (httpx.TransportError, TimeoutError, ConnectionError))
            return Verdict(WriteOutcome.TRANSIENT, reason, message, outage=outage)
        if status >= 500 or status in TRANSIENT_STATUSES:
            return Verdict(WriteOutcome.TRANSIENT, reason, message, outage=True)
        return Verdict(WriteOutcome.PERMANENT, reason, message)
    if isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError)):
        return Verdict(WriteOutcome.TRANSIENT, reason, message, outage=True)
    return Verdict(WriteOutcome.TRANSIENT, reason, message)


#: How far the jitter pulls a delay down: a row waits between 80% and 100% of
#: its step, so rows that failed together do not all come due together.
JITTER_SPREAD = 0.2


def jitter_fraction(row_id: UUID | str) -> float:
    """A fixed fraction in [0, 1] per row, from its id.

    Fixed rather than random so the row's due time is the same on every pass
    and can be computed by the database: :func:`jitter_sql` reads the same four
    hex digits of ``md5(id::text)``."""
    digest = hashlib.md5(str(row_id).encode(), usedforsecurity=False).hexdigest()
    return int(digest[:4], 16) / 65535.0


def jitter_sql(table: str) -> str:
    """:func:`jitter_fraction` as Postgres computes it for ``table``'s rows
    (``table.id``). ``table`` is an identifier the HOST writes, never input."""
    if not table.replace("_", "").replace(".", "").isalnum():
        raise ValueError(f"not a plain table name: {table!r}")
    return f"(('x' || substr(md5({table}.id::text), 1, 4))::bit(16)::int / 65535.0)"


def backoff_s(attempts: int, row_id: UUID | str, *, base_s: float, max_s: float) -> float:
    """Seconds a pending row waits after its last attempt.

    Zero before the first attempt; then ``base * 2^(attempts-1)``, capped at
    ``max_s``, less up to :data:`JITTER_SPREAD` of itself."""
    if attempts <= 0:
        return 0.0
    step = min(float(base_s) * (2 ** (attempts - 1)), float(max_s))
    return step * (1.0 - JITTER_SPREAD * jitter_fraction(row_id))


def budget_s(max_attempts: int, *, base_s: float, max_s: float) -> float:
    """The longest a row keeps being retried before it is failed: the sum of
    the waits between its attempts, with no jitter (the upper bound)."""
    return sum(
        min(float(base_s) * (2 ** (n - 1)), float(max_s)) for n in range(1, max(1, max_attempts))
    )
