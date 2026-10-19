"""The run contracts are replayed by durable hosts: their fields, types and defaults
are pinned here, so a change to any of them fails on the PR that makes it."""

import pytest
from pydantic import ValidationError

from asas_agent import Ask, Delivery, Failure, ModelCall, RunInput, ToolCall, TurnResult

#: Each contract's fields and their defaults (... for required), as hosts replay them.
SHAPES = {
    ToolCall: {
        "tenant": ..., "run_id": ..., "call_id": ..., "name": ..., "arguments": ...,
        "active_skill": None,
    },
    RunInput: {
        "tenant": ..., "run_id": ..., "need_id": "", "stage": "intake",
        "tool_activity_timeout_seconds": 60, "idle_timeout_seconds": 7200,
        "rotate_history_bytes": 8_000_000, "model_activity_timeout_seconds": 45,
    },
    TurnResult: {
        "tenant": ..., "run_id": ..., "answer": ..., "history": ..., "active_skill": ...,
        "tool_trace": [], "usage": None, "ended": "answered", "unread": [],
    },
    Delivery: {"tenant": ..., "run_id": ..., "id": ..., "kind": ..., "text": ""},
    ModelCall: {
        "tenant": ..., "run_id": ..., "index": ..., "active_skill": None, "items": [],
        "expected_length": ...,
    },
    Ask: {"tenant": ..., "run_id": ..., "call_id": ..., "arguments": ...},
    Failure: {"tenant": ..., "run_id": ..., "code": ...},
}  # fmt: skip


@pytest.mark.parametrize("model", SHAPES, ids=lambda m: m.__name__)
def test_fields_and_defaults_are_the_contract(model):
    fields = model.model_fields
    assert list(fields) == list(SHAPES[model])
    for name, default in SHAPES[model].items():
        field = fields[name]
        if default is ...:
            assert field.is_required(), name
        else:
            assert field.get_default(call_default_factory=True) == default, name


def test_a_payload_round_trips_through_json():
    sent = RunInput(tenant="t", run_id="r", need_id="CASE-1", stage="sourcing")
    assert RunInput.model_validate_json(sent.model_dump_json()) == sent
    call = ModelCall(
        tenant="t", run_id="r", index=2, items=[{"type": "message"}], expected_length=5
    )
    assert ModelCall.model_validate(call.model_dump(mode="json")) == call


def test_deadlines_must_be_positive():
    with pytest.raises(ValidationError):
        RunInput(tenant="t", run_id="r", idle_timeout_seconds=0)


def test_a_delivery_is_a_message_an_event_or_a_stop():
    assert Delivery(tenant="t", run_id="r", id="1", kind="stop").text == ""
    with pytest.raises(ValidationError):
        Delivery(tenant="t", run_id="r", id="1", kind="note")


def test_a_host_narrows_the_stage_by_subclassing():
    from typing import Literal

    class HostRunInput(RunInput):
        stage: Literal["intake", "sourcing"] = "intake"

    assert HostRunInput(tenant="t", run_id="r").stage == "intake"
    with pytest.raises(ValidationError):
        HostRunInput(tenant="t", run_id="r", stage="ordering")
