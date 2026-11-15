"""A host's correlation id rides every request (``correlation_id`` hook).

An integration layer in front of Fusion (an OIC business identifier, a
gateway's log) can then name the same call the host's own logs do. The hook
is asked per request, so each call carries the id of the work in hand."""

import asyncio

from asas_oracle import OracleSettings

from conftest import BASE


def run(coro):
    return asyncio.run(coro)


def test_every_verb_carries_the_hooks_id(make_client, oracle):
    ids = iter(["read-1", "write-2"])
    client = make_client(correlation_id=lambda: next(ids))
    run(client.get("/grades"))
    run(client.patch("/recruitingJobRequisitions/1", {"StateId": 29}))
    assert [c.headers.get("X-Correlation-Id") for c in oracle.calls] == ["read-1", "write-2"]


def test_no_hook_and_an_empty_answer_send_nothing(make_client, oracle):
    run(make_client().get("/grades"))
    run(make_client(correlation_id=lambda: None).get("/grades"))
    run(make_client(correlation_id=lambda: "").get("/grades"))
    assert all("X-Correlation-Id" not in c.headers for c in oracle.calls)


def test_the_header_name_is_configurable_and_empty_turns_it_off(make_client, oracle):
    named = OracleSettings(
        base_url=BASE, username="u", password="p", correlation_header="X-Request-Id"
    )
    off = OracleSettings(base_url=BASE, username="u", password="p", correlation_header="")
    run(make_client(named, correlation_id=lambda: "abc").get("/grades"))
    run(make_client(off, correlation_id=lambda: "abc").get("/grades"))
    first, second = oracle.calls
    assert first.headers.get("X-Request-Id") == "abc"
    assert "X-Correlation-Id" not in first.headers
    assert "X-Correlation-Id" not in second.headers and "X-Request-Id" not in second.headers


def test_a_header_the_call_passes_itself_wins(make_client, oracle):
    client = make_client(correlation_id=lambda: "hook")
    run(client.request("GET", "/grades", headers={"X-Correlation-Id": "mine"}))
    assert oracle.calls[-1].headers.get("X-Correlation-Id") == "mine"


def test_a_hook_that_raises_never_costs_the_call(make_client, oracle, caplog):
    def broken():
        raise RuntimeError("no context")

    body = run(make_client(correlation_id=broken).get("/grades"))
    assert body["items"] == []
    assert "X-Correlation-Id" not in oracle.calls[-1].headers
    assert any("correlation_id hook raised" in r.message for r in caplog.records)


def test_the_id_is_not_part_of_the_cache_key(make_client, oracle):
    ids = iter(["a", "b"])
    client = make_client(correlation_id=lambda: next(ids))
    run(client.get("/grades"))
    run(client.get("/grades"))
    # The second read is served from the cache although the id changed.
    assert len(oracle.calls_to("/grades")) == 1
