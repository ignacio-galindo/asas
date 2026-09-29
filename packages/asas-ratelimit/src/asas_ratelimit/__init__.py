"""Asas rate limiting — in-process token buckets with host-declared rules.

The host declares named ``Rule``s at boot (its own catalog, posture profiles,
and override reading stay host-side) and calls ``check(rule, key)`` on the hot
path — it either passes or raises a FastAPI-native 429 with ``Retry-After``.
Extracted from Teamy (anti-abuse TEAMY-334; extraction epic TEAMY-466, design
record 0017).

Deliberately no Redis and no DB writes: a single-instance deployment gets
exact limits from process memory; a scaled-out one gets per-instance limits
(N× looser). The seam lets a shared backend replace the bucket store later
without touching call sites.

Public surface — the Asas host contract (table-less **and** router-less
variant: no session dependency, no ``seed``/``migrate``/``build_routers``):

- :class:`Rule` / :func:`declare` / :func:`rules` — the host's named rules.
- :func:`configure` — kill switch + injectable clock (tests).
- :func:`check` — consume one token or raise 429 with ``Retry-After``.
- :func:`allow` — the non-raising form: ``(allowed, retry_after_seconds)``.
- :func:`parse_overrides` — parse a ``"rule=count/window,…"`` deployment
  override string (malformed entries are logged and skipped).
- :func:`reset` / :func:`clear_counters` — test isolation hooks.
- :func:`configure_shared_bucket` — optional: one budget across every replica
  through a store the host injects (``shared.py``); :func:`allow_async` /
  :func:`check_async` spend from it and fall back to the in-process bucket
  whenever it gives no answer. :func:`redis_shared_bucket` is a ready adapter
  over a Redis client the host already runs.
- :func:`client_address` — the proxy-safe client key (``X-Forwarded-For`` read
  from the end, never the first entry).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from fastapi import HTTPException

__version__ = "0.12.0"

__all__ = [
    "REDIS_TOKEN_BUCKET_LUA",
    "Rule",
    "SharedBucket",
    "allow",
    "allow_async",
    "bucket_key",
    "check",
    "check_async",
    "clear_counters",
    "client_address",
    "configure",
    "configure_shared_bucket",
    "declare",
    "parse_overrides",
    "redis_shared_bucket",
    "reset",
    "rules",
    "__version__",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    """``limit`` tokens per ``window_seconds``, with bucket capacity ``burst``
    (defaults to ``limit`` — i.e. the full window may be spent at once)."""

    name: str
    limit: int
    window_seconds: float
    burst: Optional[int] = None

    @property
    def capacity(self) -> int:
        return self.burst if self.burst is not None else self.limit

    @property
    def refill_per_second(self) -> float:
        return self.limit / self.window_seconds


# Bounded guard against unbounded key growth (an attacker cycling emails/IPs):
# when exceeded, full-and-stale buckets are dropped (they're equivalent to
# absent buckets anyway).
_MAX_BUCKETS = 50_000

_lock = threading.Lock()
_rules: Dict[str, Rule] = {}
_buckets: Dict[Tuple[str, str], Tuple[float, float]] = {}  # (tokens, stamp)
_enabled = True
_clock: Callable[[], float] = time.monotonic

# configure()'s "clock not passed" sentinel: a plain time.monotonic default
# would silently revert an injected clock on every kill-switch toggle,
# comparing live bucket stamps against a different epoch (= full refill for
# every key).
_CLOCK_UNSET: Callable[[], float] = lambda: 0.0  # noqa: E731 — identity sentinel


def configure(
    *, enabled: bool = True, clock: Optional[Callable[[], float]] = _CLOCK_UNSET
) -> None:
    """Kill switch + injectable clock. ``clock`` is only touched when passed:
    ``configure(enabled=False)`` must not reset a previously injected clock.
    Pass ``clock=None`` to restore the default ``time.monotonic``."""
    global _enabled, _clock
    _enabled = enabled
    if clock is not _CLOCK_UNSET:
        _clock = clock if clock is not None else time.monotonic


def declare(rule: Rule) -> None:
    """Register a rule. Fails loud at boot on values the engine cannot
    enforce: a non-positive window has no defined refill rate, and a
    negative limit means nothing (``limit=0`` is legal — a hard block)."""
    if rule.window_seconds <= 0:
        raise ValueError(f"rule {rule.name!r}: window_seconds must be > 0")
    if rule.limit < 0:
        raise ValueError(f"rule {rule.name!r}: limit must be >= 0")
    _rules[rule.name] = rule


def rules() -> Dict[str, Rule]:
    return dict(_rules)


def reset() -> None:
    """Drop all counters and rules (tests)."""
    with _lock:
        _buckets.clear()
        _rules.clear()


def clear_counters() -> None:
    """Drop counters but keep the declared rules (per-test isolation)."""
    with _lock:
        _buckets.clear()


def parse_overrides(raw: str) -> Dict[str, Tuple[int, float]]:
    """``"rule=count/window,rule2=count/window"`` — malformed entries are
    logged and skipped (a bad env var must not take the API down)."""
    overrides: Dict[str, Tuple[int, float]] = {}
    for part in filter(None, (p.strip() for p in raw.split(","))):
        try:
            name, spec = part.split("=", 1)
            count, window = spec.split("/", 1)
            parsed = (int(count), float(window))
        except ValueError:
            log.warning("rate-limit override entry %r is malformed; ignored", part)
            continue
        # The same bounds declare() enforces — an entry declare() would
        # reject must not take the API down at boot either.
        if parsed[1] <= 0 or parsed[0] < 0:
            log.warning("rate-limit override entry %r is malformed; ignored", part)
            continue
        overrides[name.strip()] = parsed
    return overrides


def _prune_locked(now: float) -> None:
    if len(_buckets) <= _MAX_BUCKETS:
        return
    for bkey in list(_buckets):
        rule = _rules.get(bkey[0])
        tokens, stamp = _buckets[bkey]
        # Only a bucket whose refill has reached capacity is equivalent to an
        # absent one. "Stale by one window" is NOT that when burst > limit: a
        # window refills `limit` tokens, so deleting the bucket would hand a
        # spent key its full burst back.
        if rule is None or tokens + (now - stamp) * rule.refill_per_second >= rule.capacity:
            del _buckets[bkey]


def allow(rule_name: str, key: str) -> Tuple[bool, float]:
    """(allowed, retry_after_seconds). Unknown rules and disabled mode allow —
    a typo'd name must never lock an endpoint (assert names at boot)."""
    if not _enabled:
        return True, 0.0
    rule = _rules.get(rule_name)
    if rule is None:
        return True, 0.0
    if rule.limit <= 0 or rule.capacity <= 0:
        # A hard block ("this=0/60" in a deployment override): deny without
        # touching the refill math, whose rate is 0 — the division below
        # would otherwise turn every request into a ZeroDivisionError 500.
        return False, rule.window_seconds
    now = _clock()
    with _lock:
        tokens, stamp = _buckets.get((rule_name, key), (float(rule.capacity), now))
        tokens = min(float(rule.capacity), tokens + (now - stamp) * rule.refill_per_second)
        if tokens >= 1.0:
            _buckets[(rule_name, key)] = (tokens - 1.0, now)
            _prune_locked(now)
            return True, 0.0
        _buckets[(rule_name, key)] = (tokens, now)
        return False, (1.0 - tokens) / rule.refill_per_second


def check(rule_name: str, key: str) -> None:
    """Consume one token or raise 429 with a Retry-After header."""
    allowed, retry_after = allow(rule_name, key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — try again later.",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def _enabled_now() -> bool:
    """The kill switch, for the shared path (``shared.py``)."""
    return _enabled


from .shared import (  # noqa: E402 - the shared path builds on everything above
    REDIS_TOKEN_BUCKET_LUA,
    SharedBucket,
    allow_async,
    bucket_key,
    check_async,
    client_address,
    configure_shared_bucket,
    redis_shared_bucket,
)
