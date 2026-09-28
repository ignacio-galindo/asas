"""Asas rate limiting — token buckets with host-declared rules.

The host declares named ``Rule``s at boot (its own catalog, posture profiles,
and override reading stay host-side) and calls ``check(rule, key)`` on the hot
path — it either passes or raises a FastAPI-native 429 with ``Retry-After``.
Extracted from Teamy (anti-abuse TEAMY-334; extraction epic TEAMY-466, design
record 0017).

No DB writes, and no Redis unless the host asks for it: by default a
single-instance deployment gets exact limits from process memory, and a
scaled-out one gets per-instance limits (N times looser). A host running
several replicas passes a shared store once at boot
(``configure(store=RedisStore(client))``, the ``[redis]`` extra) and every
replica then draws from the same buckets; call sites do not change.

Public surface — the Asas host contract (table-less **and** router-less
variant: no session dependency, no ``seed``/``migrate``/``build_routers``):

- :class:`Rule` / :func:`declare` / :func:`rules` — the host's named rules.
- :func:`configure` — kill switch, bucket store, injectable clock (tests).
- :class:`BucketStore`: the store protocol, one atomic "refill and take a
  token" step plus ``clear``. :class:`MemoryStore` is the default;
  :class:`RedisStore` shares buckets across replicas.
- :func:`check` — consume one token or raise 429 with ``Retry-After``.
- :func:`allow` — the non-raising form: ``(allowed, retry_after_seconds)``.
- :func:`parse_overrides` — parse a ``"rule=count/window,…"`` deployment
  override string (malformed entries are logged and skipped).
- :func:`reset` / :func:`clear_counters` — test isolation hooks.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Protocol, Tuple, runtime_checkable

from fastapi import HTTPException

__version__ = "0.11.1"

__all__ = [
    "BucketStore",
    "MemoryStore",
    "RedisStore",
    "Rule",
    "allow",
    "check",
    "clear_counters",
    "configure",
    "declare",
    "parse_overrides",
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


@runtime_checkable
class BucketStore(Protocol):
    """Where buckets live. The engine resolves the rule, the kill switch and
    the hard block (``limit=0``) itself, so a store only ever sees a rule
    with a positive rate and capacity, and those semantics hold on every
    store.

    ``take`` is the whole bucket step as one atomic operation: refill the
    ``(rule.name, key)`` bucket for the time elapsed, then spend one token if
    there is one. It returns ``(allowed, retry_after_seconds)``; a missing
    bucket counts as full. Atomic means two concurrent callers can never
    both spend the last token, across every process that shares the store.
    A store owns its own failure policy: whatever ``take`` raises reaches
    the caller as a 500."""

    def take(self, rule: Rule, key: str) -> Tuple[bool, float]: ...

    def clear(self) -> None:
        """Drop every bucket this store holds (test isolation)."""
        ...


# Bounded guard against unbounded key growth (an attacker cycling emails/IPs):
# when exceeded, full-and-stale buckets are dropped (they're equivalent to
# absent buckets anyway).
_MAX_BUCKETS = 50_000

_rules: Dict[str, Rule] = {}
_enabled = True
_clock: Callable[[], float] = time.monotonic


class MemoryStore:
    """Process-memory buckets, the default store. Exact on one instance,
    per-instance (N times looser) across N replicas. Reads the engine clock,
    so ``configure(clock=...)`` drives it in tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: Dict[Tuple[str, str], Tuple[float, float]] = {}  # (tokens, stamp)

    def take(self, rule: Rule, key: str) -> Tuple[bool, float]:
        now = _clock()
        bkey = (rule.name, key)
        with self._lock:
            tokens, stamp = self._buckets.get(bkey, (float(rule.capacity), now))
            tokens = min(float(rule.capacity), tokens + (now - stamp) * rule.refill_per_second)
            if tokens >= 1.0:
                self._buckets[bkey] = (tokens - 1.0, now)
                self._prune_locked(now)
                return True, 0.0
            self._buckets[bkey] = (tokens, now)
            return False, (1.0 - tokens) / rule.refill_per_second

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def _prune_locked(self, now: float) -> None:
        if len(self._buckets) <= _MAX_BUCKETS:
            return
        for bkey in list(self._buckets):
            rule = _rules.get(bkey[0])
            tokens, stamp = self._buckets[bkey]
            # Only a bucket whose refill has reached capacity is equivalent to an
            # absent one. "Stale by one window" is NOT that when burst > limit: a
            # window refills `limit` tokens, so deleting the bucket would hand a
            # spent key its full burst back.
            if rule is None or tokens + (now - stamp) * rule.refill_per_second >= rule.capacity:
                del self._buckets[bkey]


# The default store, and module-level views of its state (tests inspect them).
_DEFAULT_STORE = MemoryStore()
_buckets = _DEFAULT_STORE._buckets
_lock = _DEFAULT_STORE._lock
_store: BucketStore = _DEFAULT_STORE

# configure()'s "clock not passed" sentinel: a plain time.monotonic default
# would silently revert an injected clock on every kill-switch toggle,
# comparing live bucket stamps against a different epoch (= full refill for
# every key).
_CLOCK_UNSET: Callable[[], float] = lambda: 0.0  # noqa: E731 — identity sentinel
# The same trap for the store: a kill-switch toggle must not swap a shared
# store back to process memory.
_STORE_UNSET = object()


def configure(
    *,
    enabled: bool = True,
    clock: Optional[Callable[[], float]] = _CLOCK_UNSET,
    store: Optional[BucketStore] = _STORE_UNSET,  # type: ignore[assignment]
) -> None:
    """Kill switch, bucket store, injectable clock. ``clock`` and ``store``
    are only touched when passed: ``configure(enabled=False)`` must not reset
    a previously injected clock or store. Pass ``clock=None`` to restore the
    default ``time.monotonic`` and ``store=None`` to restore the in-memory
    store. The clock only drives :class:`MemoryStore`; :class:`RedisStore`
    reads the Redis server clock. A store missing ``take``/``clear`` fails
    here, at boot, not on the first request."""
    global _enabled, _clock, _store
    if store is not _STORE_UNSET and store is not None and not isinstance(store, BucketStore):
        raise TypeError(f"store must implement take(rule, key) and clear(); got {store!r}")
    _enabled = enabled
    if clock is not _CLOCK_UNSET:
        _clock = clock if clock is not None else time.monotonic
    if store is not _STORE_UNSET:
        _store = store if store is not None else _DEFAULT_STORE


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
    """Drop all counters and rules (tests). Counters live in the configured
    store: on a shared store this clears them for every replica."""
    _store.clear()
    _rules.clear()


def clear_counters() -> None:
    """Drop counters but keep the declared rules (per-test isolation)."""
    _store.clear()


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
    return _store.take(rule, key)


def check(rule_name: str, key: str) -> None:
    """Consume one token or raise 429 with a Retry-After header."""
    allowed, retry_after = allow(rule_name, key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — try again later.",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


# Last: the Redis store imports its client lazily, so this never needs the
# [redis] extra.
from .redis_store import RedisStore  # noqa: E402
