"""Model providers for a host's runs: the production counterpart of the
eval-only :class:`~asas_agent.models.GatewayModels`.

A host hands the SDK a ``ModelProvider`` when it runs an agent
(``Runner.run(agent, input, run_config=RunConfig(model_provider=...))``).
These build the two a product on an OpenAI-compatible gateway or on Azure
OpenAI needs, from explicit values: the package reads no settings.

* :func:`gateway_provider`: OpenAI, or any gateway that speaks its API. The
  Responses API by default; ``use_responses=False`` for a gateway that serves
  chat completions only, in which case every run uses
  ``OpenAIChatCompletionsModel`` and the SDK features that need Responses
  (built-in tools, reasoning items) are off.
* :func:`azure_provider`: Azure OpenAI, where the model name is the deployment.

Both set the client's own timeout and retry count explicitly: the SDK's
defaults are ten minutes and two retries, and a run that waits ten minutes on
one call is a run nobody is still watching.

Imports the Agents SDK and the OpenAI client, so ``import asas_agent`` loads
this module only when one of its names is first used.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents import OpenAIProvider
from openai import AsyncAzureOpenAI, AsyncOpenAI

#: Seconds one model call may take, and how many times the client asks again
#: on a transient failure, unless the host says otherwise.
DEFAULT_TIMEOUT = 120.0
DEFAULT_RETRIES = 2


def gateway_provider(
    *,
    api_key: str,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
    use_responses: bool = True,
    default_headers: Mapping[str, str] | None = None,
    **client_options: Any,
) -> OpenAIProvider:
    """A provider over an OpenAI-compatible gateway. ``default_headers`` ride
    every request (a correlation id, a gateway key); ``client_options`` reach
    ``AsyncOpenAI``."""
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
        default_headers=dict(default_headers) if default_headers else None,
        **client_options,
    )
    return OpenAIProvider(openai_client=client, use_responses=use_responses)


def azure_provider(
    *,
    api_key: str,
    endpoint: str,
    api_version: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
    use_responses: bool = True,
    default_headers: Mapping[str, str] | None = None,
    **client_options: Any,
) -> OpenAIProvider:
    """A provider over Azure OpenAI. The model a run names is the deployment
    name; ``api_version`` is Azure's (a Responses-capable one for
    ``use_responses=True``)."""
    client = AsyncAzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
        timeout=timeout,
        max_retries=max_retries,
        default_headers=dict(default_headers) if default_headers else None,
        **client_options,
    )
    return OpenAIProvider(openai_client=client, use_responses=use_responses)


__all__ = ["DEFAULT_RETRIES", "DEFAULT_TIMEOUT", "azure_provider", "gateway_provider"]
