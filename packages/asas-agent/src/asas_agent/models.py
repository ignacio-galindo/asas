"""Models for evals on a real gateway: every call measured, throttling waited out.

Imports the OpenAI Agents SDK, so `import asas_agent` loads this module only when one
of its names is first used.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from time import perf_counter
from typing import Any

from agents import Model, OpenAIProvider
from openai import AsyncOpenAI, RateLimitError


def usage_of(usage: Any) -> dict:
    """One model call's tokens, as the provider counted them: input (of which cached),
    output (of which reasoning). Zero when the provider sent none."""
    details_in = getattr(usage, "input_tokens_details", None)
    details_out = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "cached_tokens": int(getattr(details_in, "cached_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "reasoning_tokens": int(getattr(details_out, "reasoning_tokens", 0) or 0),
    }


class MeasuredModel(Model):
    """A model whose every call appends its seconds, outcome, retries and tokens to
    `measurements`."""

    def __init__(self, inner, measurements):
        self.inner, self.measurements = inner, measurements

    #: Seconds waited before each retry of a call the gateway throttled (429). A
    #: gateway may throttle a whole organisation, on and off, for a minute or so; a
    #: benchmark that dies on the first refusal measures the throttle, not the agent.
    #: Retries are counted in the measurement, never hidden.
    THROTTLE_WAITS = (5, 10, 20, 30, 30)

    async def get_response(self, *args, **kwargs):
        started = perf_counter()
        retries = 0
        while True:
            try:
                result = await self.inner.get_response(*args, **kwargs)
                break
            except RateLimitError:
                if retries >= len(self.THROTTLE_WAITS):
                    self.measurements.append(
                        {
                            "seconds": perf_counter() - started,
                            "ok": False,
                            "error_type": "RateLimitError",
                            "retries": retries,
                        }
                    )
                    raise
                await asyncio.sleep(self.THROTTLE_WAITS[retries])
                retries += 1
            except Exception as error:
                self.measurements.append(
                    {
                        "seconds": perf_counter() - started,
                        "ok": False,
                        "error_type": type(error).__name__,
                        "retries": retries,
                    }
                )
                raise
        self.measurements.append(
            {
                "seconds": perf_counter() - started,
                "ok": True,
                "retries": retries,
                "usage": usage_of(result.usage),
            }
        )
        return result

    async def stream_response(self, *args, **kwargs):
        async for item in self.inner.stream_response(*args, **kwargs):
            yield item


#: The settings a gateway is read from, in order: the key, the model, the base URL.
GATEWAY_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL")


class GatewayModels:
    """An OpenAI-compatible gateway as an eval reaches it: no client retries (the
    throttle is waited out and counted by MeasuredModel), one measurements list for
    every model it hands out. `keys` names the settings in `values` to read."""

    def __init__(
        self,
        values: Mapping[str, Any],
        timeout: float,
        *,
        keys: Sequence[str] = GATEWAY_KEYS,
    ):
        key, model, url = (values.get(k) for k in keys)
        if not all([key, model, url]):
            raise ValueError(
                f"Gateway test requires {keys[0]}, {keys[1]} and {keys[2]}"
            )
        self.model_name = model
        self.client = AsyncOpenAI(
            api_key=key, base_url=url, timeout=timeout, max_retries=0
        )
        self.provider = OpenAIProvider(openai_client=self.client, use_responses=True)
        self.measurements: list[dict] = []

    def model(self):
        return MeasuredModel(
            self.provider.get_model(self.model_name), self.measurements
        )

    async def close(self):
        await self.client.close()
