"""Engine semantics: the four kinds, the touch guard, None-skips, namespaced context."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pytest

from asas_validation import Rule, declare_rules, evaluate, raise_if_invalid

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


@dataclass
class Record:
    dob: Optional[date] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


def _declare(*rules):
    declare_rules(rules)


def test_not_future():
    _declare(Rule("m", "not_future", ("dob",), "future", "m.dob_future"))
    assert evaluate("m", None, {"dob": TOMORROW})[0].code == "m.dob_future"
    assert evaluate("m", None, {"dob": TODAY}) == []


def test_not_past():
    _declare(Rule("m", "not_past", ("due",), "past", "m.due_past"))
    assert evaluate("m", None, {"due": YESTERDAY})[0].code == "m.due_past"
    assert evaluate("m", None, {"due": TODAY}) == []


def test_order_and_violation_targets_last_own_field():
    _declare(Rule("m", "order", ("start_date", "end_date"), "order", "m.order"))
    v = evaluate("m", None, {"start_date": TODAY, "end_date": YESTERDAY})
    assert v[0].code == "m.order" and v[0].field == "end_date"
    assert evaluate("m", None, {"start_date": TODAY, "end_date": TODAY}) == []


def test_max_age():
    _declare(Rule("m", "max_age", ("dob",), "old", "m.too_old", params={"years": 150}))
    too_old = TODAY.replace(year=TODAY.year - 151)
    assert evaluate("m", None, {"dob": too_old})[0].code == "m.too_old"
    assert evaluate("m", None, {"dob": TODAY.replace(year=TODAY.year - 100)}) == []


def test_untouched_rule_never_fires():
    """Pre-existing bad data must not block an unrelated edit."""
    _declare(Rule("m", "not_future", ("dob",), "future", "m.dob_future"))
    record = Record(dob=TOMORROW)  # already invalid on the record
    assert evaluate("m", record, {"start_date": TODAY}) == []


def test_touched_rule_reads_record_for_other_fields():
    _declare(Rule("m", "order", ("start_date", "end_date"), "order", "m.order"))
    record = Record(start_date=TODAY)
    assert evaluate("m", record, {"end_date": YESTERDAY})[0].code == "m.order"


def test_none_values_skip():
    _declare(Rule("m", "order", ("start_date", "end_date"), "order", "m.order"))
    assert evaluate("m", Record(), {"end_date": YESTERDAY}) == []


def test_namespaced_field_reads_context_and_skips_without_it():
    _declare(
        Rule("child", "order", ("parent.start_date", "when"), "win", "child.before_parent")
    )
    bad = evaluate("child", None, {"when": YESTERDAY}, context={"parent.start_date": TODAY})
    assert bad[0].code == "child.before_parent" and bad[0].field == "when"
    # no context supplied → rule skipped, backend still safe on submit paths that have it
    assert evaluate("child", None, {"when": YESTERDAY}) == []


def test_raise_if_invalid_is_fastapi_422():
    from fastapi import HTTPException

    _declare(Rule("m", "not_future", ("dob",), "No future dates.", "m.dob_future"))
    with pytest.raises(HTTPException) as e:
        raise_if_invalid("m", None, {"dob": TOMORROW})
    assert e.value.status_code == 422
    assert e.value.detail == [
        {"loc": ["body", "dob"], "msg": "No future dates.", "type": "value_error.m.dob_future"}
    ]


def test_422_detail_carries_params_as_ctx_only_when_present():
    """Pydantic v2 puts a constrained type's parameters in ``ctx``; ours land in the
    same place so one client renderer serves both. No params → no ``ctx`` key, so
    the pre-0.12 envelope is byte-identical for existing rules."""
    from fastapi import HTTPException

    from asas_validation import to_detail

    _declare(
        Rule("m", "not_future", ("dob",), "No future dates.", "m.dob_future"),
        Rule("m", "min_age", ("dob",), "At least {years}.", "m.too_young", params={"years": 18}),
    )
    with pytest.raises(HTTPException) as e:
        raise_if_invalid("m", None, {"dob": TOMORROW})
    assert e.value.detail == [
        {"loc": ["body", "dob"], "msg": "No future dates.", "type": "value_error.m.dob_future"},
        {"loc": ["body", "dob"], "msg": "At least 18.", "type": "value_error.m.too_young", "ctx": {"years": 18}},
    ]
    assert to_detail([]) == []


def test_render_message_matches_the_client_exactly():
    """Bare ``{name}`` only: format specs, positional and empty braces stay as
    written, because the browser client renders with the same regex and a
    message that renders on one side only is a drift."""
    from asas_validation import render_message

    assert render_message("{years} and {nope}", {"years": 3}) == "3 and {nope}"
    assert render_message("{years:02d} {0} {} {{x}}", {"years": 3}) == "{years:02d} {0} {} {{x}}"
    assert render_message("plain", {}) == "plain"
    assert render_message("{strict}", {"strict": True}) == "True"
