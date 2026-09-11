"""The shared fixture both engines run: ``conformance/cases.json``.

The browser client exists so a form refuses exactly what the server refuses. That
promise is only as good as the test that holds the two implementations to the same
answers, so every kind's semantics — boundaries, Feb-29 clamping, the touch guard,
null-skips, namespaced context, message rendering — is written once as data and
asserted here and in ``client/test/conformance.test.js``. A case added on one side
only is a drift waiting to happen; add it to the JSON."""

import json
import pathlib
from datetime import date
from types import SimpleNamespace

import pytest

from asas_validation import Rule, declare_rules, evaluate

CASES_PATH = pathlib.Path(__file__).resolve().parent.parent / "conformance" / "cases.json"
CASES = json.loads(CASES_PATH.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_case(case):
    r = case["rule"]
    declare_rules(
        (
            Rule(
                entity="e",
                kind=r["kind"],
                fields=tuple(r["fields"]),
                message=r.get("message", "bad"),
                code=r["code"],
                params=r.get("params", {}),
            ),
        )
    )
    record = SimpleNamespace(**case["record"]) if "record" in case else None
    got = evaluate(
        "e",
        record,
        case["changes"],
        case.get("context"),
        today=date.fromisoformat(case["today"]),
    )
    assert [v.code for v in got] == case["expect"]
    if case.get("expect_field"):
        assert got[0].field == case["expect_field"]
    if case.get("expect_message"):
        assert got[0].message == case["expect_message"]


def test_every_builtin_kind_has_a_case():
    """A kind nobody wrote a conformance case for is a kind the client can drift on."""
    from asas_validation import builtin_kinds

    covered = {c["rule"]["kind"] for c in CASES}
    assert set(builtin_kinds()) <= covered, set(builtin_kinds()) - covered


def test_mapping_record_is_read_by_key():
    """A dict record (e.g. a row fetched raw) reads like an object record."""
    declare_rules((Rule("e", "order", ("start", "end"), "x", "e.order"),))
    got = evaluate("e", {"start": date(2026, 3, 1)}, {"end": date(2026, 2, 1)})
    assert [v.code for v in got] == ["e.order"]
