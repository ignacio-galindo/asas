"""The prompt registry: bounded fetches, stale-on-error, safe rendering."""

import asyncio

import pytest

import asas_llm
from asas_llm import Prompt, PromptRenderError, PromptUnavailableError


@pytest.fixture(autouse=True)
def _reset():
    yield
    asas_llm.configure_prompt_source(None)


def _run(coro):
    return asyncio.run(coro)


def test_rendering_fills_double_braces_and_leaves_json_alone():
    prompt = Prompt(name="p", text='Reply as {"score": <int>} about {{ topic }} for {{who}}.')
    assert prompt.render(topic="x", who="y").text == 'Reply as {"score": <int>} about x for y.'


def test_a_missing_variable_names_every_missing_one():
    prompt = Prompt(name="p", messages=({"role": "system", "content": "{{a}} and {{b}}"},))
    with pytest.raises(PromptRenderError, match="a, b"):
        prompt.render()


def test_a_cached_prompt_is_not_refetched_and_a_stale_one_survives_an_outage():
    fetches = []
    state = {"down": False}

    async def source(name, label):
        fetches.append(name)
        if state["down"]:
            raise ConnectionError("store down")
        return Prompt(name=name, version=str(len(fetches)), text="hi")

    asas_llm.configure_prompt_source(source, ttl_s=0.0)
    first = _run(asas_llm.get_prompt("greet"))
    state["down"] = True
    second = _run(asas_llm.get_prompt("greet"))  # ttl 0: refresh attempted, fails, stale copy served
    assert second == first and len(fetches) == 2

    asas_llm.configure_prompt_source(source, ttl_s=300)
    state["down"] = False
    _run(asas_llm.get_prompt("greet"))
    _run(asas_llm.get_prompt("greet"))
    assert len(fetches) == 3  # the second read came from the cache


def test_a_cold_fetch_is_bounded_and_a_never_seen_prompt_raises():
    async def slow(name, label):
        await asyncio.sleep(5)

    asas_llm.configure_prompt_source(slow, fetch_timeout_s=0.01)
    with pytest.raises(PromptUnavailableError):
        _run(asas_llm.get_prompt("never"))


def test_no_source_configured_says_so():
    with pytest.raises(PromptUnavailableError, match="configure_prompt_source"):
        _run(asas_llm.get_prompt("x"))
