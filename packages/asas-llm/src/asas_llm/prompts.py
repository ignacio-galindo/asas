"""Prompts fetched from a store, cached, and rendered safely.

The store is the host's (Langfuse, a table, files): :func:`configure_prompt_source`
takes ``async (name, label) -> Prompt``. What this module adds is the part the
engine learned in production:

- **The cold fetch is the bound that matters.** A cached prompt costs nothing;
  a first fetch against a slow store holds the request. Each fetch has its own
  timeout (``fetch_timeout_s``), so a worst case is known in advance.
- **A stale prompt beats no prompt.** When a refresh fails, the last good copy
  keeps serving (and a warning says so) instead of failing the model call.
  Only a prompt that was never fetched raises :class:`PromptUnavailableError`.
- **Rendering does not eat JSON.** ``str.format`` treats every ``{`` in a
  prompt's JSON example as a placeholder and raises or mangles it. Prompts
  here use ``{{variable}}`` (Langfuse's own syntax); single braces are left
  alone, and a variable the prompt names but the caller did not pass is an
  error listing every missing name, never a silent blank.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

_VARIABLE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class PromptUnavailableError(LookupError):
    """The prompt has never been fetched and the store did not answer."""


class PromptRenderError(KeyError):
    """The prompt names variables the caller did not supply."""


@dataclass(frozen=True)
class Prompt:
    """A text prompt (``text``) or a chat prompt (``messages``: role/content dicts)."""

    name: str
    version: Optional[str] = None
    text: Optional[str] = None
    messages: tuple[dict[str, str], ...] = ()
    config: dict[str, Any] = field(default_factory=dict)

    def variables(self) -> set[str]:
        bodies = [self.text or ""] + [m.get("content", "") for m in self.messages]
        return {v for body in bodies for v in _VARIABLE.findall(body)}

    def render(self, **values: Any) -> "Prompt":
        missing = sorted(self.variables() - set(values))
        if missing:
            raise PromptRenderError(f"prompt {self.name!r} needs {', '.join(missing)}")

        def fill(body: str) -> str:
            return _VARIABLE.sub(lambda m: str(values[m.group(1)]), body)

        return Prompt(
            name=self.name,
            version=self.version,
            text=fill(self.text) if self.text is not None else None,
            messages=tuple({**m, "content": fill(m.get("content", ""))} for m in self.messages),
            config=dict(self.config),
        )


PromptSource = Callable[[str, Optional[str]], Awaitable[Prompt]]

_source: Optional[PromptSource] = None
_ttl_s = 300.0
_fetch_timeout_s = 8.0
_cache: dict[tuple[str, Optional[str]], tuple[float, Prompt]] = {}


def configure_prompt_source(
    fn: Optional[PromptSource], *, ttl_s: float = 300.0, fetch_timeout_s: float = 8.0
) -> None:
    """Where prompts come from, how long a copy is fresh, and how long one
    fetch may take. Reconfiguring drops the cache."""
    global _source, _ttl_s, _fetch_timeout_s
    _source, _ttl_s, _fetch_timeout_s = fn, ttl_s, fetch_timeout_s
    _cache.clear()


async def get_prompt(name: str, *, label: Optional[str] = None) -> Prompt:
    """The prompt, fresh from cache, fetched, or (store failing) the last good copy."""
    key = (name, label)
    held = _cache.get(key)
    now = time.monotonic()
    if held is not None and now - held[0] < _ttl_s:
        return held[1]
    if _source is None:
        raise PromptUnavailableError("no prompt source is configured (configure_prompt_source)")
    try:
        prompt = await asyncio.wait_for(_source(name, label), timeout=_fetch_timeout_s)
    except Exception as exc:  # noqa: BLE001 - a stale copy may still serve
        if held is not None:
            log.warning("prompt %r refresh failed (%s); serving the cached copy", name, type(exc).__name__)
            return held[1]
        raise PromptUnavailableError(f"prompt {name!r} could not be fetched: {exc}") from exc
    _cache[key] = (now, prompt)
    return prompt
