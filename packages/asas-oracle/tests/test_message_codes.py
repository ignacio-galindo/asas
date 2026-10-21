"""Fusion's message numbers, read off a refusal without showing it.

The texts are real refusals from an Oracle Fusion instance (Recruiting,
October 2026), shortened. The number is the stable part of a Fusion error:
the prose is translated and reworded, the number is not.
"""

import asyncio

import pytest

from asas_oracle import OracleUpstreamError, message_codes

from conftest import collection  # noqa: F401 - shared fixtures

REQUIRED = (
    "You need to enter a value for the Candidate Selection Process field. (IRC-1590142)"
)
TWO_INVALID = (
    "You need to enter a valid value for the Candidate Selection Process field. (IRC-1590143) "
    "You need to enter a valid value for the External Application Flow field. (IRC-1590143)"
)
LIFECYCLE = (
    "PhaseStepProcessor.activateStateStep()| Target state is not adjacent to current state "
    "TargetStateId=26 current state id = 27"
)


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        (REQUIRED, ("IRC-1590142",)),
        (TWO_INVALID, ("IRC-1590143",)),  # each once
        (REQUIRED + " (FND-2808)", ("IRC-1590142", "FND-2808")),  # in order
        (LIFECYCLE, ()),  # a framework exception carries none
        ("CurrentPhaseId=5 TargetPhaseId=2", ()),  # ids with '=' are not codes
        ("", ()),
        (None, ()),
    ],
)
def test_message_codes(text, codes):
    assert message_codes(text) == codes


def test_a_refusal_names_its_codes_and_keeps_its_body_to_itself(client, oracle):
    oracle.route("/recruitingJobRequisitions/REQ-1", (400, REQUIRED))
    with pytest.raises(OracleUpstreamError) as raised:
        asyncio.run(client.patch("/recruitingJobRequisitions/REQ-1", {"StateId": 26}))
    exc = raised.value
    assert exc.message_codes == ("IRC-1590142",)
    assert "Candidate Selection Process" not in str(exc), "the body never reaches the message"


def test_an_answer_with_no_body_has_no_codes():
    assert OracleUpstreamError("Oracle did not answer.").message_codes == ()
