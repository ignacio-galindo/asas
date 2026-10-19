"""The run contracts: what crosses between a host's orchestration and an agent run.

A host that keeps runs in a durable workflow replays these payloads, so their field
names, types and defaults are a contract: a new field is added with a default, and
nothing is renamed or removed without a minor release that says so.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """One tool call the model made, as the host's dispatcher receives it."""

    tenant: str
    run_id: str
    call_id: str
    name: str
    arguments: dict[str, Any]
    active_skill: str | None = None


class RunInput(BaseModel):
    """One agent run on a case: its tenant, its id, the case it works on and the
    deadlines the host recorded for it."""

    tenant: str
    run_id: str
    #: The case the run works on. The name is the first host's (a procurement need)
    #: and is kept so its running workflows replay.
    need_id: str = ""
    #: The stage of the case the run works on; one run per stage. A host narrows the
    #: values it allows by subclassing.
    stage: str = "intake"
    # Recorded in the input for deterministic replay, never read from the environment
    # inside a workflow.
    tool_activity_timeout_seconds: float = Field(default=60, gt=0)
    #: How long a run waits for the person's answer before it ends idle; the next
    #: message starts a fresh run from the record.
    idle_timeout_seconds: float = Field(default=7200, gt=0)
    #: The history size past which a run ends at its next question instead of waiting
    #: (rotated): every model call records the transcript, so a long run's history grows
    #: with the square of its calls. The next message starts a fresh run from the record.
    rotate_history_bytes: int = Field(default=8_000_000, gt=0)
    #: The model call's deadline, recorded for replay like the tool's.
    model_activity_timeout_seconds: float = Field(default=45, gt=0)


class TurnResult(BaseModel):
    """How a run ended: the agent's last words, the transcript and what it did."""

    tenant: str
    run_id: str
    answer: str
    history: list[dict[str, Any]]
    active_skill: str | None
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    #: The run's tokens as the provider counted them; None from an older host.
    usage: dict[str, Any] | None = None
    ended: str = "answered"
    #: Items delivered to the run that the agent never read (it ended without asking
    #: again): the host carries them into the next run.
    unread: list[dict[str, Any]] = Field(default_factory=list)


class Delivery(BaseModel):
    """One item for a run's inbox: the person's message, an event of the host
    application or a stop."""

    tenant: str
    run_id: str
    id: str
    kind: Literal["message", "event", "stop"]
    text: str = ""


class ModelCall(BaseModel):
    """One model call by reference: the orchestration sends the items new since its
    last call and the active skill; the host holds the transcript, rebuilds the
    instructions and the tool schemas from the release, calls the model and keeps the
    response under `index`, so a retry returns it instead of calling again.
    `expected_length` is the transcript length the orchestration believes the store
    holds before this call: anything else is drift and ends the run."""

    tenant: str
    run_id: str
    index: int
    active_skill: str | None = None
    items: list[Any] = Field(default_factory=list)
    expected_length: int


class Ask(BaseModel):
    """The agent's question to the person, recorded before the run waits for the
    answer; `call_id` makes the record idempotent under retry."""

    tenant: str
    run_id: str
    call_id: str
    arguments: dict[str, Any]


class Failure(BaseModel):
    """A run that ended in error, by code only: never the model's or a tool's words."""

    tenant: str
    run_id: str
    code: str
