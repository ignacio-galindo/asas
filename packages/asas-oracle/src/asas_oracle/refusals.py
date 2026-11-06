"""Which refusal it was: a catalogue of the refusals Fusion is known to make.

Fusion's errors arrive as prose, sometimes with a message number in it ("You
need to enter a value for the Candidate Selection Process field.
(IRC-1590142)") and sometimes as a bare Java trace from its lifecycle engine
("PhaseStepProcessor.activateStateStep()| Context current state differs from
lifecycle instance"). The error CLASS the client raises says what to DO (retry,
park, fail); this says WHICH refusal it was, as a stable key a host can log,
count, put on an outbox row or map to its own words.

Each entry names:

* ``key``:     a stable name for it;
* ``codes``:   Fusion's message numbers for it, matched FIRST (the number is
               neither translated nor reworded, the prose is both);
* ``markers``: lower-cased phrases from Fusion's words, for the refusals that
               carry no number (the lifecycle engine's never do);
* ``meaning``: what it means, in plain English;
* ``seen``:    where it was measured.

Order matters: the first entry that matches wins, so a specific refusal sits
above a general one that would also match it. A host extends the catalogue by
passing its own entries ahead of :data:`FUSION_REFUSALS`.

Only the KEY and the codes are safe to show. The body a refusal is read from
never is (see :class:`asas_oracle.OracleUpstreamError`).

The full list lives in Fusion's message dictionary,
``GET /fscmRestApi/resources/<version>/messages?q=ApplicationShortName='IRC'``,
which an integration user can read only with "Manage Messages". The lifecycle
engine's refusals are not in that dictionary at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .errors import message_codes


@dataclass(frozen=True)
class KnownRefusal:
    key: str
    meaning: str
    seen: str = ""
    codes: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()


_RECRUITING = "a Fusion recruiting instance, Oct 2026"

#: The refusals measured on Fusion so far. Generic Fusion facts: what the
#: instance said and what it meant, never what a product does about it.
FUSION_REFUSALS: tuple[KnownRefusal, ...] = (
    # ── the lifecycle engine (no message numbers) ──────────────────────────
    KnownRefusal(
        "lifecycle_desync",
        "The record and Fusion's lifecycle instance disagree on its state; every "
        "status write is refused until a person walks the record back with its "
        "actions (reopen, suspend). Caused by posting a suspended requisition.",
        _RECRUITING,
        markers=("differs from lifecycle instance",),
    ),
    KnownRefusal(
        "phase_entry_required",
        "A terminal state was asked for in a phase the record is not in; entering "
        "the phase at a non-terminal state first lets it through.",
        _RECRUITING,
        markers=("cannot move directly into new phase terminal state",),
    ),
    KnownRefusal(
        "phase_not_adjacent",
        "The target phase cannot be reached from the record's phase in one step.",
        _RECRUITING,
        markers=("target phase is not adjacent to current phase",),
    ),
    KnownRefusal(
        "state_not_adjacent",
        "The target state cannot be reached from the record's state in one step.",
        _RECRUITING,
        markers=("not adjacent to current state",),
    ),
    KnownRefusal(
        "state_not_available",
        "The target state is not one a status write can set (it is owned by an "
        "action such as posting).",
        _RECRUITING,
        markers=("target state is not available",),
    ),
    KnownRefusal(
        "cannot_move_further",
        "The record sits in Fusion's own approval and a status write cannot move "
        "it from there.",
        _RECRUITING,
        markers=("cannot be moved further",),
    ),
    # ── Recruiting's numbered messages (IRC) ───────────────────────────────
    KnownRefusal(
        "duplicate_number",
        "A record with that number exists already: on a retried create, the "
        "first attempt landed.",
        _RECRUITING,
        codes=("IRC-1590053",),
        markers=("already requisition number", "enter a different number"),
    ),
    KnownRefusal(
        "value_required",
        "A value Fusion requires was not sent; `missing_fields` names it.",
        _RECRUITING,
        codes=("IRC-1590142",),
        markers=("enter a value for the",),
    ),
    KnownRefusal(
        "value_invalid",
        "A value Fusion will not take for this record (often one the instance "
        "configures per organisation or type); `refused_fields` names it.",
        _RECRUITING,
        codes=("IRC-1590143",),
        markers=("enter a valid value for the",),
    ),
    KnownRefusal(
        "action_not_allowed",
        "A lifecycle action (submit, cancel, reopen, ...) is not possible from "
        "the record's current state, or the user lacks it.",
        _RECRUITING,
        codes=("IRC-1591017",),
    ),
    KnownRefusal(
        "redraft_while_posted",
        "A posted record cannot be redrafted; unpost it first.",
        _RECRUITING,
        codes=("IRC-1590067",),
    ),
    # ── record-level refusals without a number ─────────────────────────────
    KnownRefusal(
        "record_locked",
        "Somebody has the record open in Fusion; the same write lands once they "
        "let go (transient).",
        _RECRUITING,
        markers=("another user holds the lock", "failed to lock the record"),
    ),
    KnownRefusal(
        "position_full",
        "The position has no vacancy left (its headcount equals its incumbents).",
        _RECRUITING,
        markers=("please select a vacant position",),
    ),
    KnownRefusal(
        "position_on_create",
        "Fusion's Position_Occupied rule fails when a position rides a create; "
        "attach it with a PATCH afterwards.",
        _RECRUITING,
        markers=("position_occupied",),
    ),
    KnownRefusal(
        "lov_no_match",
        "An id was sent that is not in the field's list of values on this "
        "instance.",
        _RECRUITING,
        markers=("no matching row",),
    ),
)


def identify(
    text: str | None, catalogue: Iterable[KnownRefusal] = FUSION_REFUSALS
) -> KnownRefusal | None:
    """The catalogue entry a refusal is, or ``None``.

    By message number first (Fusion's numbers are stable where its prose is
    not), then by phrase; within each pass the first entry wins. Pass the
    refusal's ``body`` (``identify(exc.body)``); the answer's ``key`` is safe
    to log, the body never is."""
    entries = tuple(catalogue)
    codes = set(message_codes(text))
    if codes:
        for known in entries:
            if codes & set(known.codes):
                return known
    lowered = " ".join((text or "").split()).lower()
    for known in entries:
        if any(marker in lowered for marker in known.markers):
            return known
    return None
