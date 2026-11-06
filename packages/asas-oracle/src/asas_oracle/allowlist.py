"""A fail-closed allow-list for writes about PEOPLE on a shared test instance.

A shared Fusion test pod holds real people beside the scrubbed ones, and a
write there can make Fusion notify them (an application, a stage move, an
offer). So outside production a host sends a write about a person only when
BOTH the person's address and the record it touches are on the list.

Patterns are comma-separated, case-insensitive, ``*`` a wildcard. An empty
list allows NOTHING: forgetting to configure it stops writes rather than
letting them through. Production should not consult it at all, and a host is
wise to refuse to boot with one set there, so a test list can never quietly
narrow production.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase


def _patterns(raw: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    items = raw.split(",") if isinstance(raw, str) else list(raw or ())
    return tuple(p.strip().lower() for p in items if p and p.strip())


def _matches(value: str | None, patterns: tuple[str, ...]) -> bool:
    candidate = (value or "").strip().lower()
    return bool(candidate) and any(fnmatchcase(candidate, p) for p in patterns)


@dataclass(frozen=True)
class WriteAllowlist:
    """``emails`` and ``records`` as comma-separated strings or sequences."""

    emails: tuple[str, ...] = ()
    records: tuple[str, ...] = ()

    @classmethod
    def parse(
        cls,
        emails: str | list[str] | tuple[str, ...] | None,
        records: str | list[str] | tuple[str, ...] | None,
    ) -> WriteAllowlist:
        return cls(_patterns(emails), _patterns(records))

    @property
    def configured(self) -> bool:
        return bool(self.emails and self.records)

    def refusal(self, *, email: str | None, record: str | None) -> str | None:
        """Why this write must NOT be sent, in words safe to log, or ``None``
        to send it."""
        if not self.configured:
            return "The test allow-list is not set, so no write about a person is sent."
        if not _matches(email, self.emails):
            return "The person's address is not on the test allow-list."
        if not _matches(record, self.records):
            return "The record is not on the test allow-list."
        return None

    def allows(self, *, email: str | None, record: str | None) -> bool:
        return self.refusal(email=email, record=record) is None
