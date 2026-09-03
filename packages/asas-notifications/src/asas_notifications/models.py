"""Notification rows + the per-channel delivery outbox (WXL-222).

``notification`` IS the in-app delivery (insert = enqueue, read_at = seen);
``notification_delivery`` exists only for external channels (email now, Slack/Teams
later) — one row per (notification, channel) the routing policy selects at emit
time. Enums are plain VARCHARs (``native_enum=False``, dual-engine rule).

**Identity columns are opaque strings.** ``org_id``, ``user_id`` and
``entity_id`` used to be ``int``. That reads as decoupling and is not: an integer
column is an assertion about the host's schema, namely that it numbers its users
and organisations sequentially. A host on UUID primary keys had nothing to put
there and no seam that widened it, so it could not adopt the package at all.

The package never interprets these values. It groups, filters and compares them,
all of which text does, so they are stored as VARCHAR and normalised at the
boundary by ``normalize_id``. An int host keeps passing ints and reads back their
decimal string; a UUID host passes its own keys. The visibility filter and the
context resolver are deliberately unaffected, because they are handed the host's
own values rather than the storage form: a filter written against ints that
silently stops dropping anyone is a leak, and that is the one failure the seam
exists to prevent.

``org_id`` follows the tenancy epic's mapping (WXL-218): notifications are tenant
data — org-scoped in the catalog, stamped from the producing request's context.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index
from sqlmodel import Field, SQLModel


# ── enums (stored as plain VARCHAR — native_enum=False) ──────────────────────


class Category(str, Enum):
    """What the notification means for the recipient (drives UI + email subject)."""

    action = "action"
    info = "info"
    warning = "warning"


class Urgency(str, Enum):
    """Channel/display policy tier; defaulted from category × reason at registration."""

    low = "low"
    normal = "normal"
    high = "high"


class Reason(str, Enum):
    """Why THIS recipient (GitHub participating-vs-watching, generalized)."""

    requested = "requested"
    participant = "participant"
    watching = "watching"


class DeliveryStatus(str, Enum):
    pending = "pending"
    sending = "sending"  # claimed by a dispatch pass (TEAMY-475); stale claims reclaim
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class Notification(SQLModel, table=True):
    __tablename__ = "notification"
    __table_args__ = (
        # The feed: WHERE user_id = ? [AND org_id = ?] AND archived_at IS (NOT)
        # NULL ORDER BY created_at DESC, id DESC. org_id sits second so the
        # org-scoped queries filter on the index while unscoped single-tenant
        # queries still use the user_id prefix. Subsumes the old single-column
        # user_id index (dropped in migration 0003). id trails as the ORDER BY
        # tiebreaker so tie-heavy batch emits still stream straight off the
        # index.
        Index(
            "ix_notification_user_org_archived_created",
            "user_id", "org_id", "archived_at", "created_at", "id",
        ),
        # The badge: WHERE user_id = ? [AND org_id = ?]
        # AND read_at IS NULL AND archived_at IS NULL.
        Index(
            "ix_notification_user_org_read_archived",
            "user_id", "org_id", "read_at", "archived_at",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    # Opaque host identity. No host FK, and deliberately no assertion about the
    # host's key TYPE either: see the module docstring. ``normalize_id`` in
    # service.py is the one place a value becomes one of these, and nothing in
    # the package parses them.
    #
    # 255 and not 64: a UUID is 36 characters, but a host whose principal
    # subject is an EMAIL can reach 254 (RFC 5321), and a column that truncates
    # or rejects a recipient is worse than a wide one. Postgres varchar stores
    # only what is present, so the declared bound costs nothing for short
    # values, including an int host's decimal strings.
    org_id: str = Field(index=True, max_length=255)
    user_id: str = Field(max_length=255)  # the recipient; indexed via the composites above
    kind: str = Field(index=True)  # e.g. "workflow.approval_requested"
    category: Category = Field(
        sa_column=Column(SAEnum(Category, native_enum=False), nullable=False)
    )
    urgency: Urgency = Field(
        sa_column=Column(SAEnum(Urgency, native_enum=False), nullable=False)
    )
    reason: Reason = Field(
        sa_column=Column(SAEnum(Reason, native_enum=False), nullable=False)
    )
    # Generic subject reference (never an FK — the package is entity-agnostic).
    entity_type: Optional[str] = None
    entity_id: Optional[str] = Field(default=None, max_length=255)
    title: str
    body: Optional[str] = None
    link: Optional[str] = None  # frontend deep link, e.g. "/teams/42"
    read_at: Optional[datetime] = None
    # Dealt with — out of the recipient's inbox. A separate axis from `read_at`:
    # reading is seeing, archiving is finishing, and a host may well want an
    # action notification to survive being read (Teamy TEAMY-692 does).
    archived_at: Optional[datetime] = None
    # Reserved for auto-clearing `action` notifications when the underlying task
    # completes. Deliberately unused: Teamy weighed it for TEAMY-692 and chose
    # the archive gesture instead, so nothing writes this column today.
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationDelivery(SQLModel, table=True):
    __tablename__ = "notification_delivery"
    __table_args__ = (
        # The dispatcher's scan (status = pending) and the stale-claim sweep
        # (status = sending AND claimed_at < cutoff). Prefix covers the old
        # single-column status index (dropped in migration 0003).
        Index("ix_notification_delivery_status_claimed", "status", "claimed_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    notification_id: int = Field(foreign_key="notification.id", index=True)
    channel: str = Field(index=True)  # "email" now; "slack"/"teams" later
    status: DeliveryStatus = Field(
        default=DeliveryStatus.pending,
        sa_column=Column(SAEnum(DeliveryStatus, native_enum=False), nullable=False),
    )
    attempts: int = Field(default=0)
    # When the row was CAS-claimed (status → sending); a claim older than
    # ``service.STALE_CLAIM_SECONDS`` belongs to a crashed pass and reclaims.
    claimed_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    last_error: Optional[str] = None
