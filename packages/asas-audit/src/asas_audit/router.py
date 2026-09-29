"""Read-only HTTP surface: list events and verify the chain.

There is deliberately no write route. Events are appended by the host's own
service code inside its business transaction (``append(session, ...)``); an
endpoint that accepted arbitrary audit rows would let a client write history.
Auth is the host's: include the router behind whatever guard reads audit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from . import service


class AuditEventRead(BaseModel):
    seq: int
    event_id: str
    actor: str
    action: str
    resource_type: str
    resource_id: str
    payload: dict[str, Any]
    occurred_at: datetime


class AuditEventPage(BaseModel):
    items: list[AuditEventRead]
    #: Pass as ``before_seq`` for the next (older) page; None on the last page.
    next_before_seq: Optional[int]


class ChainBreakRead(BaseModel):
    seq: Optional[int]
    event_id: Optional[str]
    reason: str


class VerifyRead(BaseModel):
    intact: bool
    events_checked: int
    breaks: list[ChainBreakRead]
    summary: str


def build_routers(get_session: Callable) -> list[APIRouter]:
    """The audit router against the host's session dependency (a callable
    yielding a ``sqlmodel.Session``)."""
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/events", response_model=AuditEventPage)
    def list_events(
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        before_seq: Optional[int] = Query(None, ge=1),
        limit: int = Query(50, ge=1, le=200),
        session: Session = Depends(get_session),
    ) -> AuditEventPage:
        rows = service.list_events(
            session,
            resource_type=resource_type,
            resource_id=resource_id,
            actor=actor,
            action=action,
            before_seq=before_seq,
            limit=limit,
        )
        items = [AuditEventRead.model_validate(r, from_attributes=True) for r in rows]
        return AuditEventPage(items=items, next_before_seq=items[-1].seq if len(items) == limit else None)

    @router.get("/verify", response_model=VerifyRead)
    def verify(session: Session = Depends(get_session)) -> VerifyRead:
        report = service.verify(session)
        return VerifyRead(
            intact=report.is_intact,
            events_checked=report.events_checked,
            breaks=[ChainBreakRead(seq=b.seq, event_id=b.event_id, reason=b.reason) for b in report.breaks],
            summary=report.as_sentence(),
        )

    return [router]
