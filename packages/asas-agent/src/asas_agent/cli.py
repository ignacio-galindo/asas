"""`asas-agent eval`: a scenario suite, four attempts each, one report.

    asas-agent eval <suite> --attempt my_app.evals:attempt --output report.json
    asas-agent eval <suite> --attempt my_app.evals:attempt --provider gateway ...

The host supplies the attempt: `async def attempt(path, contract, gateway) -> dict`,
where `gateway` is the GatewayModels to take models from, or None on the scripted
provider (the host's own scripted model, which proves the harness only). The report
states the provider, the model and whether reasoning quality was measured: only a
gateway run with at least one successful model call measures it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from functools import partial
from pathlib import Path

from . import __version__
from .evaluation import run_suite


def _hook(reference: str):
    module, _, name = reference.partition(":")
    if not module or not name:
        raise argparse.ArgumentTypeError(f"expected module:function, got {reference!r}")
    return getattr(importlib.import_module(module), name)


async def _eval(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise FileExistsError(
            "Evaluation output already exists; choose a new --output path."
        )
    gateway = None
    if args.provider == "gateway":
        from .models import GatewayModels

        gateway = GatewayModels(os.environ, args.timeout)
    report: dict = {
        "provider": args.provider,
        "model": gateway.model_name if gateway else "scripted",
        "suite": str(args.suite),
        "reasoning_quality_measured": False,
    }
    try:
        report["scenarios"] = await run_suite(
            args.suite,
            partial(args.attempt, gateway=gateway),
            attempts=args.attempts,
            only=args.scenario,
        )
        report["model_calls"] = gateway.measurements if gateway else []
        report["reasoning_quality_measured"] = any(
            c["ok"] for c in report["model_calls"]
        )
    finally:
        if gateway:
            await gateway.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, default=str) + "\n")
    key = "full_pass_4" if args.scope == "full" else "intake_prefix_pass_4"
    passed = sum(case[key] for case in report["scenarios"].values())
    total = len(report["scenarios"])
    print(
        f"{args.scope} pass^4: {passed}/{total}; provider={report['provider']}; "
        f"model={report['model']}; "
        f"reasoning_quality_measured={report['reasoning_quality_measured']}; "
        f"report={args.output}"
    )
    return 0 if total and passed == total else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asas-agent")
    parser.add_argument(
        "--version", action="version", version=f"asas-agent {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("eval", help="run a scenario suite, four attempts each")
    run.add_argument("suite", type=Path)
    run.add_argument("--attempt", type=_hook, required=True, help="module:function")
    run.add_argument("--provider", choices=["scripted", "gateway"], default="scripted")
    run.add_argument("--scope", choices=["full", "prefix"], default="full")
    run.add_argument(
        "--scenario", action="append", help="a scenario's stem; all without"
    )
    run.add_argument("--attempts", type=int, default=4, help="4 is the pass^4 standard")
    run.add_argument(
        "--timeout", type=float, default=600, help="seconds per model call"
    )
    run.add_argument("--output", type=Path, required=True, help="a new file")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_eval(args))
    except (FileExistsError, ValueError) as error:
        print(f"asas-agent: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
