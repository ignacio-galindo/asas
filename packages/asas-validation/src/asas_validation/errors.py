"""Framework glue: turn engine ``Violation``s into FastAPI's native 422 envelope
(``{"detail": [{"loc", "msg", "type", "ctx"?}, ...]}``) so the frontend handles our
semantic constraint errors and Pydantic's body-shape errors through one code path.
``ctx`` carries the rule's params (``{"years": 18}``) exactly as Pydantic v2 does for
its own constrained types, and only when there are any."""

from datetime import date
from typing import Any, Mapping, Optional

from fastapi import HTTPException

from .engine import Violation, evaluate


def to_detail(violations: list[Violation]) -> list[dict[str, Any]]:
    """The 422 ``detail`` list for ``violations`` — the shape the browser client's
    ``fromServer422`` reads back."""
    detail: list[dict[str, Any]] = []
    for v in violations:
        item: dict[str, Any] = {
            "loc": ["body", v.field],
            "msg": v.message,
            "type": f"value_error.{v.code}",
        }
        if v.params:
            item["ctx"] = dict(v.params)
        detail.append(item)
    return detail


def to_http(violations: list[Violation]) -> HTTPException:
    return HTTPException(status_code=422, detail=to_detail(violations))


def raise_if_invalid(
    entity: str,
    record: Optional[Any],
    changes: Mapping[str, Any],
    context: Optional[Mapping[str, Any]] = None,
    *,
    today: Optional[date] = None,
) -> None:
    """Evaluate the entity's rules for this edit and raise a 422 if anything fails.
    ``context`` carries related-record values for cross-entity rules (namespaced fields).
    Call in create/update routers right before applying the changes."""
    violations = evaluate(entity, record, changes, context, today=today)
    if violations:
        raise to_http(violations)
