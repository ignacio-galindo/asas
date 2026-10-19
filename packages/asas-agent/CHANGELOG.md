# Changelog: `asas-agent`

Versions follow semver, and the git tag matches this file: `asas-agent/v0.2.0`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure and the historical tag mapping: [`RELEASING.md`](../../RELEASING.md).

## 0.2.0 (unreleased)

A new package with an old name. `asas-agent` 0.1.0 lived in its own repository
(`ad-govtech/asas-agent`): a Postgres registry of agent definitions, Langfuse
prompts and a stateless `AgentRuntime.run` on SDK 0.8. This release shares
nothing with it. **There is no upgrade path from 0.1.0**: a host on 0.1.0 stays
on that tag or moves to this API.

The runtime is extracted from a procurement application's agent runtime in
steps; this release carries the pieces that are pure.

- **Releases** (`build_release`, `load_release`, `read_release`, `release_id`):
  an agent's prompt, extra prompt files, enabled skills and budgets captured
  under the SHA-256 of `json.dumps(release, sort_keys=True)`. The agent's folder,
  the extra files and the environments that build from files are arguments, so
  the source application's release ids are byte-identical after the move.
  Elsewhere the published release under `releases/active-<environment>.json` is
  read and refused unless it still hashes to its id and names the model.
- **Skills** (`parse_skill`, `load_skills`): the SKILL.md format, a YAML header
  (`name`, `enabled`, `applies: always`, `stage`, `roles`, `tools`, and any
  field the host adds) and the playbook. `stage_view` and `stage_tools` give a
  stage's run only its skills; the default stage is an argument.
- **Run contracts** (`RunInput`, `ToolCall`, `ModelCall`, `Ask`, `Delivery`,
  `TurnResult`, `Failure`): the payloads a durable host replays, with their
  fields and defaults pinned by tests. `RunInput.stage` is a string; a host
  narrows it by subclassing. `RunInput.need_id` keeps its first host's name so
  running workflows replay; a rename is a later, breaking release.
- **Evals** (`parse_scenario`, `run_suite`, `summarize_repetitions`,
  `assert_expected_record`, `ProcedureFailure`): scenario suites with hashed
  contracts, four attempts each, pass^4 and prefix pass^4 counted apart.
  `MeasuredModel` and `GatewayModels` (in `asas_agent.models`, loaded on first
  use) measure every gateway call, wait out throttling and count the retries;
  the settings a gateway is read from are an argument.
- **`asas-agent eval <suite> --attempt module:function`** runs a suite through
  the host's attempt and writes a report naming the provider, the model and
  `reasoning_quality_measured`. It never overwrites a report.
- No Temporal dependency, not even an extra. Table-less and router-less in this
  release; the run store and its tables arrive in a later one.
- Depends on `openai-agents>=0.22,<0.23`, `pydantic>=2.11,<3` and `pyyaml>=6.0`.
