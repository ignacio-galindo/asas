"""Skills: the playbooks an agent activates, one SKILL.md each.

A SKILL.md is a YAML header between two `---` lines, then the playbook in Markdown:

    ---
    name: request-intake        # how the agent activates it
    version: '6'
    enabled: true               # a disabled skill is left out of every release
    applies: always             # optional: always in force, never activated
    stage: sourcing             # optional: the stage whose runs hold it
    roles: [requester]
    tools: [need_read, need_update]
    ---
    The playbook.

The release keeps every header field, so a host may add its own (owner, clauses);
the fields above are the ones this package reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def parse_skill(text: str) -> tuple[dict[str, Any], str]:
    """A SKILL.md's header and its playbook, stripped."""
    _, header, body = text.split("---", 2)
    return yaml.safe_load(header), body.strip()


def load_skills(directory: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The enabled skills under `directory` (`*/SKILL.md`, in path order): those to
    activate by name, each its header plus `instructions`, and the playbooks of those
    that apply always. A skill that applies always (how to speak to people) is no
    playbook to activate: its words join the instructions, so every run follows it."""
    skills: dict[str, dict[str, Any]] = {}
    always: list[str] = []
    for path in sorted(directory.glob("*/SKILL.md")):
        spec, body = parse_skill(path.read_text())
        if not spec.get("enabled", False):
            continue
        if spec.get("applies") == "always":
            always.append(body)
        else:
            skills[spec["name"]] = {**spec, "instructions": body}
    return skills, always
