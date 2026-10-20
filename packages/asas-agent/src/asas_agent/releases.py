"""Releases: an agent's definition captured whole, under the hash of its content.

A run pins the release it started on and never rereads the files, so a retry or a
replay sees the same instructions and skills. The layout of an agents directory:

    <directory>/<agent>/agent.yaml     name, max_turns, max_tools
    <directory>/<agent>/prompt.md      the instructions
    <directory>/<agent>/<extra files>  joined to the instructions, in the order given
    <directory>/skills/*/SKILL.md      the skills (asas_agent.skills)
    <directory>/releases/              published releases and the active pointers

The id is the SHA-256 of the release without its id, as `json.dumps(..., sort_keys=True)`
writes it. Any key added to a release changes every id, so the shape below is a contract.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .skills import load_skills


def release_id(release: Mapping[str, Any]) -> str:
    """The content hash of a release that does not yet carry its id."""
    return hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest()


def build_release(
    directory: Path, model: str, *, agent: str, extra_files: Sequence[str] = ()
) -> dict[str, Any]:
    """The release of `agent` as the files under `directory` define it today, for
    `model`. Each of `extra_files` that exists in the agent's folder joins the prompt,
    and its words are part of the hash."""
    folder = directory / agent
    definition = yaml.safe_load((folder / "agent.yaml").read_text())
    skills, always = load_skills(directory / "skills")
    instructions = (folder / "prompt.md").read_text()
    for name in extra_files:
        extra = folder / name
        if extra.exists():
            instructions = instructions.rstrip() + "\n\n" + extra.read_text()
    release: dict[str, Any] = {
        "name": definition["name"],
        "model": model,
        "instructions": "\n\n".join([instructions.rstrip(), *always]),
        "skills": skills,
        "max_turns": definition["max_turns"],
        "max_tools": definition["max_tools"],
    }
    release["id"] = release_id(release)
    return release


def read_release(directory: Path, model: str, *, environment: str) -> dict[str, Any]:
    """The release published as active for `environment`, refused unless its content
    still hashes to its id and it was built for `model`."""
    pointer = json.loads(
        (directory / "releases" / f"active-{environment}.json").read_text()
    )
    pinned = pointer["id"]
    if len(pinned) != 64 or any(c not in "0123456789abcdef" for c in pinned):
        raise ValueError("Invalid agent release reference")
    release = json.loads((directory / "releases" / f"{pinned}.json").read_text())
    check = dict(release)
    check.pop("id")
    if release_id(check) != pinned or release["model"] != model:
        raise ValueError("Agent release integrity or model configuration mismatch")
    return release


def load_release(
    model: str,
    directory: Path,
    *,
    agent: str,
    extra_files: Sequence[str] = (),
    environment: str = "local",
    built_in: Collection[str] = ("local", "test"),
) -> dict[str, Any]:
    """The release a run pins: built from the files in the environments of `built_in`,
    read from the published release everywhere else."""
    if environment not in built_in:
        return read_release(directory, model, environment=environment)
    return build_release(directory, model, agent=agent, extra_files=extra_files)


def stage_view(
    release: Mapping[str, Any], stage: str, *, default_stage: str = "intake"
) -> dict:
    """The release as a run of one stage reads it: only the skills of that stage, a
    skill naming its stage in its header (`stage:`) and one that names none being
    `default_stage`'s. The id stays the release's: the view is derived."""
    return {
        **release,
        "skills": {
            name: skill
            for name, skill in release["skills"].items()
            if skill.get("stage", default_stage) == stage
        },
    }


def stage_tools(
    release: Mapping[str, Any], stage: str, *, default_stage: str = "intake"
) -> frozenset[str]:
    """The tools a stage's skills hold: the release's tool list for that stage."""
    return frozenset(
        tool
        for skill in stage_view(release, stage, default_stage=default_stage)[
            "skills"
        ].values()
        for tool in skill["tools"]
    )
