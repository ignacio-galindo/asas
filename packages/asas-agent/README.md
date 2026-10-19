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

## Running on a gateway, tracing in Langfuse

The eval models measure; a product run needs a provider with its own timeout
and retries, and its traces beside the rest of the product's in Langfuse.

```python
from agents import Runner, RunConfig, add_trace_processor
from asas_agent import gateway_provider, LangfuseTracingProcessor

provider = gateway_provider(api_key=cfg.LLM_KEY, base_url=cfg.LLM_URL,
                            timeout=120, max_retries=2,
                            use_responses=False)        # a chat-completions-only gateway
add_trace_processor(LangfuseTracingProcessor(langfuse_client, mask=mask))

result = await Runner.run(agent, "...", run_config=RunConfig(model_provider=provider))
```

`azure_provider(api_key=, endpoint=, api_version=)` is the Azure OpenAI
counterpart, where the model a run names is the deployment. The tracing
processor writes the run as one Langfuse trace in the group id's session, a
generation per model call with model, messages and usage, a span per tool
call with its arguments and result, and never fails the run when Langfuse is
down. Both are loaded on first use, like the eval models.

## Developing

`pip install -e '.[dev]'`, then `pytest -q`. No database, no network, no model.
