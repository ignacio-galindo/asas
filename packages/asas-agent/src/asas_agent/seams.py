"""The seams a host fills for the SDK loop (:mod:`asas_agent.loop`).

Protocols only, importing nothing heavy, so `import asas_agent` names them without
loading the SDK. The loop owns the skills, the budget and the trace; the host owns
the tools, the person the agent speaks to, the record the agent works on and the
words that say so.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


class ToolDispatcher(Protocol):
    """Runs one tool call under the host's authorisation and gates: (call id, tool
    name, arguments, active skill) in, the result the model reads out. An exception
    that is not an `AgentsException` becomes a typed refusal the model reads; an
    `AgentsException` ends the run."""

    def __call__(
        self, call_id: str, name: str, arguments: dict, active_skill: str | None, /
    ) -> Awaitable[dict]: ...


class AskHandler(Protocol):
    """The agent's question to the person: (call id, the ask tool's arguments) in, what
    the model reads when the answer comes. It may raise `AskPending` when nobody in
    this process waits, which ends the run at the question."""

    def __call__(self, call_id: str, arguments: dict, /) -> Awaitable[dict]: ...


@runtime_checkable
class SkillAware(Protocol):
    """A run model that is told the active skill instead of the rendered instructions
    (a host's model called by reference rebuilds them from the run's snapshot)."""

    def bind(self, active_skill: Callable[[], str | None]) -> None: ...


class ContextRenderer(Protocol):
    """What the agent reads beside the skills: the host's record and its words.

    The loop renders the instructions as the release's prompt, `head`, the skill
    catalogue, the active skill (or `no_skill`), the skill's clauses, then `tail`;
    the input opens with `opening` and the snapshot's history. Every method is pure:
    a host that calls the model by reference renders the same text from the run's
    snapshot."""

    #: Tools every skill holds, beside `activate_skill`.
    always: frozenset[str]
    #: The tool whose call goes to the ask handler; one of `always`.
    ask_tool: str
    #: `activate_skill`'s description, as the model reads it.
    activate_skill: str
    #: The active skill section while no skill is active.
    no_skill: str

    def head(self, snapshot: Mapping[str, Any]) -> Sequence[str]:
        """Sections between the prompt and the skill catalogue."""
        ...

    def tail(self, snapshot: Mapping[str, Any]) -> Sequence[str]:
        """Sections after the active skill and its clauses."""
        ...

    def record(self, result: Mapping[str, Any]) -> str | None:
        """The record a tool result carries, as the agent reads it, or None."""
        ...

    def opening(self, record: str) -> Sequence[dict]:
        """The input items before the history: the record as it reads at the start
        (empty when the run has none yet)."""
        ...
