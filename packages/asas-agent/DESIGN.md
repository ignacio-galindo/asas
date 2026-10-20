# asas-agent design

## Shape

```
asas_agent/
  contracts.py   run payloads (pydantic): RunInput, ToolCall, ModelCall, Ask,
                 Delivery, TurnResult, Failure
  skills.py      the SKILL.md format: parse_skill, load_skills
  releases.py    build_release, load_release, read_release, stage_view, stage_tools
  evaluation.py  scenario suites: parse_scenario, run_suite, summarize_repetitions,
                 assert_expected_record
  models.py      MeasuredModel, GatewayModels (imports the SDK; loaded on first use)
  providers.py   gateway_provider, azure_provider: a product run's ModelProvider
                 (imports the SDK and the OpenAI client; loaded on first use)
  tracing.py     LangfuseTracingProcessor: the SDK's run traces in Langfuse 2.x
                 (imports the SDK; loaded on first use)
  seams.py       the Protocols a host fills: ToolDispatcher, AskHandler, SkillAware,
                 ContextRenderer
  loop.py        run_agent, instructions_for, described_tools, model_settings
                 (imports the SDK; loaded on first use)
  cli.py         asas-agent eval
```

Hosts import from `asas_agent` only; the submodules may move between releases.

## Hosted, not hosting

A host runs each agent run somewhere durable. The first host keeps one Temporal
child workflow per run, makes every model and tool call an Activity and replays
the payloads above. None of that is here: the package never imports a workflow
engine, and a run's moving parts reach the host through seams the host fills.

| Seam | What the host supplies | Arrives |
|---|---|---|
| model provider | the SDK `Model` or `ModelProvider` a run calls (a gateway, a model called by reference from a workflow and told the active skill through `SkillAware`, a scripted model in tests) | 0.3.0 |
| tool dispatcher | `ToolDispatcher`: the call id, tool name, arguments and active skill in, the tool's result out, under the host's authorisation and gates | 0.3.0 |
| ask handler | `AskHandler`: records the agent's question and returns when the person answers, or raises `AskPending` | 0.3.0 |
| context renderer | `ContextRenderer`: the record the agent reads first and finds in tool results, the tools every skill holds, the ask tool, and the host's words before and after the skills | 0.3.0 |
| run store | the run's transcript, model-call cache, tool effects, inbox and outbox | with the store and its tables |
| clock | the time the run sees | with the store |

Rules for code here, so a host can call it from a workflow sandbox:

- No I/O, randomness, environment reads or global state at import.
- No reads of the clock or the environment inside a function a workflow calls;
  the host passes what it recorded (`RunInput` carries its deadlines for that
  reason).
- `import asas_agent` does not import the SDK or the OpenAI client.

## The loop

`run_agent` renders the instructions as the release's prompt, the renderer's
`head`, the skill catalogue, the active skill's playbook and clauses, then the
renderer's `tail`, and opens the input with the renderer's `opening` before the
history. Only `activate_skill` changes the instructions, so the provider's prompt
cache holds them between writes; the record rides in the input. Tools run one at
a time in the order the model issued them, so a replay sees the same sequence.
An `AgentsException` from a seam ends the run; any other exception from the
dispatcher is a typed refusal the model reads.

## Releases

The id is the SHA-256 of the release without its id, as
`json.dumps(release, sort_keys=True)` writes it. The keys are `name`, `model`,
`instructions`, `skills`, `max_turns`, `max_tools`. A new key changes every
host's ids, so it is a breaking release, never a patch.

## Tables

None yet. When the run store moves here it owns its tables and its Alembic chain,
runs in the host's database, protects each tenant table with asas-tenancy's row
level security in the revision that creates it, and keeps the table names its
first host already has, so adopting it moves no data.
