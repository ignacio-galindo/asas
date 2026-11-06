"""Telling Fusion's refusals apart, and what a write outbox does with each.

The texts are real refusals from a Fusion recruiting instance (October 2026),
shortened. The number is the stable part of a Fusion error: the prose is
translated and reworded, the number is not.
"""

import asyncio
from uuid import UUID

import httpx
import pytest
from asas_oracle import (
    FUSION_REFUSALS,
    KnownRefusal,
    OracleAlreadyExistsError,
    OracleAuthError,
    OracleGatewayError,
    OracleLifecycleDesyncError,
    OracleLockedError,
    OracleNotConfiguredError,
    OracleNotFoundError,
    OraclePhaseEntryRequiredError,
    OracleStateRefusedError,
    OracleUnavailableError,
    OracleUpstreamError,
    WriteAllowlist,
    WriteOutcome,
    backoff_s,
    budget_s,
    classify,
    identify,
    jitter_fraction,
    jitter_sql,
    message_codes,
    missing_fields,
)

REQUIRED = "You need to enter a value for the Candidate Selection Process field. (IRC-1590142)"
TWO_INVALID = (
    "You need to enter a valid value for the Candidate Selection Process field. (IRC-1590143) "
    "You need to enter a valid value for the External Application Flow field. (IRC-1590143)"
)
DUPLICATE = (
    "You need to enter a different number. There's already requisition number 2026-114. "
    "(IRC-1590053)"
)
NOT_ADJACENT = (
    "PhaseStepProcessor.activateStateStep()| Target state is not adjacent to current state "
    "TargetStateId=26 current state id = 27"
)
DESYNC = (
    "PhaseStepProcessor.activateStateStep()| Context current state differs from lifecycle "
    "instance. Context CurrentStateId==27, Lifecycle CurrentStateId==37"
)
LOCKED = "Failed to lock the record in table IRC_REQUISITIONS_B: another user holds the lock."


# ── message numbers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        (REQUIRED, ("IRC-1590142",)),
        (TWO_INVALID, ("IRC-1590143",)),  # each once
        (REQUIRED + " (FND-2808)", ("IRC-1590142", "FND-2808")),  # in order
        (NOT_ADJACENT, ()),  # a framework exception carries none
        ("CurrentPhaseId=5 TargetPhaseId=2", ()),  # ids with '=' are not codes
        ("", ()),
        (None, ()),
    ],
)
def test_message_codes(text, codes):
    assert message_codes(text) == codes


def test_a_refusal_names_its_codes_and_keeps_its_body_to_itself(client, oracle):
    oracle.route("/recruitingJobRequisitions/REQ-1", (400, REQUIRED))
    with pytest.raises(OracleUpstreamError) as raised:
        asyncio.run(client.patch("/recruitingJobRequisitions/REQ-1", {"StateId": 26}))
    assert raised.value.message_codes == ("IRC-1590142",)
    assert "Candidate Selection Process" not in str(raised.value)


def test_an_answer_with_no_body_has_no_codes():
    assert OracleUpstreamError("Oracle did not answer.").message_codes == ()


def test_missing_and_refused_fields_are_told_apart():
    assert missing_fields(REQUIRED) == ["Candidate Selection Process"]
    assert missing_fields(TWO_INVALID) == [], "'a valid value' is a refusal, not a missing one"
    assert missing_fields("") == []


# ── the client's classes ────────────────────────────────────────────────────


def test_fusions_own_duplicate_is_an_already_exists(client, oracle):
    """The generic words never matched Fusion Recruiting's duplicate, so the
    recover-by-reading-back path never ran on a real instance."""
    oracle.route("/recruitingJobRequisitions", (400, DUPLICATE))
    with pytest.raises(OracleAlreadyExistsError):
        asyncio.run(client.post("/recruitingJobRequisitions", {"RequisitionNumber": "2026-114"}))


def test_a_patch_with_the_duplicate_words_is_not_a_duplicate(client, oracle):
    oracle.route("/recruitingJobRequisitions/REQ-1", (400, DUPLICATE))
    with pytest.raises(OracleUpstreamError) as raised:
        asyncio.run(client.patch("/recruitingJobRequisitions/REQ-1", {"Title": "x"}))
    assert not isinstance(raised.value, OracleAlreadyExistsError)


def test_a_lifecycle_desync_is_its_own_final_class(client, oracle):
    oracle.route("/recruitingJobRequisitions/REQ-1", (400, DESYNC))
    with pytest.raises(OracleLifecycleDesyncError) as raised:
        asyncio.run(client.patch("/recruitingJobRequisitions/REQ-1", {"StateId": 26}))
    assert isinstance(raised.value, OracleStateRefusedError), "a caller of the parent still fails it"
    assert not raised.value.is_transient


def test_an_ordinary_lifecycle_refusal_is_not_a_desync(client, oracle):
    oracle.route("/recruitingJobRequisitions/REQ-1", (400, NOT_ADJACENT))
    with pytest.raises(OracleStateRefusedError) as raised:
        asyncio.run(client.patch("/recruitingJobRequisitions/REQ-1", {"StateId": 26}))
    assert not isinstance(raised.value, OracleLifecycleDesyncError)


# ── which refusal it was ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (REQUIRED, "value_required"),
        (TWO_INVALID, "value_invalid"),
        (DUPLICATE, "duplicate_requisition_number"),
        (NOT_ADJACENT, "state_not_adjacent"),
        (DESYNC, "lifecycle_desync"),
        (LOCKED, "record_locked"),
        ("Cannot move directly into new phase terminal state", "phase_entry_required"),
        ("Please select a vacant position.", "position_full"),
        ("JBO-27024: ... (IRC-1591017)", "action_not_allowed"),
    ],
)
def test_identify(text, key):
    assert identify(text).key == key


def test_the_number_wins_over_the_words():
    """A translated refusal keeps its number: the code decides, not the prose."""
    assert identify("Vous devez saisir une valeur. (IRC-1590143)").key == "value_invalid"
    assert identify("Context current state differs from lifecycle instance (IRC-1590142)").key == (
        "value_required"
    )


def test_an_unknown_refusal_is_none():
    assert identify("Something else entirely.") is None
    assert identify(None) is None


def test_a_host_extends_the_catalogue_ahead_of_the_default():
    mine = KnownRefusal("our_rule", "A host's own reading.", codes=("IRC-1590142",))
    assert identify(REQUIRED, (mine, *FUSION_REFUSALS)).key == "our_rule"
    assert identify(DUPLICATE, (mine, *FUSION_REFUSALS)).key == "duplicate_requisition_number"


def test_catalogue_keys_are_unique():
    keys = [k.key for k in FUSION_REFUSALS]
    assert len(keys) == len(set(keys))


# ── the outbox's rules ──────────────────────────────────────────────────────


def _err(cls, status=400, **kw):
    return cls("x", status=status, **kw)


@pytest.mark.parametrize(
    ("exc", "outcome", "outage"),
    [
        (OracleNotConfiguredError("off"), WriteOutcome.BLOCKED, False),
        (_err(OracleAuthError, 403), WriteOutcome.BLOCKED, False),
        (_err(OracleGatewayError, 405), WriteOutcome.BLOCKED, False),
        (_err(OracleLockedError), WriteOutcome.TRANSIENT, False),
        (OracleUnavailableError("open", retry_after_seconds=30), WriteOutcome.TRANSIENT, True),
        (_err(OracleStateRefusedError), WriteOutcome.PERMANENT, False),
        (_err(OraclePhaseEntryRequiredError), WriteOutcome.PERMANENT, False),
        (_err(OracleLifecycleDesyncError), WriteOutcome.PERMANENT, False),
        (_err(OracleNotFoundError, 404), WriteOutcome.PERMANENT, False),
        (_err(OracleAlreadyExistsError), WriteOutcome.PERMANENT, False),
        (_err(OracleUpstreamError, 400), WriteOutcome.PERMANENT, False),
        (_err(OracleUpstreamError, 503), WriteOutcome.TRANSIENT, True),
        (_err(OracleUpstreamError, 429), WriteOutcome.TRANSIENT, True),
        (_err(OracleUpstreamError, 408), WriteOutcome.TRANSIENT, True),
        (httpx.ConnectTimeout("t"), WriteOutcome.TRANSIENT, True),
        (RuntimeError("?"), WriteOutcome.TRANSIENT, False),
    ],
)
def test_classify(exc, outcome, outage):
    verdict = classify(exc)
    assert (verdict.outcome, verdict.outage) == (outcome, outage)
    assert verdict.reason == type(exc).__name__


def test_a_transport_failure_is_an_outage_and_an_unreadable_answer_is_not():
    unreachable = OracleUpstreamError("Oracle is unreachable.")
    unreachable.__cause__ = httpx.ConnectError("refused")
    assert classify(unreachable).outage is True
    unreadable = OracleUpstreamError("Oracle returned a malformed body.")
    unreadable.__cause__ = ValueError("not json")
    verdict = classify(unreadable)
    assert (verdict.outcome, verdict.outage) == (WriteOutcome.TRANSIENT, False)


def test_a_hosts_own_blocking_class():
    class SwitchedOff(Exception):
        pass

    assert classify(SwitchedOff()).outcome is WriteOutcome.TRANSIENT
    assert classify(SwitchedOff(), also_blocking=(SwitchedOff,)).outcome is WriteOutcome.BLOCKED


def test_the_verdict_never_carries_the_body():
    exc = OracleUpstreamError("Oracle answered 400.", status=400, body=REQUIRED)
    assert "Candidate Selection Process" not in classify(exc).message


ROW = UUID("6f1c2b1e-0000-4000-8000-000000000001")


def test_backoff_doubles_caps_and_jitters_down_only():
    assert backoff_s(0, ROW, base_s=60, max_s=3600) == 0.0
    steps = [backoff_s(n, ROW, base_s=60, max_s=3600) for n in range(1, 9)]
    fraction = jitter_fraction(ROW)
    assert steps[0] == pytest.approx(60 * (1 - 0.2 * fraction))
    assert steps[1] == pytest.approx(2 * steps[0])
    assert steps[-1] == pytest.approx(3600 * (1 - 0.2 * fraction)), "capped"
    assert all(0.8 * 60 <= s for s in steps)


def test_jitter_is_fixed_per_row_and_in_range():
    assert jitter_fraction(ROW) == jitter_fraction(str(ROW))
    assert 0.0 <= jitter_fraction(ROW) <= 1.0
    assert jitter_fraction(ROW) != jitter_fraction(UUID(int=7))


def test_jitter_sql_reads_the_same_digits_and_refuses_odd_names():
    assert "md5(oracle_outbox.id::text), 1, 4" in jitter_sql("oracle_outbox")
    with pytest.raises(ValueError):
        jitter_sql("t; drop table x")


def test_budget_is_the_sum_of_the_waits():
    assert budget_s(1, base_s=60, max_s=3600) == 0
    assert budget_s(4, base_s=60, max_s=3600) == 60 + 120 + 240
    assert budget_s(10, base_s=60, max_s=300) == 60 + 120 + 240 + 300 * 6


# ── the test allow-list ─────────────────────────────────────────────────────


def test_an_empty_allowlist_allows_nothing():
    assert not WriteAllowlist.parse("", "").allows(email="a@x.com", record="R1")
    assert not WriteAllowlist.parse("*", "").allows(email="a@x.com", record="R1")


def test_both_the_person_and_the_record_must_match():
    allow = WriteAllowlist.parse(" QA+*@example.com , tester@example.com", "2026-1*")
    assert allow.allows(email="qa+one@EXAMPLE.com", record="2026-114")
    assert not allow.allows(email="real.person@example.com", record="2026-114")
    assert not allow.allows(email="tester@example.com", record="2025-9")
    assert not allow.allows(email=None, record="2026-114")
    assert "address" in allow.refusal(email="x@y.z", record="2026-114")


def test_sequences_work_as_well_as_strings():
    assert WriteAllowlist.parse(["a@x.com"], ("R*",)).allows(email="a@x.com", record="R7")
