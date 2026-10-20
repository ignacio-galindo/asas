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
| model provider | the SDK `Model` a run calls (a gateway, a model called by reference from a workflow, a scripted model in tests) | with the SDK loop |
| tool dispatcher | `ToolCall` in, the tool's result out, under the host's authorisation and gates | with the SDK loop |
| ask handler | records the agent's question (`Ask`) and returns when the person answers | with the SDK loop |
| context renderer | the record the agent reads first, and its words in the instructions | with the SDK loop |
| run store | the run's transcript, model-call cache, tool effects, inbox and outbox | with the store and its tables |
| clock | the time the run sees | with the store |

Rules for code here, so a host can call it from a workflow sandbox:

- No I/O, randomness, environment reads or global state at import.
- No reads of the clock or the environment inside a function a workflow calls;
  the host passes what it recorded (`RunInput` carries its deadlines for that
  reason).
- `import asas_agent` does not import the SDK or the OpenAI client.

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
