import hashlib
import json
import sys
import textwrap

import pytest

from asas_agent.cli import main


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A suite of two scenarios and a host module whose attempt passes only `a`."""
    suite = tmp_path / "suite"
    (suite / "scenarios").mkdir(parents=True)
    contracts = {}
    for stem in ("a", "b"):
        text = f"# profile: p\n{stem}\n"
        (suite / "scenarios" / f"{stem}.txt").write_text(text)
        contracts[stem] = {"source_sha256": hashlib.sha256(text.encode()).hexdigest()}
    (suite / "manifest.json").write_text(json.dumps({"scenarios": ["a.txt", "b.txt"]}))
    (suite / "assertions.json").write_text(json.dumps(contracts))
    (tmp_path / "eval_host.py").write_text(
        textwrap.dedent(
            """
            async def attempt(path, contract, gateway):
                assert gateway is None  # the scripted provider
                return {"status": "passed" if path.stem == "a" else "failed"}
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield suite
    sys.modules.pop("eval_host", None)


def test_eval_runs_the_suite_through_the_hosts_attempt(host, tmp_path, capsys):
    output = tmp_path / "report.json"
    code = main(
        ["eval", str(host), "--attempt", "eval_host:attempt", "--output", str(output)]
    )
    assert code == 1  # b fails
    report = json.loads(output.read_text())
    assert (report["provider"], report["model"]) == ("scripted", "scripted")
    assert report["reasoning_quality_measured"] is False
    assert (
        report["scenarios"]["a"]["full_pass_4"]
        and not report["scenarios"]["b"]["full_pass_4"]
    )
    assert "full pass^4: 1/2; provider=scripted" in capsys.readouterr().out

    only_a = tmp_path / "a.json"
    argv = ["eval", str(host), "--attempt", "eval_host:attempt", "--scenario", "a"]
    assert main([*argv, "--output", str(only_a)]) == 0


def test_eval_never_overwrites_evidence(host, tmp_path, capsys):
    output = tmp_path / "report.json"
    output.write_text("{}")
    code = main(
        ["eval", str(host), "--attempt", "eval_host:attempt", "--output", str(output)]
    )
    assert code == 2 and "already exists" in capsys.readouterr().err
    assert output.read_text() == "{}"


def test_a_gateway_run_without_its_settings_stops_before_running(
    host, tmp_path, monkeypatch, capsys
):
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    output = tmp_path / "report.json"
    argv = [
        "eval",
        str(host),
        "--attempt",
        "eval_host:attempt",
        "--provider",
        "gateway",
    ]
    assert main([*argv, "--output", str(output)]) == 2
    assert "Gateway test requires" in capsys.readouterr().err and not output.exists()
