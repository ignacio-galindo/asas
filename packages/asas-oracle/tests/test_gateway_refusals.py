"""Refusals through an API gateway (AD Connect: webMethods / CentraSite in
front of the integration tier), against the same calls made direct.

The bodies are the gateway's real answers, as a host measured them. The point
of every test here is one question a write outbox has to get right: is this
the GATEWAY saying it does not carry the operation (hold the row, it resumes
once the registration exists), or FUSION saying something about the record
(fail it, or retry it)?
"""

import asyncio
import json

import httpx
import pytest

from asas_oracle import (
    OracleAlreadyExistsError,
    OracleFusionClient,
    OracleGatewayError,
    OracleLockedError,
    OracleNotFoundError,
    OracleSettings,
    OracleStateRefusedError,
    OracleUpstreamError,
    OracleWrongAddressError,
    WriteOutcome,
    classify,
)

GATEWAY = "https://adconnect.example.gov/gateway/ADR_API/1.0"
DIRECT = "https://pod.example.oraclecloud.com/hcmRestApi/resources/11.13.18.05"

#: Fusion's own REST error document, which a gateway never writes.
ORACLE_404 = (
    404,
    json.dumps({"title": "Not Found", "o:errorCode": "404", "o:errorDetails": []}),
    "application/vnd.oracle.adf.error+json",
)
PLAIN_404 = (404, "Resource not found.", "text/plain")
GW_404 = (404, "API Gateway encountered an error. Error Message: Resource not found.", "text/plain")
GW_405 = (405, "", "text/plain")
GW_415 = (415, "Unsupported Media Type", "text/plain")
GW_TRANSPORT = (500, "CASDK-0001 Transport Protocol Not Supported", "text/plain")
GW_UNMAPPED = (406, "CASDK-0041 The parameter [[RequisitionNumber]] could not be mapped", "text/plain")
HTML_404 = (404, "<html><body><h1>404 Not Found</h1></body></html>", "text/html")
LOCKED = (400, "Failed to lock the record: another user holds the lock.", "text/plain")
NOT_ADJACENT = (
    400,
    "PhaseStepProcessor.activateStateStep()| Target state is not adjacent to current state",
    "text/plain",
)
APPLICATION_MOVE = (
    400,
    "The job application can't be moved to the provided phase and state. (IRC-1590385)",
    "text/plain",
)
DUPLICATE = (
    400,
    "You need to enter a different number. There's already requisition number 2026-114. (IRC-1590053)",
    "text/plain",
)


def _client(base: str, answer: tuple[int, str, str]) -> OracleFusionClient:
    status, body, content_type = answer

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body.encode(), headers={"content-type": content_type})

    if "/gateway/" in base:
        settings = OracleSettings(
            base_url=base, username="integration", password="s3cret", gateway_api_key="k"
        )
    else:
        settings = OracleSettings(base_url=base, username="integration", password="s3cret")
    return OracleFusionClient(settings, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _raised(base: str, method: str, answer: tuple[int, str, str]) -> OracleUpstreamError:
    client = _client(base, answer)
    path = "/recruitingJobRequisitions/REQ-1"

    async def go():
        if method == "GET":
            await client.get(path, use_cache=False)
        elif method == "PATCH":
            await client.patch(path, {"Title": "x"})
        else:
            await client.post("/recruitingJobRequisitions", {"RequisitionNumber": "2026-114"})

    with pytest.raises(OracleUpstreamError) as raised:
        asyncio.run(go())
    return raised.value


# ── writes through the gateway ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer", [PLAIN_404, GW_404, GW_405, GW_415, GW_TRANSPORT, GW_UNMAPPED, HTML_404]
)
@pytest.mark.parametrize("method", ["PATCH", "POST"])
def test_a_write_the_gateway_does_not_carry_is_held_not_failed(method, answer):
    exc = _raised(GATEWAY, method, answer)
    assert isinstance(exc, OracleGatewayError), type(exc).__name__
    assert classify(exc).outcome is WriteOutcome.BLOCKED


def test_fusions_own_404_through_the_gateway_is_still_no_such_record():
    exc = _raised(GATEWAY, "PATCH", ORACLE_404)
    assert type(exc) is OracleNotFoundError
    assert classify(exc).outcome is WriteOutcome.PERMANENT


@pytest.mark.parametrize(
    ("answer", "cls", "outcome"),
    [
        (LOCKED, OracleLockedError, WriteOutcome.TRANSIENT),
        (NOT_ADJACENT, OracleStateRefusedError, WriteOutcome.PERMANENT),
        (APPLICATION_MOVE, OracleStateRefusedError, WriteOutcome.PERMANENT),
    ],
)
def test_fusions_words_through_the_gateway_win_over_the_gateways(answer, cls, outcome):
    """Through the gateway Fusion's refusals arrive as plain text; they must
    still read as Fusion's, not as the gateway refusing."""
    exc = _raised(GATEWAY, "PATCH", answer)
    assert isinstance(exc, cls) and not isinstance(exc, OracleGatewayError)
    assert classify(exc).outcome is outcome


def test_the_duplicate_through_the_gateway_is_already_exists():
    exc = _raised(GATEWAY, "POST", DUPLICATE)
    assert isinstance(exc, OracleAlreadyExistsError)


# ── reads through the gateway ───────────────────────────────────────────────


@pytest.mark.parametrize("answer", [GW_405, GW_404, GW_TRANSPORT, GW_UNMAPPED])
def test_a_read_the_gateway_does_not_carry(answer):
    assert isinstance(_raised(GATEWAY, "GET", answer), OracleGatewayError)


@pytest.mark.parametrize("answer", [PLAIN_404, ORACLE_404])
def test_a_bare_404_on_a_read_stays_no_such_record(answer):
    """Reading it as the gateway's would stop every caller on one missing
    person; on a read the gateway has to say so in its own words."""
    assert type(_raised(GATEWAY, "GET", answer)) is OracleNotFoundError


# ── the same answers, direct ────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_direct_a_plain_404_is_no_such_record(method):
    assert type(_raised(DIRECT, method, PLAIN_404)) is OracleNotFoundError


def test_direct_an_html_404_is_the_wrong_address_and_is_held():
    exc = _raised(DIRECT, "PATCH", HTML_404)
    assert isinstance(exc, OracleWrongAddressError)
    assert classify(exc).outcome is WriteOutcome.BLOCKED


def test_direct_a_405_is_not_read_as_a_gateway():
    exc = _raised(DIRECT, "PATCH", GW_405)
    assert not isinstance(exc, OracleGatewayError)
    assert classify(exc).outcome is WriteOutcome.PERMANENT
