# asas-agent

The framework that runs agents, on the OpenAI Agents SDK. One way to release an
agent, give it skills, hand runs to a durable host and evaluate it, shared by
every application that runs agents.

## Principles

- **Agents are hosted, not hosting.** The package depends on no workflow engine.
  An application that keeps runs in Temporal (or anything else) plugs into its
  seams; the workflows, Activities, payload codec and worker stay in the
  application. Every module is safe to import inside a workflow sandbox: no I/O,
  randomness or global state at import, and `import asas_agent` loads neither the
  SDK nor an OpenAI client.
- **A release is its content.** An agent's prompt, skills and budgets are captured
  whole under the SHA-256 of their content. A run pins its release and never
  rereads the files; publishing is a deployment step, never done at start-up.
- **Skills are playbooks.** One SKILL.md each, a YAML header and the words. The
  agent activates a skill; a skill that applies always joins every run's words.
- **Contracts replay.** The run payloads keep their fields and defaults; a new
  field comes with a default, and nothing is renamed without a minor release.
- **Evals count what was observed.** A scenario passes only when four attempts of
  four pass, a step the host cannot run never counts as a pass, and every report
  states the provider, the model and whether reasoning quality was measured.

## Five minutes

```python
from pathlib import Path
import asas_agent

release = asas_agent.load_release("my-model", Path("agents"), agent="assistant")
release["id"]  # pin this on the run
asas_agent.stage_tools(release, "intake")  # the tools an intake run is offered
```

```bash
asas-agent eval evals/assistant --attempt my_app.evals:attempt --output report.json
```

Pin it like every Asas package, by tag or by commit:

```
asas-agent @ git+https://github.com/wlootah-a11y/asas.git@asas-agent/v0.2.0#subdirectory=packages/asas-agent
```

The package shape and the seams: [`DESIGN.md`](DESIGN.md). What changed:
[`CHANGELOG.md`](CHANGELOG.md).

## Developing

`pip install -e '.[dev]'`, then `pytest -q`. No database, no network, no model.
