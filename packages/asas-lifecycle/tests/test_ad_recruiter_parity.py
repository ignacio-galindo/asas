"""The five status machines the AI Recruiter carried, expressed with this package.

Each ``*_TABLE`` below is a verbatim copy of that product's hand-rolled
``_TRANSITIONS`` dict (``modules/domain/<entity>/domain/state_machine.py``),
written against a local copy of its enum. Each ``*_LIFECYCLE`` is the same
machine declared with ``asas_lifecycle``, with the actions the product's docs
name. The tests assert that, for every ordered pair of states, the lifecycle
allows exactly what the table allowed and refuses exactly what it refused, and
that "terminal" means the same thing in both. The product is not imported.
"""

from enum import StrEnum

import pytest

from asas_lifecycle import Edge, InvalidTransition, Lifecycle, guard

# ── requisition ─────────────────────────────────────────────────────────────


class RequisitionStatus(StrEnum):
    NEW = "new"
    AWAITING_APPROVAL = "awaiting_approval"
    RETURNED = "returned"
    ACTIVE = "active"
    CLOSED = "closed"
    CANCELLED = "cancelled"


R = RequisitionStatus
REQUISITION_TABLE = {
    R.NEW: frozenset({R.AWAITING_APPROVAL, R.ACTIVE, R.CANCELLED}),
    R.AWAITING_APPROVAL: frozenset({R.ACTIVE, R.RETURNED, R.CANCELLED}),
    R.RETURNED: frozenset({R.AWAITING_APPROVAL, R.ACTIVE, R.CANCELLED}),
    R.ACTIVE: frozenset({R.NEW, R.CLOSED}),
    R.CLOSED: frozenset(),
    R.CANCELLED: frozenset(),
}

REQUISITION_LIFECYCLE = Lifecycle(
    "requisition",
    RequisitionStatus,
    initial=R.NEW,
    terminal={R.CLOSED, R.CANCELLED},
    transitions=[
        Edge(R.NEW, R.AWAITING_APPROVAL, "submit"),              # F6
        Edge(R.NEW, R.ACTIVE, "activate"),                       # R-08, R2
        Edge(R.AWAITING_APPROVAL, R.ACTIVE, "approve"),          # F8
        Edge(R.AWAITING_APPROVAL, R.RETURNED, "return"),         # F9
        Edge(R.RETURNED, R.AWAITING_APPROVAL, "submit"),         # F9 resubmit
        Edge(R.RETURNED, R.ACTIVE, "activate"),                  # Chapter 2
        # F10 / R-15: cancelled only from the three pre-live states. One edge.
        Edge((R.NEW, R.AWAITING_APPROVAL, R.RETURNED), R.CANCELLED, "cancel"),
        Edge(R.ACTIVE, R.NEW, "revert"),                         # R3
        Edge(R.ACTIVE, R.CLOSED, "close"),                       # F11, R4
    ],
    register=False,
)

# ── job ─────────────────────────────────────────────────────────────────────


class JobStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


J = JobStatus
JOB_TABLE = {
    J.DRAFT: frozenset({J.ACTIVE, J.CLOSED}),
    J.ACTIVE: frozenset({J.DRAFT, J.CLOSED}),
    J.CLOSED: frozenset(),
}

JOB_LIFECYCLE = Lifecycle(
    "job",
    JobStatus,
    initial=J.DRAFT,
    terminal=J.CLOSED,
    transitions=[
        (J.DRAFT, J.ACTIVE, "publish"),      # J2
        (J.DRAFT, J.CLOSED, "discard"),      # J6
        (J.ACTIVE, J.DRAFT, "unpublish"),    # J3
        (J.ACTIVE, J.CLOSED, "discard"),     # J4
        (J.ACTIVE, J.CLOSED, "hire"),        # J5: a second named edge, same pair
    ],
    register=False,
)

# ── application ─────────────────────────────────────────────────────────────


class ApplicationStage(StrEnum):
    ACTIVE = "active"
    OFFER_EXTENDED = "offer_extended"
    CLOSED = "closed"


A = ApplicationStage
APPLICATION_TABLE = {
    A.ACTIVE: frozenset({A.OFFER_EXTENDED, A.CLOSED}),
    A.OFFER_EXTENDED: frozenset({A.ACTIVE, A.CLOSED}),
    A.CLOSED: frozenset(),
}

APPLICATION_LIFECYCLE = Lifecycle(
    "application",
    ApplicationStage,
    initial=A.ACTIVE,
    terminal=A.CLOSED,
    transitions=[
        (A.ACTIVE, A.OFFER_EXTENDED, "extend_offer"),   # A2
        (A.OFFER_EXTENDED, A.ACTIVE, "reopen"),         # A3
        Edge((A.ACTIVE, A.OFFER_EXTENDED), A.CLOSED, "close"),
    ],
    register=False,
)

# ── interview ───────────────────────────────────────────────────────────────


class InterviewStatus(StrEnum):
    SCHEDULED = "scheduled"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DRAFT = "draft"
    ABANDONED = "abandoned"


I = InterviewStatus  # noqa: E741
INTERVIEW_TABLE = {
    I.DRAFT: frozenset({I.SCHEDULED, I.ABANDONED}),
    I.SCHEDULED: frozenset({I.SCHEDULED, I.WAITING_REVIEW, I.COMPLETED, I.CANCELLED}),
    I.WAITING_REVIEW: frozenset({I.COMPLETED, I.CANCELLED}),
    I.COMPLETED: frozenset({I.SCHEDULED}),
    I.ABANDONED: frozenset(),
    I.CANCELLED: frozenset(),
}

INTERVIEW_LIFECYCLE = Lifecycle(
    "interview",
    InterviewStatus,
    # Smart scheduling creates a draft; the direct-schedule path creates rows
    # already scheduled. Two entry points, both declared.
    initial={I.DRAFT, I.SCHEDULED},
    terminal={I.ABANDONED, I.CANCELLED},
    transitions=[
        (I.DRAFT, I.SCHEDULED, "confirm"),
        (I.DRAFT, I.ABANDONED, "abandon"),
        (I.SCHEDULED, I.SCHEDULED, "reschedule"),        # a self-edge
        (I.SCHEDULED, I.WAITING_REVIEW, "save_review_draft"),
        Edge((I.SCHEDULED, I.WAITING_REVIEW), I.COMPLETED, "submit_review"),
        Edge((I.SCHEDULED, I.WAITING_REVIEW), I.CANCELLED, "cancel"),
        (I.COMPLETED, I.SCHEDULED, "reopen"),            # completed is NOT terminal
    ],
    register=False,
)

# ── offer ───────────────────────────────────────────────────────────────────


class OfferStatus(StrEnum):
    CREATED = "created"
    NEGOTIATING = "negotiating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


O = OfferStatus  # noqa: E741
OFFER_TABLE = {
    O.CREATED: frozenset({O.NEGOTIATING, O.ACCEPTED, O.REJECTED, O.WITHDRAWN}),
    O.NEGOTIATING: frozenset({O.ACCEPTED, O.REJECTED, O.WITHDRAWN}),
    O.ACCEPTED: frozenset(),
    O.REJECTED: frozenset(),
    O.WITHDRAWN: frozenset(),
}

OPEN = (O.CREATED, O.NEGOTIATING)
OFFER_LIFECYCLE = Lifecycle(
    "offer",
    OfferStatus,
    initial=O.CREATED,
    terminal={O.ACCEPTED, O.REJECTED, O.WITHDRAWN},
    transitions=[
        (O.CREATED, O.NEGOTIATING, "negotiate"),
        Edge(OPEN, O.ACCEPTED, "accept"),
        Edge(OPEN, O.REJECTED, "reject"),
        Edge(OPEN, O.WITHDRAWN, "withdraw"),
    ],
    register=False,
)


MACHINES = {
    "requisition": (REQUISITION_TABLE, REQUISITION_LIFECYCLE),
    "job": (JOB_TABLE, JOB_LIFECYCLE),
    "application": (APPLICATION_TABLE, APPLICATION_LIFECYCLE),
    "interview": (INTERVIEW_TABLE, INTERVIEW_LIFECYCLE),
    "offer": (OFFER_TABLE, OFFER_LIFECYCLE),
}


def _pairs(table):
    states = list(table)
    return [(a, b) for a in states for b in states]


@pytest.mark.parametrize("name", MACHINES)
def test_allows_exactly_what_the_table_allows(name):
    table, lifecycle = MACHINES[name]
    for a, b in _pairs(table):
        assert lifecycle.can_transition(a, b) is (b in table[a]), (name, a, b)


@pytest.mark.parametrize("name", MACHINES)
def test_every_refused_pair_raises_with_the_allowed_set(name):
    table, lifecycle = MACHINES[name]
    refused = [(a, b) for a, b in _pairs(table) if b not in table[a]]
    assert refused  # every machine refuses something
    for a, b in refused:
        with pytest.raises(InvalidTransition) as exc:
            lifecycle.transition(a, b)
        err = exc.value
        assert (err.lifecycle, err.from_state, err.to_state) == (name, a, b)
        assert set(err.allowed) == set(table[a])


@pytest.mark.parametrize("name", MACHINES)
def test_every_allowed_pair_transitions(name):
    table, lifecycle = MACHINES[name]
    for a, b in _pairs(table):
        if b in table[a]:
            record = lifecycle.transition(a, b)
            assert (record.from_state, record.to_state) == (a, b)


@pytest.mark.parametrize("name", MACHINES)
def test_allowed_from_matches_the_table(name):
    table, lifecycle = MACHINES[name]
    for state, targets in table.items():
        assert set(lifecycle.allowed_from(state)) == targets, (name, state)


@pytest.mark.parametrize("name", MACHINES)
def test_terminal_means_no_way_out_in_both(name):
    """The product's ``is_terminal`` was ``not _TRANSITIONS[s]``."""
    table, lifecycle = MACHINES[name]
    for state, targets in table.items():
        assert lifecycle.is_terminal(state) is (not targets), (name, state)


def test_interview_keeps_its_self_edge_and_reopenable_completed():
    lc = INTERVIEW_LIFECYCLE
    assert lc.fire(I.SCHEDULED, "reschedule").to_state is I.SCHEDULED
    assert not lc.is_terminal(I.COMPLETED)
    assert lc.fire(I.COMPLETED, "reopen").to_state is I.SCHEDULED
    # both entry points start a record; a mid-life state does not
    assert lc.start(I.DRAFT).from_state is None
    assert lc.start(I.SCHEDULED).to_state is I.SCHEDULED
    with pytest.raises(InvalidTransition) as exc:
        lc.start(I.COMPLETED)
    assert exc.value.reason == "not_initial"


def test_job_close_keeps_both_causes_as_actions():
    """J4 (discarded) and J5 (hired) are one pair, two named moves; the record
    says which one happened."""
    assert JOB_LIFECYCLE.fire(J.ACTIVE, "hire").action == "hire"
    assert JOB_LIFECYCLE.transition(J.ACTIVE, J.CLOSED, action="discard").action == "discard"


def test_requisition_cancel_is_pre_live_only():
    """R-15/R-16: cancelled from the three pre-live states, closed only from active."""
    lc = REQUISITION_LIFECYCLE
    for s in (R.NEW, R.AWAITING_APPROVAL, R.RETURNED):
        assert lc.fire(s, "cancel").to_state is R.CANCELLED
    with pytest.raises(InvalidTransition) as exc:
        lc.fire(R.ACTIVE, "cancel")
    assert exc.value.reason == "unknown_action"
    assert set(exc.value.allowed) == {R.NEW, R.CLOSED}


def test_a_service_rule_becomes_a_guard():
    """J3: unpublish only while the job has never had an application. The
    product enforced it in the service beside the table; here it sits on the
    edge and is refused with its own sentence."""

    @guard("the job has never received an application")
    def never_applied(ctx) -> bool:
        return ctx["applications"] == 0

    lc = Lifecycle(
        "job", JobStatus, initial=J.DRAFT, terminal=J.CLOSED, register=False,
        transitions=[
            (J.DRAFT, J.ACTIVE, "publish"),
            (J.DRAFT, J.CLOSED, "discard"),
            Edge(J.ACTIVE, J.DRAFT, "unpublish", guard=never_applied),
            (J.ACTIVE, J.CLOSED, "discard"),
        ],
    )
    assert lc.can_transition(J.ACTIVE, J.DRAFT, context={"applications": 0})
    assert not lc.can_transition(J.ACTIVE, J.DRAFT, context={"applications": 3})
    assert "only when the job has never received an application" in " ".join(lc.describe())


def test_application_stage_events_come_from_the_sink():
    """The product wrote one ``application_stage_event`` row per move, with a
    NULL ``from_stage`` for the creation event. The same rows, host-persisted."""
    rows = []

    def record(event):
        ctx = event.context
        rows.append({
            "application_id": ctx["application_id"],
            "from_stage": event.from_state,
            "to_stage": event.to_state,
            "actor": ctx["actor"],
            "detail": {"action": event.action},
        })

    lc = Lifecycle(
        "application", ApplicationStage, initial=A.ACTIVE, terminal=A.CLOSED,
        register=False, sink=record,
        transitions=[
            (A.ACTIVE, A.OFFER_EXTENDED, "extend_offer"),
            (A.OFFER_EXTENDED, A.ACTIVE, "reopen"),
            Edge((A.ACTIVE, A.OFFER_EXTENDED), A.CLOSED, "close"),
        ],
    )
    ctx = {"application_id": 7, "actor": "recruiter@x"}
    lc.start(context=ctx)
    lc.fire(A.ACTIVE, "extend_offer", context=ctx)
    lc.fire(A.OFFER_EXTENDED, "reopen", context=ctx)
    with pytest.raises(InvalidTransition):
        lc.transition(A.CLOSED, A.ACTIVE, context=ctx)   # refused: nothing written
    assert [(r["from_stage"], r["to_stage"]) for r in rows] == [
        (None, A.ACTIVE),
        (A.ACTIVE, A.OFFER_EXTENDED),
        (A.OFFER_EXTENDED, A.ACTIVE),
    ]
