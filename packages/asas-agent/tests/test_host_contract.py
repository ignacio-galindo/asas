"""Host-contract conformance (Teamy TEAMY-798), and the two properties a durable host
relies on: the package imports nothing heavy until asked, and it has no Temporal
dependency, so it runs inside a workflow sandbox and outside one alike."""

import subprocess
import sys
import tomllib
import types
from pathlib import Path

import asas_agent

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def test_declares_all_and_every_name_resolves():
    assert asas_agent.__all__
    missing = [n for n in asas_agent.__all__ if not hasattr(asas_agent, n)]
    assert not missing, f"asas_agent.__all__ names that do not resolve: {missing}"


def test_exports_no_module():
    modules = [
        n
        for n in asas_agent.__all__
        if isinstance(getattr(asas_agent, n), types.ModuleType)
    ]
    assert not modules, modules


def test_owns_no_schema_or_router_slots_yet():
    for name in ("migrate", "seed", "build_routers", "build_router"):
        assert not hasattr(asas_agent, name), name


def test_import_loads_neither_the_sdk_nor_a_client():
    """`import asas_agent` stays light and side-effect free: a workflow sandbox
    imports the contracts, and must not pay for (or replay) the SDK's import. Checked
    in a fresh interpreter so this suite's own imports cannot mask a regression."""
    code = (
        "import sys, asas_agent; "
        "bad = [m for m in ('agents', 'openai', 'httpx', 'temporalio') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": SRC, "PATH": ""},
    )
    assert out.stdout.strip() == "", f"import asas_agent loaded: {out.stdout.strip()}"


def test_the_eval_models_load_on_first_use():
    assert asas_agent.GatewayModels.__module__ == "asas_agent.models"


def test_the_sdk_loop_loads_on_first_use_and_its_seams_at_import():
    assert asas_agent.run_agent.__module__ == "asas_agent.loop"
    assert asas_agent.ContextRenderer.__module__ == "asas_agent.seams"


def test_no_temporal_dependency():
    """Agents are hosted in Temporal by the application, not the other way round."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extras = project.get("optional-dependencies", {}).values()
    declared = [*project["dependencies"], *(d for extra in extras for d in extra)]
    assert not [d for d in declared if d.lower().startswith("temporalio")]
    sources = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    assert "temporalio" not in sources
