"""Model output -> text -> a validated Pydantic object, or a typed error.

The engine's runner returned ``None`` on every failure, which made "the model
said nothing", "the model said something unparseable" and "the network died"
the same value at every call site. This raises instead, keeping the raw text
(trimmed) on the error so a caller can log or retry with it.

What it tolerates, in order, because models do all of these in practice:

1. content that is not a string: a message object with ``.content``, a list of
   content parts (``[{"type": "text", "text": ...}]``, which Anthropic and
   multimodal OpenAI replies use), or a dict with ``text`` / ``output``;
2. a Markdown code fence around the JSON (```` ```json ... ``` ````);
3. prose before or after the JSON object;
4. malformed JSON (trailing commas, single quotes, a truncated tail) when the
   optional ``json-repair`` is installed (``asas-llm[repair]``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, TypeVar

import pydantic

T = TypeVar("T", bound=pydantic.BaseModel)

_NO = object()

_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.S)


class StructuredOutputError(ValueError):
    """The output could not be turned into the requested model."""

    def __init__(self, message: str, *, raw: str) -> None:
        super().__init__(message)
        self.raw = raw[:2000]


class EmptyOutputError(StructuredOutputError):
    """The model returned nothing at all."""


def extract_text(output: Any) -> str:
    """Normalise a model reply to plain text (see module doc, point 1)."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    content = getattr(output, "content", _NO) if not isinstance(output, dict) else _NO
    if content is not _NO:
        return extract_text(content)
    if isinstance(output, list):
        parts = []
        for part in output:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text", "output_text"):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "".join(parts)
    if isinstance(output, dict):
        for key in ("text", "output"):
            if isinstance(output.get(key), str):
                return output[key]
        if "content" in output:
            return extract_text(output["content"])
    return str(output)


def _json_candidate(text: str) -> str:
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    text = text.strip()
    if text[:1] in "{[":
        return text
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return text
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1] if end > start else text[start:]


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        try:
            from json_repair import repair_json
        except ImportError:
            raise
        return json.loads(repair_json(text))


def parse_structured(output: Any, model: type[T], *, context: Optional[str] = None) -> T:
    """Validate a model reply as ``model``. Raises :class:`EmptyOutputError` or
    :class:`StructuredOutputError`; never returns None."""
    label = f"({context}) " if context else ""
    text = extract_text(output)
    if not text.strip():
        raise EmptyOutputError(f"{label}the model returned no content", raw=text)
    candidate = _json_candidate(text)
    try:
        data = _loads(candidate)
    except ValueError as exc:
        raise StructuredOutputError(f"{label}the reply is not JSON: {exc}", raw=text) from exc
    try:
        return model.model_validate(data)
    except pydantic.ValidationError as exc:
        raise StructuredOutputError(
            f"{label}the reply does not match {model.__name__}: {exc.error_count()} error(s)", raw=text
        ) from exc
