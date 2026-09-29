"""One model call, made well: a timeout, retries that respect the provider,
usage reported once, and trace nesting that survives agents and tools.

The engine had none of the first three on its LangChain path (a hung call hung
the request; a 429 was a failure), and its fourth was hand-rolled per method.
Everything here is provider-agnostic: :func:`call` wraps any
``async () -> result`` (an SDK call, a LangChain ``ainvoke``, an HTTP request).

**What is retried.** A timeout, a connection error, and an HTTP status of 408,
409, 429 or any 5xx (read from the exception's ``status_code``, ``status`` or
``response.status_code``, which covers openai, anthropic and httpx). Any other
4xx is the caller's mistake and is raised at once: retrying a bad request only
spends quota.

**How long it waits.** The provider's ``Retry-After`` when it sent one (header
or ``retry_after`` attribute), capped at ``max_backoff_s``; otherwise
exponential backoff with full jitter, so a fleet that failed together does not
retry together.

**Usage.** :func:`configure_usage_sink` receives one :class:`Usage` per
successful call, with tokens read from the result by ``usage_of`` (the default
understands OpenAI, Anthropic and LangChain ``usage_metadata`` shapes).

**Tracing.** :func:`trace_scope` sets the current trace id in a ContextVar and
:func:`current_trace_id` reads it, so a call inside an agent's tool nests under
the agent's trace instead of starting its own: the engine's rule, stated once.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterator, Optional, TypeVar

log = logging.getLogger(__name__)
R = TypeVar("R")

RETRYABLE_STATUSES = frozenset({408, 409, 429}) | frozenset(range(500, 600))

_trace_id: ContextVar[Optional[str]] = ContextVar("asas_llm_trace_id", default=None)


class LLMCallError(RuntimeError):
    """A call failed after its retries. ``attempts`` says how many were made;
    ``__cause__`` is the last underlying error."""

    def __init__(self, message: str, *, attempts: int, retryable: bool) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.retryable = retryable


class LLMTimeoutError(LLMCallError):
    """Every attempt ran out of time."""


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    timeout_s: float = 60.0
    base_backoff_s: float = 1.0
    max_backoff_s: float = 30.0


@dataclass(frozen=True)
class Usage:
    name: str
    model: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    duration_s: float
    attempts: int
    trace_id: Optional[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> Optional[int]:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


_usage_sink: Optional[Callable[[Usage], Any]] = None


def configure_usage_sink(fn: Optional[Callable[[Usage], Any]]) -> None:
    """Where each successful call's :class:`Usage` goes (a metrics counter, a
    tracer, a cost table). Sync or async; a failing sink is logged, never raised."""
    global _usage_sink
    _usage_sink = fn


def current_trace_id() -> Optional[str]:
    return _trace_id.get()


@contextmanager
def trace_scope(trace_id: Optional[str] = None) -> Iterator[str]:
    """Run a block under a trace id: the given one, the enclosing one, or a new
    one. Nested scopes keep the OUTER id, so inner calls nest rather than fork."""
    outer = _trace_id.get()
    effective = outer or trace_id or uuid.uuid4().hex
    token = _trace_id.set(effective)
    try:
        yield effective
    finally:
        _trace_id.reset(token)


def status_of(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def retry_after_of(exc: BaseException) -> Optional[float]:
    value = getattr(exc, "retry_after", None)
    if value is None:
        headers = getattr(getattr(exc, "response", None), "headers", None) or getattr(exc, "headers", None)
        if headers is not None:
            try:
                value = headers.get("retry-after") or headers.get("Retry-After")
            except Exception:  # noqa: BLE001
                value = None
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None  # an HTTP-date Retry-After: fall back to backoff


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    status = status_of(exc)
    if status is not None:
        return status in RETRYABLE_STATUSES
    name = type(exc).__name__
    return any(word in name for word in ("Timeout", "Connection", "RateLimit", "ServiceUnavailable", "Overloaded"))


def _read_int(source: Any, *names: str) -> Optional[int]:
    for name in names:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        if isinstance(value, int):
            return value
    return None


def usage_of(result: Any) -> tuple[Optional[int], Optional[int]]:
    """Input and output tokens from an OpenAI, Anthropic or LangChain result."""
    for holder in (getattr(result, "usage_metadata", None), getattr(result, "usage", None),
                   result.get("usage") if isinstance(result, dict) else None):
        if holder:
            return (
                _read_int(holder, "input_tokens", "prompt_tokens"),
                _read_int(holder, "output_tokens", "completion_tokens"),
            )
    return None, None


async def _emit(usage: Usage) -> None:
    if _usage_sink is None:
        return
    try:
        outcome = _usage_sink(usage)
        if asyncio.iscoroutine(outcome):
            await outcome
    except Exception:  # noqa: BLE001 - accounting must never fail the call
        log.warning("asas-llm usage sink failed", exc_info=True)


async def call(
    fn: Callable[[], Awaitable[R]],
    *,
    name: str,
    model: Optional[str] = None,
    policy: RetryPolicy = RetryPolicy(),
    metadata: Optional[dict[str, Any]] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> R:
    """Run ``fn`` with a per-attempt timeout and the retry rules above; report
    usage once on success; raise :class:`LLMCallError` (or
    :class:`LLMTimeoutError`) on failure, chained to the last error."""
    started = time.monotonic()
    last: Optional[BaseException] = None
    for attempt in range(1, max(1, policy.attempts) + 1):
        try:
            result = await asyncio.wait_for(fn(), timeout=policy.timeout_s)
        except Exception as exc:  # noqa: BLE001 - classified below
            last = exc
            retryable = is_retryable(exc)
            if not retryable or attempt >= policy.attempts:
                kind = LLMTimeoutError if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else LLMCallError
                raise kind(
                    f"({name}) failed after {attempt} attempt(s): {type(exc).__name__}: {exc}",
                    attempts=attempt,
                    retryable=retryable,
                ) from exc
            hinted = retry_after_of(exc)
            wait = min(policy.max_backoff_s, hinted) if hinted is not None else random.uniform(
                0, min(policy.max_backoff_s, policy.base_backoff_s * 2 ** (attempt - 1))
            )
            log.info("(%s) attempt %d failed (%s); retrying in %.2fs", name, attempt, type(exc).__name__, wait)
            await sleep(wait)
            continue
        tokens_in, tokens_out = usage_of(result)
        await _emit(
            Usage(name=name, model=model, input_tokens=tokens_in, output_tokens=tokens_out,
                  duration_s=time.monotonic() - started, attempts=attempt, trace_id=current_trace_id(),
                  metadata=dict(metadata or {}))
        )
        return result
    raise AssertionError("unreachable") from last  # pragma: no cover
