"""The OIC invocation id (``X-Oracle-Ics-Instance-Id``) reaches the host.

Oracle Integration Cloud stamps this header on every call it routes. The host
has no view of the response, so the client surfaces it twice: on the
``on_request`` event for a call that answered, and on the raised
``OracleUpstreamError`` for one that failed, where it matters most."""

import asyncio

import httpx

from asas_oracle import (
    ICS_INSTANCE_ID_HEADER,
    OracleNotFoundError,
    OracleUpstreamError,
    RequestEvent,
)


def run(coro):
    return asyncio.run(coro)


INSTANCE = "WZ6wuN9HEfGEGzfDwhSaZA"


def test_the_header_name_is_the_one_oic_sets():
    assert ICS_INSTANCE_ID_HEADER == "X-Oracle-Ics-Instance-Id"


def test_a_successful_call_reports_the_instance_id_on_its_event(make_client, oracle):
    events: list[RequestEvent] = []
    oracle.route(
        "/grades",
        lambda rec: httpx.Response(
            200, json={"items": [], "hasMore": False}, headers={ICS_INSTANCE_ID_HEADER: INSTANCE}
        ),
    )
    run(make_client(on_request=events.append).get("/grades"))
    assert events[-1].instance_id == INSTANCE
    assert events[-1].status == 200


def test_a_call_without_the_header_reports_none(make_client, oracle):
    events: list[RequestEvent] = []
    oracle.route("/grades", (200, {"items": [], "hasMore": False}))
    run(make_client(on_request=events.append).get("/grades"))
    assert events[-1].instance_id is None


def test_a_failed_call_carries_the_instance_id_on_the_error(make_client, oracle):
    oracle.route(
        "/recruitingJobRequisitions/999",
        lambda rec: httpx.Response(
            404, text="no such requisition", headers={ICS_INSTANCE_ID_HEADER: INSTANCE}
        ),
    )
    try:
        run(make_client().get("/recruitingJobRequisitions/999"))
    except OracleNotFoundError as exc:
        assert isinstance(exc, OracleUpstreamError)
        assert exc.instance_id == INSTANCE
    else:  # pragma: no cover
        raise AssertionError("expected a 404 to raise")


def test_an_empty_header_is_reported_as_none(make_client, oracle):
    events: list[RequestEvent] = []
    oracle.route(
        "/grades",
        lambda rec: httpx.Response(
            200, json={"items": [], "hasMore": False}, headers={ICS_INSTANCE_ID_HEADER: ""}
        ),
    )
    run(make_client(on_request=events.append).get("/grades"))
    assert events[-1].instance_id is None
