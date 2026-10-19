"""The SDK's run traces land in Langfuse one to one, masked, never failing
the run. A stub Langfuse client records the calls; the SDK's own trace and
span helpers produce the real shapes."""

from datetime import datetime

import pytest
from agents import set_trace_processors
from agents.tracing import agent_span, custom_span, function_span, generation_span, trace

from asas_agent.tracing import GENERATION_KINDS, LangfuseTracingProcessor, langfuse_id


class Client:
    def __init__(self, fail=False):
        self.calls: list[tuple[str, dict]] = []
        self.flushes = 0
        self.fail = fail

    def trace(self, **kw):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.calls.append(("trace", kw))

    def span(self, **kw):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.calls.append(("span", kw))

    def generation(self, **kw):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.calls.append(("generation", kw))

    def flush(self):
        self.flushes += 1


@pytest.fixture
def client():
    c = Client()
    set_trace_processors([LangfuseTracingProcessor(c)])
    yield c
    set_trace_processors([])


def _run_flow():
    with trace("my-flow", group_id="sess-1", metadata={"k": "v"}) as t:
        with agent_span(name="assistant", tools=["read"]):
            with generation_span(
                input=[{"role": "user", "content": "hi"}],
                output=[{"role": "assistant", "content": "hello"}],
                model="gpt-5.1",
                usage={"input_tokens": 3, "output_tokens": 2},
            ):
                pass
            with function_span(name="read", input='{"id": 1}', output="ok"):
                pass
            with custom_span(name="step", data={"a": 1}) as s:
                s.set_error({"message": "boom", "data": {"x": 1}})
    return t


def test_a_run_becomes_a_trace_in_the_sessions_and_its_spans_nest(client):
    t = _run_flow()
    kinds = [k for k, _ in client.calls]
    assert kinds == ["trace", "generation", "span", "span", "span"]
    opened = client.calls[0][1]
    assert opened["id"] == langfuse_id(t.trace_id) and len(opened["id"]) == 32
    assert opened["name"] == "my-flow" and opened["session_id"] == "sess-1" and opened["metadata"] == {"k": "v"}

    generation = client.calls[1][1]
    agent = client.calls[4][1]
    assert generation["trace_id"] == opened["id"]
    assert generation["parent_observation_id"] == agent["id"]
    assert agent["parent_observation_id"] is None and agent["name"] == "agent:assistant"
    assert agent["input"] == {"tools": ["read"]}
    assert isinstance(generation["start_time"], datetime) and generation["end_time"] >= generation["start_time"]


def test_a_generation_keeps_the_model_the_messages_and_the_usage(client):
    _run_flow()
    generation = client.calls[1][1]
    assert generation["model"] == "gpt-5.1"
    assert generation["input"] == [{"role": "user", "content": "hi"}]
    assert generation["output"] == [{"role": "assistant", "content": "hello"}]
    assert generation["usage_details"] == {"input_tokens": 3, "output_tokens": 2}
    assert generation["metadata"] == {"kind": "generation"}


def test_a_tool_call_keeps_its_arguments_and_result_and_an_error_is_the_level(client):
    _run_flow()
    tool = client.calls[2][1]
    assert tool["name"] == "function:read" and tool["input"] == '{"id": 1}' and tool["output"] == "ok"
    assert "level" not in tool
    step = client.calls[3][1]
    assert step["name"] == "custom:step" and step["input"] == {"data": {"a": 1}}
    assert step["level"] == "ERROR" and step["status_message"] == "boom"


def test_the_mask_is_applied_to_inputs_outputs_and_data():
    client = Client()

    def mask(*, data):
        return "[masked]" if isinstance(data, list) else data

    set_trace_processors([LangfuseTracingProcessor(client, mask=mask)])
    try:
        _run_flow()
    finally:
        set_trace_processors([])
    generation = client.calls[1][1]
    assert generation["input"] == "[masked]" and generation["output"] == "[masked]"
    assert client.calls[2][1]["output"] == "ok"  # a string is not a list: untouched


def test_a_failing_backend_never_fails_the_run(caplog):
    client = Client(fail=True)
    set_trace_processors([LangfuseTracingProcessor(client)])
    try:
        _run_flow()  # raises nothing
    finally:
        set_trace_processors([])
    assert any("could not" in r.getMessage() for r in caplog.records)


def test_flushing_at_trace_end_and_shutdown():
    client = Client()
    processor = LangfuseTracingProcessor(client, flush_on_trace_end=True)
    set_trace_processors([processor])
    try:
        _run_flow()
    finally:
        set_trace_processors([])
    assert client.flushes == 1
    processor.shutdown()
    assert client.flushes == 2


def test_ids_and_kinds():
    assert langfuse_id("trace_" + "a" * 32) == "a" * 32
    assert langfuse_id("span_abc") == "span_abc" and langfuse_id(None) is None
    assert GENERATION_KINDS == {"generation", "response"}


def test_import_asas_agent_does_not_load_the_sdk_for_this_module():
    import subprocess
    import sys
    from pathlib import Path

    src = str(Path(__file__).resolve().parents[1] / "src")
    code = "import sys, asas_agent; print('agents' in sys.modules, 'langfuse' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env={"PYTHONPATH": src, "PATH": ""})
    assert out.stdout.strip() == "False False"
