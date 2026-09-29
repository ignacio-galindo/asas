"""Timeouts, retries, Retry-After, usage and trace nesting."""

import asyncio
from types import SimpleNamespace

import pytest

import asas_llm
from asas_llm import LLMCallError, LLMTimeoutError, RetryPolicy, call


class HTTPError(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.response = SimpleNamespace(status_code=status, headers={"retry-after": retry_after} if retry_after else {})


def _flaky(*failures, result="ok"):
    queue = list(failures)
    calls = []

    async def fn():
        calls.append(1)
        if queue:
            raise queue.pop(0)
        return result

    return fn, calls


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def slept():
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    return waits, sleep


def test_retryable_failures_are_retried_and_retry_after_is_honoured(slept):
    waits, sleep = slept
    fn, calls = _flaky(HTTPError(429, retry_after="7"), HTTPError(503))
    assert _run(call(fn, name="t", policy=RetryPolicy(attempts=3, base_backoff_s=1), sleep=sleep)) == "ok"
    assert len(calls) == 3
    assert waits[0] == 7.0 and 0 <= waits[1] <= 2


def test_a_bad_request_is_not_retried(slept):
    _, sleep = slept
    fn, calls = _flaky(HTTPError(400))
    with pytest.raises(LLMCallError) as exc:
        _run(call(fn, name="t", sleep=sleep))
    assert len(calls) == 1 and exc.value.attempts == 1 and exc.value.retryable is False
    assert isinstance(exc.value.__cause__, HTTPError)


def test_retry_after_is_capped(slept):
    waits, sleep = slept
    fn, _ = _flaky(HTTPError(429, retry_after="3600"))
    _run(call(fn, name="t", policy=RetryPolicy(max_backoff_s=30), sleep=sleep))
    assert waits == [30]


def test_a_hung_call_times_out_on_every_attempt(slept):
    _, sleep = slept

    async def hang():
        await asyncio.sleep(10)

    with pytest.raises(LLMTimeoutError) as exc:
        _run(call(hang, name="t", policy=RetryPolicy(attempts=2, timeout_s=0.01), sleep=sleep))
    assert exc.value.attempts == 2


def test_usage_is_reported_once_per_success_in_every_shape():
    seen = []
    asas_llm.configure_usage_sink(seen.append)
    try:
        shapes = [
            SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)),  # OpenAI
            SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=3)),  # Anthropic
            SimpleNamespace(usage_metadata={"input_tokens": 2, "output_tokens": 1}),  # LangChain
        ]
        for shape in shapes:
            fn, _ = _flaky(result=shape)
            _run(call(fn, name="u", model="m"))
    finally:
        asas_llm.configure_usage_sink(None)
    assert [(u.input_tokens, u.output_tokens, u.total_tokens) for u in seen] == [(10, 5, 15), (7, 3, 10), (2, 1, 3)]
    assert all(u.attempts == 1 and u.model == "m" for u in seen)


def test_a_failing_sink_never_fails_the_call():
    def broken(usage):
        raise RuntimeError("metrics down")

    asas_llm.configure_usage_sink(broken)
    try:
        fn, _ = _flaky()
        assert _run(call(fn, name="u")) == "ok"
    finally:
        asas_llm.configure_usage_sink(None)


def test_nested_scopes_keep_the_outer_trace():
    with asas_llm.trace_scope("outer") as outer:
        with asas_llm.trace_scope("inner") as inner:
            assert inner == outer == "outer" == asas_llm.current_trace_id()
    assert asas_llm.current_trace_id() is None
    with asas_llm.trace_scope() as fresh:
        assert len(fresh) == 32
