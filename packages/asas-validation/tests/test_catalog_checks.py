"""``assert_rules_known`` rejects a malformed catalog at boot: arity, params, date
params, duplicate codes, unknown kinds. Field-name checks are in
test_catalog_and_router.py."""

import pytest

from asas_validation import (
    Rule,
    assert_rules_known,
    builtin_kinds,
    declare_rules,
    evaluate,
    known_kinds,
    register_fields,
    register_kind,
)


def _declare(*rules):
    declare_rules(rules)
    register_fields("m", ["a", "b", "c"])


def test_wrong_arity_fails_loud():
    _declare(Rule("m", "order", ("a",), "x", "m.r"))
    with pytest.raises(ValueError, match="reads 2 field\\(s\\), rule names 1"):
        assert_rules_known()
    _declare(Rule("m", "not_future", ("a", "b"), "x", "m.r"))
    with pytest.raises(ValueError, match="reads 1 field"):
        assert_rules_known()


def test_missing_required_param_fails_loud():
    _declare(Rule("m", "max_age", ("a",), "x", "m.r"))
    with pytest.raises(ValueError, match="requires params 'years'"):
        assert_rules_known()
    _declare(Rule("m", "max_span", ("a", "b"), "x", "m.r"))
    with pytest.raises(ValueError, match="'days' or 'years'"):
        assert_rules_known()
    _declare(Rule("m", "max_span", ("a", "b"), "x", "m.r", params={"days": 3}))
    assert_rules_known()  # one of the alternatives is enough


def test_non_int_span_param_fails_loud():
    _declare(Rule("m", "max_age", ("a",), "x", "m.r", params={"years": "18"}))
    with pytest.raises(ValueError, match="'years' must be an int"):
        assert_rules_known()


def test_unparseable_date_param_fails_loud():
    _declare(Rule("m", "not_before", ("a",), "x", "m.r", params={"date": "01/01/2000"}))
    with pytest.raises(ValueError, match="m.r.*not an ISO date"):
        assert_rules_known()


def test_duplicate_code_fails_loud():
    """The client keys field errors by code; two rules sharing one are indistinguishable."""
    _declare(
        Rule("m", "not_future", ("a",), "x", "m.dup"),
        Rule("m", "not_past", ("b",), "x", "m.dup"),
    )
    with pytest.raises(ValueError, match="declared twice"):
        assert_rules_known()
    register_fields("n", ["a"])
    _declare(
        Rule("m", "not_future", ("a",), "x", "m.dup"),
        Rule("n", "not_past", ("a",), "x", "m.dup"),
    )
    with pytest.raises(ValueError, match="declared on both 'm' and 'n'"):
        assert_rules_known()


def test_a_valid_catalog_of_every_builtin_kind_passes():
    _declare(
        Rule("m", "not_future", ("a",), "x", "1"),
        Rule("m", "not_past", ("a",), "x", "2"),
        Rule("m", "max_age", ("a",), "x", "3", params={"years": 1}),
        Rule("m", "min_age", ("a",), "x", "4", params={"years": 1}),
        Rule("m", "max_future", ("a",), "x", "5", params={"days": 1}),
        Rule("m", "not_before", ("a",), "x", "6", params={"date": "2000-01-01"}),
        Rule("m", "not_after", ("a",), "x", "7", params={"date": "2000-01-01"}),
        Rule("m", "order", ("a", "b"), "x", "8", params={"strict": True}),
        Rule("m", "max_span", ("a", "b"), "x", "9", params={"years": 1}),
        Rule("m", "min_span", ("a", "b"), "x", "10", params={"days": 1}),
    )
    assert_rules_known()


def test_register_kind_is_server_only_and_cannot_shadow_a_builtin():
    register_kind("weekday", lambda v, today, rule: v[0].weekday() < 5, arity=1)
    assert "weekday" in known_kinds() and "weekday" not in builtin_kinds()
    _declare(Rule("m", "weekday", ("a",), "not a weekday", "m.weekday"))
    assert_rules_known()
    from datetime import date

    assert evaluate("m", None, {"a": date(2026, 9, 12)})[0].code == "m.weekday"  # a Saturday
    assert evaluate("m", None, {"a": date(2026, 9, 11)}) == []

    with pytest.raises(ValueError, match="built in"):
        register_kind("order", lambda v, t, r: True, arity=2)
    with pytest.raises(ValueError, match="at least one field"):
        register_kind("nothing", lambda v, t, r: True, arity=0)


def test_register_kind_params_are_enforced_at_boot():
    register_kind("gap", lambda v, t, r: True, arity=2, params=(("days",),))
    _declare(Rule("m", "gap", ("a", "b"), "x", "m.gap"))
    with pytest.raises(ValueError, match="requires params 'days'"):
        assert_rules_known()


def test_evaluate_names_an_unknown_kind_when_assert_was_skipped():
    _declare(Rule("m", "sideways", ("a",), "x", "m.kind"))
    from datetime import date

    with pytest.raises(ValueError, match="unknown kind 'sideways'"):
        evaluate("m", None, {"a": date(2026, 1, 1)})
