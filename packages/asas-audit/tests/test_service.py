"""append / verify / list_events against a real database."""

import uuid

import pytest
from sqlalchemy import text
from sqlmodel import Session

import asas_audit
from tests.conftest import drop_append_only_triggers


def _append(session, n: int = 1, **over):
    events = []
    for i in range(n):
        kwargs = dict(action="thing.changed", resource_type="thing", resource_id=i, actor="user:1",
                      payload={"i": i})
        kwargs.update(over)
        events.append(asas_audit.append(session, **kwargs))
    return events


def test_appended_events_chain_and_verify(session):
    first, second = _append(session, 2)
    session.commit()
    assert first.hash_prev is None and second.hash_prev == first.hash_current
    report = asas_audit.verify(session)
    assert report.is_intact and report.events_checked == 2


def test_the_append_rides_the_callers_transaction(session):
    _append(session, 1)
    session.rollback()
    assert asas_audit.verify(session).events_checked == 0
    assert session.execute(text("SELECT COUNT(*) FROM asas_audit_head")).scalar() == 0


def test_organisations_have_separate_chains(session):
    org = uuid.uuid4()
    (a,) = _append(session, 1, org_id=org)
    (b,) = _append(session, 1, org_id=7)
    session.commit()
    assert a.hash_prev is None and b.hash_prev is None  # each is the first of its chain
    assert asas_audit.verify(session, org_id=org).events_checked == 1
    assert asas_audit.verify(session, org_id="7").events_checked == 1
    assert asas_audit.verify(session).events_checked == 0


def test_the_org_resolver_scopes_appends_and_reads(session):
    asas_audit.configure_org_resolver(lambda s: "acme")
    _append(session, 2)
    session.commit()
    assert {e.org_id for e in asas_audit.list_events(session)} == {"acme"}
    assert asas_audit.verify(session).events_checked == 2


def test_a_row_rewritten_behind_the_apps_back_is_found(migrated):
    with Session(migrated) as s:
        _append(s, 3)
        s.commit()
    drop_append_only_triggers(migrated)
    with Session(migrated) as s:
        s.execute(text("UPDATE asas_audit_event SET actor = 'someone-else' WHERE seq = 2"))
        s.commit()
        report = asas_audit.verify(s)
    assert [(b.seq, b.reason) for b in report.breaks] == [(2, "hash mismatch")]


def test_deleting_the_newest_events_is_found(migrated):
    with Session(migrated) as s:
        _append(s, 3)
        s.commit()
    drop_append_only_triggers(migrated)
    with Session(migrated) as s:
        s.execute(text("DELETE FROM asas_audit_event WHERE seq = 3"))
        s.commit()
        report = asas_audit.verify(s)
    assert [b.reason for b in report.breaks] == ["tail truncated"]


def test_list_events_is_newest_first_and_keyset_paged(session):
    _append(session, 5)
    _append(session, 1, resource_type="other")
    session.commit()
    page = asas_audit.list_events(session, resource_type="thing", limit=2)
    assert [e.resource_id for e in page] == ["4", "3"]
    older = asas_audit.list_events(session, resource_type="thing", before_seq=page[-1].seq, limit=10)
    assert [e.resource_id for e in older] == ["2", "1", "0"]


@pytest.mark.parametrize("verb", ["UPDATE asas_audit_event SET actor = 'x'", "DELETE FROM asas_audit_event"])
def test_the_database_refuses_to_change_history(session, verb):
    _append(session, 1)
    session.commit()
    with pytest.raises(Exception, match="append-only"):
        session.execute(text(verb))
        session.flush()
    session.rollback()
    assert session.execute(text("SELECT COUNT(*) FROM asas_audit_event")).scalar() == 1
