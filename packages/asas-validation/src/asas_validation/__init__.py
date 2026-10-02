"""Asas validation — declarative temporal/cross-field constraint engine.

Config-as-data input validation: the host declares its rule catalog (pure data, in host
code) and this package evaluates it on every create/update, returning FastAPI-native
422 envelopes so semantic errors and body-shape errors share one client code path.
The same catalog is served over ``/validation/rules`` and evaluated in the browser by
the bundled client (``client/``), so a form can refuse what the server would refuse
without a second copy of the rules. Extracted from Teamy (epic TEAMY-466; design
record 0007 for the rule model, 0017 for the extraction).

Public surface — the Asas host contract (table-less variant: no session dependency,
no ``seed``, no ``migrate``):

- :func:`declare_rules` — the host declares its whole ``Rule`` catalog once at boot.
- :func:`register_fields` — the host registers each entity's real field names, then
  calls :func:`assert_rules_known` to fail loud on a malformed rule.
- :func:`configure` — host hook: the calendar zone (or a clock) "today" is read in.
- :func:`raise_if_invalid` — router hook: evaluate an edit, raise 422 on violations.
- :func:`evaluate` / :class:`Violation` / :func:`to_http` / :func:`to_detail` — the
  pieces, for callers that need them separately.
- :func:`register_kind` / :func:`known_kinds` / :func:`builtin_kinds` — the kind
  registry; host-registered kinds are server-only.
- :func:`build_router` — the ``/validation/rules`` read endpoint (ETag-cached); the
  host applies auth guards when including it.
"""

from .catalog import is_known, known_fields, register_fields
from .clock import configure, today
from .engine import (
    KindSpec,
    Violation,
    as_date,
    assert_rules_known,
    builtin_kinds,
    evaluate,
    known_kinds,
    register_kind,
    render_message,
    shift_years,
)
from .errors import raise_if_invalid, to_detail, to_http
from .router import build_router, serialize
from .rules import Rule, declare_rules, declared_rules, rules_for

__version__ = "0.12.0"

__all__ = [
    "KindSpec",
    "Rule",
    "Violation",
    "as_date",
    "assert_rules_known",
    "build_router",
    "builtin_kinds",
    "configure",
    "declare_rules",
    "declared_rules",
    "evaluate",
    "is_known",
    "known_fields",
    "known_kinds",
    "raise_if_invalid",
    "register_fields",
    "register_kind",
    "render_message",
    "rules_for",
    "serialize",
    "shift_years",
    "to_detail",
    "to_http",
    "today",
    "__version__",
]
