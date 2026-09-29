# Changelog — `asas-llm`

Versions follow semver, and the git tag matches this file: `asas-llm/v0.1.0`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure: [`RELEASING.md`](../../RELEASING.md).

## 0.1.0 — 2026-09-29

- First release, extracted from the ai-recruiter engine (`base/llm/runner.py`,
  `base/utils/pydantic.py`, `base/llm/prompts`, the transcription tracer) and
  made framework-independent (pydantic only):
  - `to_strict_json_schema` / `response_format`, with the envelope name
    sanitised to what the API accepts;
  - `parse_structured` raises `EmptyOutputError` / `StructuredOutputError`
    instead of returning None, and tolerates fences, prose, content-part lists
    and (with `[repair]`) malformed JSON;
  - `call` adds what the engine's LangChain path lacked: per-attempt timeouts,
    retries on 408/409/429/5xx only, Retry-After, jittered backoff, and a usage
    sink;
  - `trace_scope` / `current_trace_id` for nesting;
  - `get_prompt` with a bounded cold fetch, stale-on-error serving and
    `{{variable}}` rendering;
  - `redact_messages` for inline audio, image and file payloads.
