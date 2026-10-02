"""Constraint engine — evaluates the declared rules against a proposed edit.

Model-free: it reads the current record with ``getattr`` (duck-typed; a ``Mapping``
is read by key) and overlays the incoming ``changes`` dict, so it never imports host
models. Two guards keep it well-behaved for partial updates:

  * a rule only fires when the edit **actually touches** one of its fields — so an
    unrelated update (e.g. just the name) is never blocked by pre-existing data;
  * a rule is skipped when any field it reads is missing/``None`` — you can't compare
    against an absent date.

Values are reduced to calendar dates before comparison: a ``date`` as is, a
``datetime`` by its date (an aware one read in the configured zone, see ``clock``),
an ISO string parsed. Anything else is a host programming error and raises, so it
surfaces in tests rather than as a 500 on the first real request.

Kinds are a registry (``register_kind``): each carries its arity and required
params so ``assert_rules_known`` can reject a malformed rule at boot. The built-in
kinds are mirrored by the browser client; a host-registered kind is server-only and
the client skips it (the server stays authoritative either way).
"""

import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from . import catalog, clock
from .rules import Rule, declared_rules, rules_for

Check = Callable[[list[date], date, Rule], bool]


@dataclass(frozen=True)
class Violation:
    field: str
    code: str
    message: str
    params: Mapping[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class KindSpec:
    """What a kind needs from a rule: how many fields it reads and which params it
    requires. ``params`` is a tuple of alternatives-groups: every group must have at
    least one of its names present (``(("days", "years"),)`` reads "days or years")."""

    check: Check
    arity: int
    params: tuple[tuple[str, ...], ...] = ()


# ── value coercion ─────────────────────────────────────────────────────────────


def as_date(value: Any, field: str = "?") -> Optional[date]:
    """Reduce ``value`` to a calendar date, or ``None`` for absent. Raises ``ValueError``
    for a value that cannot be a date — silently skipping would hide a wiring bug."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):  # before ``date``: datetime is a date subclass
        if value.tzinfo is not None:
            value = value.astimezone(clock.timezone())  # None → system local zone
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
        try:
            return as_date(datetime.fromisoformat(value), field)
        except ValueError:
            raise ValueError(
                f"validation field '{field}': '{value}' is not an ISO date or datetime"
            ) from None
    raise ValueError(
        f"validation field '{field}': cannot read a date from {type(value).__name__}"
    )


def _read(record: Any, field: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field)
    return getattr(record, field, None)


def _effective(
    record: Optional[Any],
    changes: Mapping[str, Any],
    context: Optional[Mapping[str, Any]],
    field: str,
) -> Any:
    """The value ``field`` would have after applying ``changes`` to ``record``.

    A **namespaced** field (``parent.field``, e.g. ``project.start_date``) is a related
    record's value, supplied by the caller in ``context``; it's never on the record or in
    the incoming changes."""
    if field in changes:
        return changes[field]
    if context and field in context:
        return context[field]
    if "." in field:
        return None  # namespaced parent field the caller didn't supply → skip the rule
    return _read(record, field) if record is not None else None


# ── calendar arithmetic (mirrored exactly by the browser client) ───────────────


def shift_years(day: date, years: int) -> date:
    """``day`` moved ``years`` years (negative = back), clamping a Feb-29 anchor to
    Feb-28 when the target year is not a leap year."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # Feb 29 → non-leap target year
        return day.replace(month=2, day=28, year=day.year + years)


def _bound(anchor: date, params: Mapping[str, Any], sign: int) -> date:
    """``anchor`` moved forward (``sign=+1``) or back (``-1``) by the ``years`` and/or
    ``days`` in ``params``. Years first, then days, so the Feb-29 clamp applies before
    the day offset — the client does it in the same order."""
    out = anchor
    if params.get("years") is not None:
        out = shift_years(out, sign * int(params["years"]))
    if params.get("days") is not None:
        out = out + timedelta(days=sign * int(params["days"]))
    return out


# ── built-in kinds ─────────────────────────────────────────────────────────────


def _not_future(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] <= today


def _not_past(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] >= today


def _max_age(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] >= _bound(today, rule.params, -1)


def _min_age(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] <= _bound(today, rule.params, -1)


def _max_future(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] <= _bound(today, rule.params, +1)


def _not_before(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] >= as_date(rule.params["date"], "params.date")


def _not_after(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] <= as_date(rule.params["date"], "params.date")


def _order(v: list[date], today: date, rule: Rule) -> bool:
    return v[0] < v[1] if rule.params.get("strict") else v[0] <= v[1]


def _max_span(v: list[date], today: date, rule: Rule) -> bool:
    return v[1] <= _bound(v[0], rule.params, +1)


def _min_span(v: list[date], today: date, rule: Rule) -> bool:
    return v[1] >= _bound(v[0], rule.params, +1)


_SPAN = (("days", "years"),)

_BUILTIN_KINDS: Mapping[str, KindSpec] = MappingProxyType(
    {
        "not_future": KindSpec(_not_future, 1),
        "not_past": KindSpec(_not_past, 1),
        "max_age": KindSpec(_max_age, 1, (("years",),)),
        "min_age": KindSpec(_min_age, 1, (("years",),)),
        "max_future": KindSpec(_max_future, 1, _SPAN),
        "not_before": KindSpec(_not_before, 1, (("date",),)),
        "not_after": KindSpec(_not_after, 1, (("date",),)),
        "order": KindSpec(_order, 2),
        "max_span": KindSpec(_max_span, 2, _SPAN),
        "min_span": KindSpec(_min_span, 2, _SPAN),
    }
)

_KINDS: dict[str, KindSpec] = dict(_BUILTIN_KINDS)


def register_kind(
    name: str,
    check: Check,
    *,
    arity: int,
    params: tuple[tuple[str, ...], ...] = (),
) -> None:
    """Add a host-specific kind. ``check(values, today, rule) -> bool`` receives the
    rule's field values already reduced to dates, in ``rule.fields`` order. A built-in
    name cannot be overridden — the browser client implements the built-ins by name,
    and redefining one would make the two sides disagree silently."""
    if name in _BUILTIN_KINDS:
        raise ValueError(f"validation kind '{name}' is built in and cannot be overridden")
    if arity < 1:
        raise ValueError("a validation kind reads at least one field")
    _KINDS[name] = KindSpec(check, arity, tuple(tuple(g) for g in params))


def known_kinds() -> tuple[str, ...]:
    """Every kind ``evaluate`` currently understands, built-in first."""
    return tuple(_KINDS)


def builtin_kinds() -> tuple[str, ...]:
    """The kinds the browser client also implements."""
    return tuple(_BUILTIN_KINDS)


# ── evaluation ─────────────────────────────────────────────────────────────────


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_message(message: str, params: Mapping[str, Any]) -> str:
    """Substitute simple ``{name}`` placeholders from ``params``; an unknown name, and
    anything that is not a bare ``{name}`` (``{0}``, ``{years:02d}``, ``{}``), is left
    as written. The same regex as the browser client's ``renderMessage`` — on purpose
    not ``str.format``, whose richer syntax the client would not honour."""
    def sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        return str(params[name]) if name in params else m.group(0)

    return _PLACEHOLDER.sub(sub, str(message))


def evaluate(
    entity: str,
    record: Optional[Any],
    changes: Mapping[str, Any],
    context: Optional[Mapping[str, Any]] = None,
    *,
    today: Optional[date] = None,
) -> list[Violation]:
    """Return the constraint violations for applying ``changes`` to ``record`` (``None``
    on create). ``context`` supplies related-record values for namespaced fields (see
    ``_effective``); ``today`` overrides the configured clock (see ``clock``). Only
    rules whose fields the edit touches, and whose values are all present, are checked.
    Violations come back in catalog order."""
    day = today if today is not None else clock.today()
    violations: list[Violation] = []
    for rule in rules_for(entity):
        if not any(f in changes for f in rule.fields):
            continue
        vals = [as_date(_effective(record, changes, context, f), f) for f in rule.fields]
        if any(v is None for v in vals):
            continue
        spec = _KINDS.get(rule.kind)
        if spec is None:
            raise ValueError(f"validation rule '{rule.code}' has unknown kind '{rule.kind}'")
        if not spec.check(vals, day, rule):
            violations.append(
                Violation(
                    rule.target,
                    rule.code,
                    render_message(rule.message, rule.params),
                    dict(rule.params),
                )
            )
    return violations


def assert_rules_known() -> None:
    """Fail loud at startup if the catalog is malformed: a rule that references a field
    not registered in the catalog (a typo, or a renamed model field), an unknown kind,
    the wrong number of fields for its kind, a missing required param, an unparseable
    ``date`` param, or two rules sharing a ``code`` (the client keys errors by code).
    The host calls this from its wiring after declaring rules and registering fields."""
    seen_codes: dict[str, str] = {}
    for rule in declared_rules():
        for field in rule.fields:
            # A namespaced `parent.field` is validated against the parent entity's
            # registered fields; a plain field against the rule's own entity.
            if "." in field:
                ns_entity, ns_field = field.split(".", 1)
            else:
                ns_entity, ns_field = rule.entity, field
            if not catalog.is_known(ns_entity, ns_field):
                raise ValueError(
                    f"validation rule '{rule.code}' references unknown field "
                    f"'{ns_entity}.{ns_field}'"
                )
        spec = _KINDS.get(rule.kind)
        if spec is None:
            raise ValueError(f"validation rule '{rule.code}' has unknown kind '{rule.kind}'")
        if len(rule.fields) != spec.arity:
            raise ValueError(
                f"validation rule '{rule.code}': kind '{rule.kind}' reads "
                f"{spec.arity} field(s), rule names {len(rule.fields)}"
            )
        for group in spec.params:
            if not any(rule.params.get(name) is not None for name in group):
                need = " or ".join(f"'{n}'" for n in group)
                raise ValueError(
                    f"validation rule '{rule.code}': kind '{rule.kind}' requires params {need}"
                )
        if "date" in rule.params:
            try:
                as_date(rule.params["date"], "params.date")
            except ValueError as e:
                raise ValueError(f"validation rule '{rule.code}': {e}") from None
        for name in ("years", "days"):
            if name in rule.params and not isinstance(rule.params[name], int):
                raise ValueError(
                    f"validation rule '{rule.code}': param '{name}' must be an int"
                )
        if rule.code in seen_codes and seen_codes[rule.code] != rule.entity:
            raise ValueError(
                f"validation code '{rule.code}' is declared on both "
                f"'{seen_codes[rule.code]}' and '{rule.entity}'"
            )
        if rule.code in seen_codes:
            raise ValueError(f"validation code '{rule.code}' is declared twice")
        seen_codes[rule.code] = rule.entity
