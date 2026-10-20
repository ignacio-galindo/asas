"""Asas Agent: the framework that runs agents, on the OpenAI Agents SDK.

* **Releases.** An agent's prompt, skills and budgets captured whole under the hash
  of their content (:func:`build_release`, :func:`load_release`); a run pins its
  release and never rereads the files, so retries and replays see one agent.
* **Skills.** Playbooks in SKILL.md files with a YAML header (:mod:`asas_agent.skills`),
  activated by the agent; one stage of a case sees only its own (:func:`stage_view`).
* **Run contracts.** The payloads between a host's orchestration and a run
  (:class:`RunInput`, :class:`ToolCall`, :class:`ModelCall`, :class:`Ask`,
  :class:`Delivery`, :class:`TurnResult`, :class:`Failure`), stable under replay.
* **Evals.** Scenario suites run four times and judged on what was observed
  (:func:`run_suite`, :func:`summarize_repetitions`, :func:`assert_expected_record`),
  on a measured gateway (:class:`GatewayModels`, :class:`MeasuredModel`); the
  ``asas-agent eval`` command runs a suite.

Independent of Temporal: a host that keeps runs in durable workflows plugs into the
seams (DESIGN.md), and every module is safe to import inside a workflow sandbox: no
I/O, randomness or global state at import. ``import asas_agent`` loads neither the
Agents SDK nor the OpenAI client; :mod:`asas_agent.models` loads them when one of its
names is first used.

Host contract shape: **table-less, router-less** in this release.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts import Ask, Delivery, Failure, ModelCall, RunInput, ToolCall, TurnResult
from .evaluation import (
    ProcedureFailure,
    ScenarioStep,
    assert_expected_record,
    parse_scenario,
    run_suite,
    summarize_repetitions,
)
from .releases import (
    build_release,
    load_release,
    read_release,
    release_id,
    stage_tools,
    stage_view,
)
from .skills import load_skills, parse_skill

if TYPE_CHECKING:  # pragma: no cover
    from .models import GATEWAY_KEYS, GatewayModels, MeasuredModel, usage_of

__version__ = "0.2.0"

#: Names served by asas_agent.models, imported on first use.
_MODELS = frozenset({"GATEWAY_KEYS", "GatewayModels", "MeasuredModel", "usage_of"})


def __getattr__(name: str) -> Any:
    if name in _MODELS:
        from . import models

        return getattr(models, name)
    raise AttributeError(f"module 'asas_agent' has no attribute {name!r}")


__all__ = [
    "GATEWAY_KEYS",
    "Ask",
    "Delivery",
    "Failure",
    "GatewayModels",
    "MeasuredModel",
    "ModelCall",
    "ProcedureFailure",
    "RunInput",
    "ScenarioStep",
    "ToolCall",
    "TurnResult",
    "__version__",
    "assert_expected_record",
    "build_release",
    "load_release",
    "load_skills",
    "parse_scenario",
    "parse_skill",
    "read_release",
    "release_id",
    "run_suite",
    "stage_tools",
    "stage_view",
    "summarize_repetitions",
    "usage_of",
]
