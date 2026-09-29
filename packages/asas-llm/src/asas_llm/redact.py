"""Keep media out of traces and logs.

A multimodal request carries its audio or image inline as base64, often
megabytes, and a tracer that records the request ships all of it to a
third-party store (and sometimes the personal data in it). The engine redacted
``input_audio.data`` for its transcription traces; this does the same for
every inline payload shape the OpenAI and Anthropic chat APIs use, leaving the
text parts and the structure intact so the trace is still readable.
"""

from __future__ import annotations

from typing import Any

PLACEHOLDER = "<omitted {n} chars>"


def _omit(value: Any) -> Any:
    return PLACEHOLDER.format(n=len(value)) if isinstance(value, str) else value


def _part(part: Any) -> Any:
    if not isinstance(part, dict):
        return part
    part = dict(part)
    if isinstance(part.get("input_audio"), dict):
        part["input_audio"] = {**part["input_audio"], "data": _omit(part["input_audio"].get("data"))}
    if isinstance(part.get("image_url"), dict):
        url = part["image_url"].get("url")
        if isinstance(url, str) and url.startswith("data:"):
            part["image_url"] = {**part["image_url"], "url": url.split(",", 1)[0] + "," + _omit(url)}
    if isinstance(part.get("source"), dict) and part["source"].get("type") == "base64":
        part["source"] = {**part["source"], "data": _omit(part["source"].get("data"))}
    if isinstance(part.get("file"), dict) and "file_data" in part["file"]:
        part["file"] = {**part["file"], "file_data": _omit(part["file"]["file_data"])}
    return part


def redact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of chat ``messages`` with every inline media payload replaced by
    its length. The input is never mutated."""
    out = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            content = [_part(p) for p in content]
        out.append({**message, "content": content})
    return out
