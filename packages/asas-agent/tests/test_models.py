from types import SimpleNamespace

import pytest
from conftest import run
from openai import RateLimitError

from asas_agent import GatewayModels, MeasuredModel, usage_of


def throttled():
    """A 429 as the client raises it, built without a transport: only its type is read."""
    error = RateLimitError.__new__(RateLimitError)
    Exception.__init__(error, "slow down")
    return error


class Inner:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    async def get_response(self, *args, **kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def usage(**tokens):
    return SimpleNamespace(
        input_tokens=tokens.get("input", 0),
        input_tokens_details=SimpleNamespace(cached_tokens=tokens.get("cached", 0)),
        output_tokens=tokens.get("output", 0),
        output_tokens_details=SimpleNamespace(
            reasoning_tokens=tokens.get("reasoning", 0)
        ),
    )


def test_tokens_are_read_as_the_provider_counted_them():
    assert usage_of(usage(input=10, cached=4, output=3, reasoning=2)) == {
        "input_tokens": 10, "cached_tokens": 4, "output_tokens": 3, "reasoning_tokens": 2,
    }  # fmt: skip
    assert usage_of(None) == dict.fromkeys(
        ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens"), 0
    )


def test_a_throttled_call_is_waited_out_and_its_retries_counted(monkeypatch):
    monkeypatch.setattr(MeasuredModel, "THROTTLE_WAITS", (0, 0))
    measurements = []
    answer = SimpleNamespace(usage=usage(input=5, output=1))
    model = MeasuredModel(Inner(throttled(), throttled(), answer), measurements)
    assert run(model.get_response()) is answer
    [call] = measurements
    assert (call["ok"], call["retries"], call["usage"]["input_tokens"]) == (True, 2, 5)


def test_a_throttle_past_the_waits_fails_and_is_measured(monkeypatch):
    monkeypatch.setattr(MeasuredModel, "THROTTLE_WAITS", (0,))
    measurements = []
    model = MeasuredModel(Inner(throttled(), throttled()), measurements)
    with pytest.raises(RateLimitError):
        run(model.get_response())
    assert measurements[0] | {"seconds": 0} == {
        "seconds": 0, "ok": False, "error_type": "RateLimitError", "retries": 1,
    }  # fmt: skip


def test_any_other_failure_is_measured_and_raised():
    measurements = []
    with pytest.raises(TimeoutError):
        run(MeasuredModel(Inner(TimeoutError()), measurements).get_response())
    assert (measurements[0]["ok"], measurements[0]["error_type"]) == (
        False,
        "TimeoutError",
    )


def test_a_gateway_names_the_settings_it_lacks():
    with pytest.raises(
        ValueError, match="OPENAI_API_KEY, OPENAI_MODEL and OPENAI_BASE_URL"
    ):
        GatewayModels({"OPENAI_API_KEY": "k"}, 30)
    with pytest.raises(ValueError, match="KEY, MODEL and URL"):
        GatewayModels({}, 30, keys=("KEY", "MODEL", "URL"))


def test_a_gateway_hands_out_measured_models_sharing_one_list():
    gateway = GatewayModels(
        {"KEY": "k", "MODEL": "m", "URL": "https://gateway.invalid/v1"},
        30,
        keys=("KEY", "MODEL", "URL"),
    )
    try:
        first, second = gateway.model(), gateway.model()
        assert gateway.model_name == "m"
        assert first.measurements is second.measurements is gateway.measurements
        assert gateway.client.max_retries == 0
    finally:
        run(gateway.close())
