"""The production providers build the SDK's provider over an explicit client,
with the timeout and retries a host set, never the SDK's defaults."""

from agents import OpenAIChatCompletionsModel, OpenAIProvider, OpenAIResponsesModel

from asas_agent.providers import DEFAULT_RETRIES, DEFAULT_TIMEOUT, azure_provider, gateway_provider


def test_a_gateway_provider_uses_the_responses_api_by_default():
    provider = gateway_provider(api_key="k", base_url="https://gw.example/v1", default_headers={"x-api-key": "g"})
    assert isinstance(provider, OpenAIProvider)
    model = provider.get_model("gpt-5.1")
    assert isinstance(model, OpenAIResponsesModel)
    client = model._client
    assert str(client.base_url).startswith("https://gw.example/v1")
    assert client.timeout == DEFAULT_TIMEOUT and client.max_retries == DEFAULT_RETRIES
    assert client.default_headers["x-api-key"] == "g"


def test_a_chat_completions_gateway_gets_the_chat_model():
    provider = gateway_provider(api_key="k", base_url="https://gw.example/v1", use_responses=False, timeout=9.0, max_retries=0)
    model = provider.get_model("gpt-5.1")
    assert isinstance(model, OpenAIChatCompletionsModel)
    assert model._client.timeout == 9.0 and model._client.max_retries == 0


def test_an_azure_provider_names_the_deployment():
    provider = azure_provider(api_key="k", endpoint="https://x.openai.azure.com", api_version="2025-03-01-preview", use_responses=False)
    model = provider.get_model("my-deployment")
    assert isinstance(model, OpenAIChatCompletionsModel)
    assert model.model == "my-deployment"
    assert "openai.azure.com" in str(model._client.base_url)
