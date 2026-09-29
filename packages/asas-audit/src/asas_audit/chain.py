"""Hash-chain primitives. Pure: no database, no clock, unit-testable alone.

The writer and the verifier MUST share one canonical encoding, which is why
both live here and nowhere else::

    hash_current = sha256(hash_prev_hex || canonical_json(chain_payload))

The chain payload is rebuilt from STORED columns on verify. A row edited,
deleted or inserted out of band therefore re-derives a different hash at that
row, and every later row inherits the break.

Two normalizations make "rebuilt from stored columns" actually true on every
engine (they are the reason a naive port of this pattern reports false breaks):

* **Time** is hashed as NAIVE UTC with microseconds. SQLite drops tzinfo on the
  round trip and a Postgres ``timestamp`` column has none, so an aware value
  hashed on write and a naive one read back would never agree.
* **The payload** is stored as its own canonical JSON, decoded. A ``datetime``,
  ``UUID`` or ``Decimal`` in the caller's dict is hashed through ``default=str``;
  storing the raw dict would either fail (JSON columns refuse those types) or
  come back as something that hashes differently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


def canonical_bytes(value: Any) -> bytes:
    """The one encoding every hash is taken over: sorted keys, no whitespace,
    and anything JSON cannot say rendered through ``str``."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def normalize_payload(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The payload exactly as it will be stored AND hashed (see module doc)."""
    return json.loads(canonical_bytes(payload or {}))


def normalize_time(moment: datetime) -> datetime:
    """Naive UTC, which is what every engine hands back."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def chain_payload(
    *,
    event_id: str,
    org_id: str,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> dict[str, Any]:
    """The exact dict that is hashed. Single source of truth for writer and verifier."""
    return {
        "id": event_id,
        "org_id": org_id,
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "payload": payload,
        "occurred_at": normalize_time(occurred_at).isoformat(timespec="microseconds"),
    }


def compute_hash(hash_prev: Optional[str], payload: dict[str, Any]) -> str:
    """Hex sha256 of the previous hash (hex, or nothing for the first event)
    followed by the canonical payload."""
    return hashlib.sha256((hash_prev or "").encode() + canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class ChainBreak:
    """One place the stored chain disagrees with itself.

    ``reason`` is a plain sentence: ``"hash mismatch"`` (the row's content was
    changed), ``"broken link"`` (a row before it was removed or inserted),
    or ``"tail truncated"`` (rows after the last one checked were removed, or
    the head was rewritten).
    """

    seq: Optional[int]
    event_id: Optional[str]
    reason: str
    expected: str
    stored: str


@dataclass(frozen=True)
class VerifyReport:
    org_id: str
    events_checked: int
    breaks: tuple[ChainBreak, ...]

    @property
    def is_intact(self) -> bool:
        return not self.breaks

    def as_sentence(self) -> str:
        scope = f"organisation {self.org_id!r}" if self.org_id else "the platform"
        if self.is_intact:
            return f"The audit log for {scope} is intact across {self.events_checked} events."
        first = self.breaks[0]
        where = f"event {first.seq}" if first.seq is not None else "the end of the log"
        return (
            f"The audit log for {scope} has {len(self.breaks)} break(s); the first is a "
            f"{first.reason} at {where}."
        )


def verify_rows(
    org_id: str,
    rows: Iterable[Any],
    *,
    head_hash: Optional[str] = None,
    head_events: Optional[int] = None,
) -> VerifyReport:
    """Re-derive the chain over ``rows`` in ``seq`` order.

    ``rows`` carry ``seq, event_id, actor, action, resource_type, resource_id,
    payload, occurred_at, hash_prev, hash_current``. The optional head is the
    chain's own record of how long it is and where it ends: without it,
    deleting the LAST events leaves a shorter chain that is still internally
    consistent, and nothing would notice.
    """
    breaks: list[ChainBreak] = []
    prev: Optional[str] = None
    count = 0
    for row in rows:
        count += 1
        expected = compute_hash(
            prev,
            chain_payload(
                event_id=row.event_id,
                org_id=org_id,
                actor=row.actor,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                payload=row.payload,
                occurred_at=row.occurred_at,
            ),
        )
        if (row.hash_prev or None) != prev:
            breaks.append(ChainBreak(row.seq, row.event_id, "broken link", prev or "", row.hash_prev or ""))
        elif expected != row.hash_current:
            breaks.append(ChainBreak(row.seq, row.event_id, "hash mismatch", expected, row.hash_current))
        prev = row.hash_current
    if head_events is not None and (head_events != count or (head_hash or None) != prev):
        breaks.append(
            ChainBreak(None, None, "tail truncated", f"{head_events}:{head_hash or ''}", f"{count}:{prev or ''}")
        )
    return VerifyReport(org_id=org_id, events_checked=count, breaks=tuple(breaks))
