"""Pydantic model -> the JSON schema a provider's STRICT structured output accepts.

Ported from a production AI service, which ported it from the OpenAI SDK to drop that dependency. Strict mode refuses schemas a
plain ``model_json_schema()`` produces all the time, and each refusal is a 400
at runtime, not a type error:

- every object needs ``additionalProperties: false``;
- every property must be listed in ``required`` (optionality is expressed as
  ``anyOf [..., null]``, which Pydantic already emits for ``Optional``);
- a ``$ref`` may not have sibling keys, so ``{"$ref": ..., "description": ...}``
  (what a documented nested field produces) is inlined, and the inlined copy is
  made strict too;
- a single-entry ``allOf`` (Pydantic's wrapper for a documented reference) is
  collapsed;
- ``default: null`` is dropped, since the schema is already nullable.

:func:`response_format` wraps the result in the ``json_schema`` envelope, and
fixes the one thing the port never did: the envelope's ``name`` must match
``^[a-zA-Z0-9_-]{1,64}$``, and a model's ``title`` ("Candidate Summary") does
not.
"""

from __future__ import annotations

import copy
import inspect
import re
from typing import Any, Union

import pydantic

_MISSING = object()


def to_strict_json_schema(model: Union[type[pydantic.BaseModel], pydantic.TypeAdapter]) -> dict[str, Any]:
    """The strict schema for a Pydantic model or ``TypeAdapter``. Never mutates
    the model's own cached schema."""
    if inspect.isclass(model) and issubclass(model, pydantic.BaseModel):
        schema = copy.deepcopy(model.model_json_schema())
    elif isinstance(model, pydantic.TypeAdapter):
        schema = copy.deepcopy(model.json_schema())
    else:
        raise TypeError(f"expected a Pydantic v2 model or TypeAdapter, got {model!r}")
    return _strict(schema, path=(), root=schema)


def schema_name(model: Any) -> str:
    """A name the ``json_schema`` envelope accepts, from the model's title."""
    raw = getattr(model, "__name__", None) or "response"
    if inspect.isclass(model) and issubclass(model, pydantic.BaseModel):
        raw = model.model_json_schema().get("title") or raw
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(raw)).strip("_")[:64]
    return name or "response"


def response_format(model: Union[type[pydantic.BaseModel], pydantic.TypeAdapter]) -> dict[str, Any]:
    """The ``response_format`` argument for strict structured output."""
    return {
        "type": "json_schema",
        "json_schema": {"name": schema_name(model), "strict": True, "schema": to_strict_json_schema(model)},
    }


def _strict(node: object, *, path: tuple[str, ...], root: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise TypeError(f"expected a dict at {'/'.join(path) or '<root>'}, got {node!r}")
    for defs_key in ("$defs", "definitions"):
        defs = node.get(defs_key)
        if isinstance(defs, dict):
            for name, sub in defs.items():
                _strict(sub, path=(*path, defs_key, name), root=root)
    if node.get("type") == "object" and "additionalProperties" not in node:
        node["additionalProperties"] = False
    properties = node.get("properties")
    if isinstance(properties, dict):
        node["required"] = list(properties)
        node["properties"] = {
            key: _strict(sub, path=(*path, "properties", key), root=root) for key, sub in properties.items()
        }
    items = node.get("items")
    if isinstance(items, dict):
        node["items"] = _strict(items, path=(*path, "items"), root=root)
    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        node["anyOf"] = [_strict(v, path=(*path, "anyOf", str(i)), root=root) for i, v in enumerate(any_of)]
    all_of = node.get("allOf")
    if isinstance(all_of, list):
        if len(all_of) == 1:
            node.update(_strict(all_of[0], path=(*path, "allOf", "0"), root=root))
            node.pop("allOf")
        else:
            node["allOf"] = [_strict(v, path=(*path, "allOf", str(i)), root=root) for i, v in enumerate(all_of)]
    if node.get("default", _MISSING) is None:
        node.pop("default")
    ref = node.get("$ref")
    if isinstance(ref, str) and len(node) > 1:
        resolved = _resolve(root, ref)
        # The node's own keys (a description, say) win over the referenced ones.
        node.update({**copy.deepcopy(resolved), **node})
        node.pop("$ref")
        return _strict(node, path=path, root=root)
    return node


def _resolve(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref {ref!r}: only local '#/...' references resolve")
    target: Any = root
    for part in ref[2:].split("/"):
        target = target[part]
        if not isinstance(target, dict):
            raise ValueError(f"$ref {ref!r} does not resolve to an object")
    return target
