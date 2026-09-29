"""Asas LLM: the provider-agnostic parts of calling a model well.

Extracted from the ai-recruiter engine, where each piece first failed in
production, and made independent of any SDK or framework (only pydantic):

- :func:`to_strict_json_schema` / :func:`response_format` - a Pydantic model as
  the schema strict structured output accepts ($ref inlining, required-all,
  additionalProperties, a valid envelope name).
- :func:`parse_structured` / :func:`extract_text` - a reply as a validated
  model or a typed error (fences, prose, content parts, JSON repair); never None.
- :func:`call` with :class:`RetryPolicy` - a per-attempt timeout, retries only
  on what is retryable, the provider's Retry-After, jittered backoff, and one
  :class:`Usage` per success to :func:`configure_usage_sink`.
- :func:`trace_scope` / :func:`current_trace_id` - nested calls join the outer
  trace instead of starting their own.
- :func:`get_prompt` with :func:`configure_prompt_source` - cached prompts with
  a bounded cold fetch, stale-on-error serving, and ``{{variable}}`` rendering
  that leaves JSON examples alone.
- :func:`redact_messages` - inline audio, image and file payloads out of traces.

Table-less and router-less variant of the host contract: no session, no
migrations, no routes; the host injects its prompt store and usage sink.
"""

from .calls import (
    RETRYABLE_STATUSES,
    LLMCallError,
    LLMTimeoutError,
    RetryPolicy,
    Usage,
    call,
    configure_usage_sink,
    current_trace_id,
    is_retryable,
    retry_after_of,
    status_of,
    trace_scope,
    usage_of,
)
from .parse import EmptyOutputError, StructuredOutputError, extract_text, parse_structured
from .prompts import (
    Prompt,
    PromptRenderError,
    PromptUnavailableError,
    configure_prompt_source,
    get_prompt,
)
from .redact import redact_messages
from .schema import response_format, schema_name, to_strict_json_schema

__version__ = "0.1.0"

__all__ = [
    "EmptyOutputError",
    "LLMCallError",
    "LLMTimeoutError",
    "Prompt",
    "PromptRenderError",
    "PromptUnavailableError",
    "RETRYABLE_STATUSES",
    "RetryPolicy",
    "StructuredOutputError",
    "Usage",
    "call",
    "configure_prompt_source",
    "configure_usage_sink",
    "current_trace_id",
    "extract_text",
    "get_prompt",
    "is_retryable",
    "parse_structured",
    "redact_messages",
    "response_format",
    "retry_after_of",
    "schema_name",
    "status_of",
    "to_strict_json_schema",
    "trace_scope",
    "usage_of",
    "__version__",
]
