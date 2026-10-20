"""Procedure evals: scenarios run four times, judged on what was observed.

A scenario suite is a directory:

    <suite>/manifest.json      {"scenarios": ["<file>", ...]}
    <suite>/assertions.json    {"<file stem>": {"source_sha256": "...", ...}, ...}
    <suite>/scenarios/<file>   one scenario

A scenario file names its profile in a `# profile: <name>` line, then one step a line:
a `/command`, a bare number (a choice from the last answer's options) or a message.
Other `#` lines are comments. The contract of a scenario in `assertions.json` is the
host's to define, except `source_sha256`: a scenario edited without its contract is
drift, and the suite refuses to run it.

An attempt passes only on evidence: the host's attempt returns `passed`, or
`prefix_passed` when only the first part of the journey could run, and a step the host
cannot run is never counted as a pass.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProcedureFailure(AssertionError):
    """An observed run broke a procedure assertion; the message starts with its code."""


@dataclass(frozen=True)
class ScenarioStep:
    line: int
    kind: str
    text: str


def parse_scenario(path: Path) -> tuple[str, list[ScenarioStep]]:
    """A scenario file's profile and its steps, each with its line number."""
    profile = None
    steps = []
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        text = raw.strip()
        if text.startswith("# profile:"):
            profile = text.split(":", 1)[1].strip()
        if not text or text.startswith("#"):
            continue
        kind = (
            "command"
            if text.startswith("/")
            else "choice"
            if text.isdigit()
            else "message"
        )
        steps.append(ScenarioStep(number, kind, text))
    if not profile or not steps:
        raise ValueError(f"{path.name}: a profile and at least one step are required")
    return profile, steps


def summarize_repetitions(attempts: list[dict]) -> dict:
    """pass^4: four attempts, every one passed. A prefix pass counts only towards the
    prefix figure, never the full one."""
    full = len(attempts) == 4 and all(a["status"] == "passed" for a in attempts)
    prefix = len(attempts) == 4 and all(
        a["status"] in {"passed", "prefix_passed"} for a in attempts
    )
    return {"full_pass_4": full, "intake_prefix_pass_4": prefix, "attempts": attempts}


def assert_expected_record(record: dict, assertions: list[dict]) -> list[str]:
    """Each `{"path": "a.b", "equals": value}` holds on the record the run left; the
    paths verified. A missing fact fails as surely as a wrong one."""
    verified = []
    for assertion in assertions:
        value = record
        for part in assertion["path"].split("."):
            if not isinstance(value, dict) or part not in value:
                raise ProcedureFailure(f"missing_record_fact: {assertion['path']}")
            value = value[part]
        if value != assertion["equals"]:
            raise ProcedureFailure(f"record_mismatch: {assertion['path']}")
        verified.append(assertion["path"])
    return verified


#: One attempt at a scenario: its file and its contract in, the observed result out
#: (a dict with at least `status`).
Attempt = Callable[[Path, dict[str, Any]], Awaitable[dict[str, Any]]]


async def run_suite(
    suite: Path,
    attempt: Attempt,
    *,
    attempts: int = 4,
    only: Collection[str] | None = None,
) -> dict[str, dict]:
    """Every scenario of the suite's manifest (or those whose stem is in `only`),
    `attempts` times each in turn, summarised per scenario stem. Each scenario's hash
    is checked against its contract before it runs."""
    manifest = json.loads((suite / "manifest.json").read_text())
    contracts = json.loads((suite / "assertions.json").read_text())
    results: dict[str, dict] = {}
    for filename in manifest["scenarios"]:
        path = suite / "scenarios" / filename
        if only and path.stem not in only:
            continue
        contract = contracts[path.stem]
        if hashlib.sha256(path.read_bytes()).hexdigest() != contract["source_sha256"]:
            raise ValueError(f"Assertion spec drift: {filename}")
        observed = []
        for number in range(1, attempts + 1):
            print(
                f"{path.stem}: attempt {number} of {attempts}",
                file=sys.stderr,
                flush=True,
            )
            observed.append(await attempt(path, contract))
        results[path.stem] = summarize_repetitions(observed)
    return results
