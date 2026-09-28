"""Hash-chain primitives. Pure: no database, no session, unit-testable.

Each entry carries the fingerprint of the one before it, so the log is a chain
and any alteration to a link shows up as a divergence from that point on. Three
kinds of tampering are detectable and all three matter: an entry that was
**edited**, an entry that was **deleted**, and an entry **inserted** after the
fact to look original.

**The writer and the verifier share one canonical encoding per row, which is why
they live in one file.** A writer and a verifier that disagree about a row's
encoding is how a chain reports breaks that are not there, and a tamper-evidence
tool that cries wolf gets switched off, which is strictly worse than not having
one. So this module owns the exact dict that gets
hashed (:func:`chain_payload`), the exact bytes it becomes
(:func:`canonical_bytes`), and both sides of the arithmetic. Nothing else in the
package, and nothing in a host, may compose either.

    hash_current = sha256(hash_prev || canonical_json(chain_payload))

On verify the payload is rebuilt from the stored columns, so a row that was
changed in place no longer hashes to what it says it does.

**Every row names the encoding that produced its hash**, and the verifier
rebuilds each row with that row's own encoding. New rows always use
:data:`CURRENT_ENCODING`. The point is adoption: a host that already keeps a
hash chain of its own, written by code this package was extracted from and has
since diverged from, can hand that chain over without rewriting a single stored
fingerprint (which the append-only trigger would refuse anyway). It registers a
:class:`ChainEncoding` that reproduces its old bytes, labels its old rows with
that name, and from then on the package appends to the same chain.

An encoding is **declarative on purpose**: it can rename the keys of the hashed
dict and choose how a timestamp is spelled, and nothing else. The JSON rules
(:func:`canonical_bytes`) and the arithmetic (:func:`compute_hash`) are shared by
every encoding, so a registered encoding cannot quietly weaken what the chain
proves. A host whose old bytes differ in some other way is a case for a new
encoding in this module, not for a hook that lets a host compose the bytes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The one encoding, used by both the writer and the verifier.

    ``sort_keys`` because a dict's insertion order is not part of its meaning and
    two processes must not encode the same facts differently. Tight separators so
    the bytes do not depend on a formatting default. ``default=str`` so a value
    the host put in the payload cannot make the hash undefined; it is a fallback,
    not a licence to store exotic objects.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()


def canonical_timestamp(value: datetime) -> str:
    """The one spelling of a moment, and it has to be one spelling.

    **Found by running the suite on both engines.** The hash covers
    ``occurred_at``, so the writer's rendering of it and the verifier's rendering
    of the value read back must be identical, and they were not: Postgres returns
    a ``timestamptz`` as an aware datetime, SQLite has no timezone type at all and
    returns a naive one. The same row therefore hashed one way on write and
    another on verify, and the whole chain reported as tampered with on SQLite
    while passing on Postgres.

    A naive value is read as UTC rather than as local time, which is the only
    choice that is stable: local time depends on the process, so the same row
    would hash differently on two machines, and it is what SQLite gives back for
    a value that was UTC when it went in.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


#: The encoding this package writes. Its bytes are exactly those of 0.1.0,
#: which had one encoding and no name for it.
CURRENT_ENCODING = "asas-audit/1"

#: The fields every encoding hashes, under the names the current encoding uses.
#: An encoding may rename them; it cannot add or drop one.
CHAIN_FIELDS = (
    "id", "org_id", "actor", "action", "resource_type", "resource_id",
    "payload", "occurred_at",
)

#: The width of the ``encoding`` column, so a name that cannot be stored is
#: refused at registration rather than at the first insert.
_NAME_MAX = 32


class UnknownEncodingError(LookupError):
    """A row names an encoding this process has not registered.

    Raised rather than reported as a break, because it is a configuration fault
    and not evidence of tampering: the host forgot to register the encoding its
    old rows use. Reporting it as a break would make a whole adopted history look
    rewritten, which is the false alarm that gets a verifier switched off.
    """


@dataclass(frozen=True)
class ChainEncoding:
    """One way of turning a row into the bytes that get hashed.

    ``keys`` renames fields of the hashed dict, from the names in
    :data:`CHAIN_FIELDS` to the names a legacy writer used (``{"org_id":
    "tenant_id"}``). ``timestamp`` spells ``occurred_at``; keep the default
    unless the legacy writer's spelling differs for values this package reads
    back, because the default is the one that survives a driver returning the
    moment in a different zone (see :func:`canonical_timestamp`).

    ``timestamp`` should be a module-level function rather than a lambda: two
    registrations of one name are accepted only when they are equal, and two
    lambdas never are.
    """

    name: str
    keys: Mapping[str, str] = field(default_factory=dict)
    timestamp: Callable[[datetime], str] = canonical_timestamp

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > _NAME_MAX:
            raise ValueError(
                f"encoding name {self.name!r} must be 1 to {_NAME_MAX} characters, "
                f"the width of the column it is stored in"
            )
        keys = dict(self.keys)
        unknown = sorted(set(keys) - set(CHAIN_FIELDS))
        if unknown:
            raise ValueError(
                f"encoding {self.name!r} renames {unknown}, which are not chain "
                f"fields; the fields are {list(CHAIN_FIELDS)}"
            )
        final = [keys.get(f, f) for f in CHAIN_FIELDS]
        if len(set(final)) != len(final):
            raise ValueError(
                f"encoding {self.name!r} maps two fields to one key: {final}"
            )
        object.__setattr__(self, "keys", keys)

    def payload(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Rename the current-encoding dict into this encoding's keys."""
        return {self.keys.get(k, k): v for k, v in fields.items()}


_REGISTRY: dict[str, ChainEncoding] = {}


def register_encoding(encoding: ChainEncoding) -> ChainEncoding:
    """Make ``encoding`` available to the verifier under its name.

    Call it at import or boot time, before :func:`asas_audit.verify` runs.
    Registering the same definition twice is harmless; a **different** definition
    under a taken name is refused, because the name is what the rows store, and
    two definitions for one name would make old rows verify under whichever
    happened to register last.
    """
    existing = _REGISTRY.get(encoding.name)
    if existing is not None and existing != encoding:
        raise ValueError(
            f"chain encoding {encoding.name!r} is already registered with a "
            f"different definition ({existing!r}); rows store the name, so one "
            f"name must mean one set of bytes"
        )
    _REGISTRY[encoding.name] = encoding
    return encoding


def get_encoding(name: str) -> ChainEncoding:
    """The registered encoding called ``name``, or :class:`UnknownEncodingError`."""
    try:
        return _REGISTRY[name]
    except (KeyError, TypeError):
        raise UnknownEncodingError(
            f"no chain encoding named {name!r} is registered (registered: "
            f"{sorted(_REGISTRY)}). A host adopting a legacy chain registers its "
            f"encoding with asas_audit.register_encoding before verifying."
        ) from None


register_encoding(ChainEncoding(CURRENT_ENCODING))


def chain_payload(
    *,
    event_id: Any,
    org_id: Any,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: Any,
    payload: dict[str, Any],
    occurred_at: datetime,
    encoding: str = CURRENT_ENCODING,
) -> dict[str, Any]:
    """Exactly what gets hashed. The single source of truth for the encoding.

    Every field is stringified rather than left to the JSON encoder's own idea of
    an int or a UUID, so a host that keys tenants by integer and one that keys
    them by UUID produce the same shape, and a column type change does not
    invalidate an existing chain.

    ``occurred_at`` goes through the encoding's timestamp formatter, which for
    the current encoding is :func:`canonical_timestamp`, for the reason that
    function explains.

    Adding a field here **breaks every existing chain**, because the rebuilt
    payload of an old row would no longer match its stored hash. If a field ever
    has to join it, that is a new named encoding, so the rows written before it
    keep verifying under the one they were written with.
    """
    enc = get_encoding(encoding)
    return enc.payload({
        "id": str(event_id),
        "org_id": str(org_id),
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "payload": payload,
        "occurred_at": enc.timestamp(occurred_at),
    })


def compute_hash(hash_prev: Optional[bytes], payload: dict[str, Any]) -> bytes:
    """The link. ``hash_prev`` is ``None`` for the first entry in a chain."""
    return hashlib.sha256((hash_prev or b"") + canonical_bytes(payload)).digest()


@dataclass(frozen=True)
class ChainBreak:
    """One divergence, with both hashes so a reader can see it rather than take
    it on trust."""

    event_id: Any
    seq: int
    action: str
    expected_hash_hex: str
    stored_hash_hex: str
    #: Why this row diverged, in words. A break at the FIRST bad row is the
    #: interesting one; the rows after it usually diverge as a consequence.
    detail: str = ""
    #: The encoding the row names, which is the one it was recomputed with.
    encoding: str = CURRENT_ENCODING


@dataclass(frozen=True)
class VerifyReport:
    """The verdict for one tenant's chain."""

    org_id: Any
    events_checked: int
    breaks: tuple[ChainBreak, ...]

    @property
    def is_intact(self) -> bool:
        return not self.breaks

    @property
    def first_break(self) -> Optional[ChainBreak]:
        """The row where the history stops adding up, which is the one to look at.

        Everything after a break usually diverges too, as a consequence rather
        than as separate evidence, so a report of forty breaks is normally one
        alteration and thirty-nine echoes.
        """
        return self.breaks[0] if self.breaks else None


def verify_rows(org_id: Any, rows: Sequence[Any]) -> VerifyReport:
    """Re-derive the chain over ``rows``, which must be ordered by ``seq`` ascending.

    ``rows`` are anything with the stored columns as attributes (the package's own
    ORM row, a plain namedtuple, a test double). Keeping it structural is what
    makes this function testable without a database, which in turn is what makes
    the tamper cases cheap to assert.

    Two checks per row, and both are needed:

    * the stored ``hash_prev`` must equal the previous row's ``hash_current``,
      which is what catches a **deleted** row (the survivors' links no longer
      meet) and an **inserted** one;
    * the stored ``hash_current`` must equal the hash recomputed from the row's
      own columns, which is what catches an **edited** row.

    Each row is recomputed with the encoding it names (``row.encoding``; a row
    object without that attribute is read as :data:`CURRENT_ENCODING`). A chain
    may change encoding part way, which is what an adopted history looks like:
    legacy rows first, then this package's, the first of which links to the last
    legacy fingerprint like any other row. A name that is not registered raises
    :class:`UnknownEncodingError` instead of reporting a break.
    """
    breaks: list[ChainBreak] = []
    prev_hash: Optional[bytes] = None
    for row in rows:
        encoding = getattr(row, "encoding", CURRENT_ENCODING)
        try:
            get_encoding(encoding)
        except UnknownEncodingError as exc:
            raise UnknownEncodingError(
                f"row seq={row.seq} id={row.id}: {exc}"
            ) from None
        expected = compute_hash(
            prev_hash,
            chain_payload(
                event_id=row.id,
                org_id=org_id,
                actor=row.actor,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                payload=row.payload,
                occurred_at=row.occurred_at,
                encoding=encoding,
            ),
        )
        stored = bytes(row.hash_current)
        stored_prev = bytes(row.hash_prev) if row.hash_prev is not None else None

        if stored_prev != prev_hash:
            breaks.append(
                _break(row, expected, stored,
                       "does not link to the previous entry: a row was deleted, "
                       "inserted, or reordered")
            )
        elif expected != stored:
            breaks.append(
                _break(row, expected, stored,
                       "its own columns no longer hash to its stored fingerprint: "
                       "the row was edited")
            )
        # Chain off what is STORED rather than what was expected, so one bad row
        # does not necessarily invalidate every row after it: a single edited
        # entry then reports as one break rather than as a cascade.
        prev_hash = stored
    return VerifyReport(org_id=org_id, events_checked=len(rows), breaks=tuple(breaks))


def _break(row: Any, expected: bytes, stored: bytes, detail: str) -> ChainBreak:
    return ChainBreak(
        event_id=row.id,
        seq=int(row.seq),
        action=row.action,
        expected_hash_hex=expected.hex(),
        stored_hash_hex=stored.hex(),
        detail=detail,
        encoding=getattr(row, "encoding", CURRENT_ENCODING),
    )
