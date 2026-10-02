# asas-llm

The provider-agnostic parts of calling a language model well. No SDK and no
framework is required (only pydantic), so it works under OpenAI, Anthropic,
Azure, LangChain or plain httpx.

```python
import asas_llm

class Summary(BaseModel):
    headline: str
    risks: list[str]

async def summarise(text: str) -> Summary:
    prompt = (await asas_llm.get_prompt("summary")).render(document=text)
    reply = await asas_llm.call(
        lambda: client.chat.completions.create(
            model="gpt-5.1",
            messages=list(prompt.messages),
            response_format=asas_llm.response_format(Summary),
        ),
        name="summary", model="gpt-5.1",
        policy=asas_llm.RetryPolicy(attempts=3, timeout_s=45),
    )
    return asas_llm.parse_structured(reply.choices[0].message.content, Summary)
```

## What it gets right, so you do not have to

- **Strict schemas that the API accepts.** A plain `model_json_schema()` is
  refused by strict structured output for missing `additionalProperties`,
  optional fields not in `required`, `$ref`s with a description beside them,
  and single-entry `allOf`s. `to_strict_json_schema` fixes all of them without
  touching the model's cached schema, and `response_format` gives the envelope
  a name that matches `^[a-zA-Z0-9_-]{1,64}$`.
- **A failed parse is an error, not `None`.** `parse_structured` strips code
  fences and surrounding prose, reads content-part lists, repairs malformed
  JSON when `asas-llm[repair]` is installed, and raises `EmptyOutputError` or
  `StructuredOutputError` with the raw reply attached.
- **Retries that respect the provider.** `call` retries timeouts, connection
  errors and 408/409/429/5xx only, waits the provider's `Retry-After` when it
  sends one, and otherwise backs off with full jitter. A 400 is raised at once.
- **Usage, once per call.** `configure_usage_sink(fn)` receives a `Usage`
  (tokens in and out, duration, attempts, trace id) read from OpenAI,
  Anthropic or LangChain result shapes.
- **Nested calls join the outer trace.** Inside `trace_scope()`, a model call
  made by an agent's tool reads the agent's trace id instead of starting a
  new trace.
- **Prompts that do not take the request down.** `get_prompt` caches for
  `ttl_s`, bounds every fetch with `fetch_timeout_s`, and serves the last good
  copy when a refresh fails. `{{variable}}` rendering leaves JSON examples in
  the prompt alone and names every missing variable.
- **Media stays out of traces.** `redact_messages` replaces inline audio,
  data-URL images and base64 files with their length before a tracer sees them.

## Host contract

Table-less and router-less: no session, no migrations, no routes. The host
injects its prompt store (`configure_prompt_source(async (name, label) ->
Prompt)`) and, optionally, a usage sink.

Extracted from a production AI service's model-call layer and generalised.
