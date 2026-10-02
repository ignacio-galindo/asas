"""Read-only endpoint exposing the declared rule set so clients can enforce the *same*
constraints locally — one source of truth for what the rules are. ETag-cached because
the rules are static per deploy. Built by a factory for contract consistency; it needs
no session dependency (this module has no database).

The payload is the contract the browser client (``client/asas-validation.js``)
reads: a list of ``{entity, kind, fields, field, code, message, params}``. Additive changes
only — the client is vendored into hosts and upgrades on its own schedule."""

import hashlib
import json
from typing import Optional

from fastapi import APIRouter, Header, Response

from .rules import Rule, declared_rules, rules_for


def serialize(rules: list[Rule]) -> list[dict]:
    """The wire form of ``rules`` — what ``GET /validation/rules`` returns and what the
    browser client consumes. ``message`` is the raw template; the client renders it."""
    return [
        {
            "entity": r.entity,
            "kind": r.kind,
            "fields": list(r.fields),
            "field": r.target,
            "code": r.code,
            "message": r.message,
            "params": r.params,
        }
        for r in rules
    ]


_serialize = serialize  # pre-0.12 private name, kept for anyone who reached in


def _etag(rules: list[Rule]) -> str:
    # Weak ETag (W/"…") on purpose: compression proxies (e.g. Render's edge) downgrade
    # strong ETags to weak on gzipped responses, so a strong tag never round-trips —
    # the client echoes W/"…" and revalidation always misses. Emitting the weak form
    # makes the round trip exact (the lookups module's ETag convention).
    # Hashed over the rules actually being served, not the full catalog: an ETag
    # names a representation, and each ``entity`` filter is a different body — one
    # catalog-wide tag would 304 entity B's request against entity A's cached rules.
    blob = json.dumps(serialize(rules), sort_keys=True, default=str)
    return 'W/"' + hashlib.md5(blob.encode()).hexdigest() + '"'


def build_router() -> APIRouter:
    """Build the ``/validation/rules`` read router. The host applies any auth guards
    when including it (composition-time, like every Asas router)."""

    router = APIRouter(prefix="/validation", tags=["validation"])

    @router.get("/rules")
    def get_rules(
        response: Response,
        entity: Optional[str] = None,
        if_none_match: Optional[str] = Header(default=None),
    ):
        """Return the validation rules, optionally filtered to one ``entity``."""
        rules = rules_for(entity) if entity else list(declared_rules())
        etag = _etag(rules)
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return serialize(rules)

    return router
