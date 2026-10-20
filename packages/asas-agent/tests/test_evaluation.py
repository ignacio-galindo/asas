import hashlib
import json

import pytest
from conftest import run

from asas_agent import (
    ProcedureFailure,
    ScenarioStep,
    assert_expected_record,
    parse_scenario,
    run_suite,
    summarize_repetitions,
)


def test_a_scenario_is_its_profile_and_its_steps(tmp_path):
    path = tmp_path / "chairs.txt"
    path.write_text(
        "# A comment\n# profile: analyst\n\nI need 12 chairs\n/confirm\n2\n"
    )
    profile, steps = parse_scenario(path)
    assert profile == "analyst"
    assert steps == [
        ScenarioStep(4, "message", "I need 12 chairs"),
        ScenarioStep(5, "command", "/confirm"),
        ScenarioStep(6, "choice", "2"),
    ]


@pytest.mark.parametrize("text", ["I need chairs\n", "# profile: analyst\n# nothing\n"])
def test_a_scenario_needs_a_profile_and_a_step(tmp_path, text):
    path = tmp_path / "bad.txt"
    path.write_text(text)
    with pytest.raises(ValueError, match="a profile and at least one step"):
        parse_scenario(path)


def test_only_four_passes_of_four_are_a_pass():
    assert summarize_repetitions([{"status": "passed"}] * 4)["full_pass_4"]
    prefix = summarize_repetitions([{"status": "prefix_passed"}] * 4)
    assert prefix["intake_prefix_pass_4"] and not prefix["full_pass_4"]
    assert not summarize_repetitions([{"status": "passed"}] * 3)["full_pass_4"]
    three_and_a_failure = [{"status": "passed"}] * 3 + [{"status": "failed"}]
    assert not summarize_repetitions(three_and_a_failure)["intake_prefix_pass_4"]


def test_a_record_fact_missing_or_wrong_fails():
    rule = [{"path": "fields.quantity", "equals": "12"}]
    with pytest.raises(ProcedureFailure, match="missing_record_fact"):
        assert_expected_record({"fields": {}}, rule)
    with pytest.raises(ProcedureFailure, match="record_mismatch"):
        assert_expected_record({"fields": {"quantity": "3"}}, rule)
    assert assert_expected_record({"fields": {"quantity": "12"}}, rule) == [
        "fields.quantity"
    ]


def suite(tmp_path, scenarios: dict[str, str], *, drift: str | None = None):
    (tmp_path / "scenarios").mkdir()
    contracts = {}
    for name, text in scenarios.items():
        path = tmp_path / "scenarios" / name
        path.write_text(text)
        digest = hashlib.sha256(text.encode()).hexdigest()
        contracts[path.stem] = {"source_sha256": "0" * 64 if name == drift else digest}
    (tmp_path / "manifest.json").write_text(json.dumps({"scenarios": list(scenarios)}))
    (tmp_path / "assertions.json").write_text(json.dumps(contracts))
    return tmp_path


def test_a_suite_runs_each_scenario_four_times_and_summarises_it(tmp_path):
    root = suite(
        tmp_path, {"a.txt": "# profile: p\nhello\n", "b.txt": "# profile: p\nbye\n"}
    )
    seen = []

    async def attempt(path, contract):
        seen.append(path.name)
        return {"status": "passed" if path.stem == "a" else "failed"}

    results = run(run_suite(root, attempt))
    assert seen == ["a.txt"] * 4 + ["b.txt"] * 4
    assert results["a"]["full_pass_4"] and not results["b"]["full_pass_4"]
    assert list(run(run_suite(root, attempt, attempts=1, only={"b"}))) == ["b"]


def test_a_scenario_edited_without_its_contract_is_refused(tmp_path):
    root = suite(tmp_path, {"a.txt": "# profile: p\nhello\n"}, drift="a.txt")

    async def attempt(path, contract):  # pragma: no cover - never reached
        return {"status": "passed"}

    with pytest.raises(ValueError, match="Assertion spec drift: a.txt"):
        run(run_suite(root, attempt))
