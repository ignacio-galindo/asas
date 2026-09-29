"""The two tables: the events, and each organisation's chain head.

``org_id`` is a STRING, not an int: a host keyed on UUIDs passes ``str(uuid)``
and one keyed on ints passes ``str(n)``, and neither needs a second package
version. The empty string is the platform's own chain (no organisation), which
keeps the head's primary key non-null on every engine.

Nothing here references a host table: no foreign keys, plain identifiers only,
so removing the package is a table drop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, DateTime, Index
from sqlmodel import Field, SQLModel


class AuditEvent(SQLModel, table=True):
    """One append-only event. ``seq`` is the global insertion order and the
    authoritative chain order within an organisation, even when many events
    share one ``occurred_at`` inside a transaction."""

    __tablename__ = "asas_audit_event"
    __table_args__ = (Index("ix_asas_audit_event_resource", "org_id", "resource_type", "resource_id"),)

    seq: Optional[int] = Field(default=None, primary_key=True)
    event_id: str = Field(max_length=36, unique=True)
    org_id: str = Field(default="", max_length=64, index=True)
    actor: str = Field(max_length=200)
    action: str = Field(max_length=128, index=True)
    resource_type: str = Field(max_length=64)
    resource_id: str = Field(max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    # From the caller's moment, NOT a server default, so the chain can be
    # re-derived from stored columns alone. Naive UTC (see chain.py).
    # A plain SQL DateTime: SQLModel's own type refuses naive values.
    occurred_at: datetime = Field(sa_column=Column(DateTime(), nullable=False))
    hash_prev: Optional[str] = Field(default=None, max_length=64)
    hash_current: str = Field(max_length=64)


class AuditHead(SQLModel, table=True):
    """Where an organisation's chain ends and how long it is.

    It is the serialization point for appends (a compare-and-set on
    ``last_hash``) and the verifier's evidence against a truncated tail."""

    __tablename__ = "asas_audit_head"

    org_id: str = Field(primary_key=True, max_length=64)
    last_hash: str = Field(max_length=64)
    events: int = Field(default=0)
    # The newest event's ``seq``. Within one organisation appends are serialized
    # on this row, so every event with ``seq <= last_seq`` committed before the
    # head did: the verifier reads the head FIRST and then exactly those events,
    # and an append landing mid-verify can never read as a truncated tail.
    last_seq: int = Field(default=0)
