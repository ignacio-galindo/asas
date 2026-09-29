"""The pure chain: normalization and the verifier, with no database."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from asas_audit.chain import (
    chain_payload,
    compute_hash,
    normalize_payload,
    normalize_time,
    verify_rows,
)


def test_aware_and_naive_utc_hash_the_same():
    aware = datetime(2026, 9, 29, 12, 0, 0, 5, tzinfo=timezone(timedelta(hours=4)))
    naive = datetime(2026, 9, 29, 8, 0, 0, 5)
    assert normalize_time(aware) == naive
    assert chain_payload(event_id="e", org_id="", actor="a", action="x", resource_type="t",
                         resource_id="1", payload={}, occurred_at=aware)["occurred_at"] == \
        "2026-09-29T08:00:00.000005"


def test_the_payload_is_stored_as_it_is_hashed():
    uid = uuid.uuid4()
    body = normalize_payload({"b": Decimal("1.50"), "a": uid, "when": datetime(2026, 1, 1)})
    assert body == {"a": str(uid), "b": "1.50", "when": "2026-01-01 00:00:00"}
    assert normalize_payload(None) == {}


def _rows(n: int):
    rows, prev = [], None
    for i in range(1, n + 1):
        fields = dict(event_id=f"e{i}", org_id="o", actor="a", action="x", resource_type="t",
                      resource_id=str(i), payload={"i": i}, occurred_at=datetime(2026, 1, 1, 0, 0, i))
        h = compute_hash(prev, chain_payload(**fields))
        fields.pop("org_id")
        rows.append(SimpleNamespace(seq=i, hash_prev=prev, hash_current=h, **fields))
        prev = h
    return rows


def test_an_intact_chain_verifies_and_says_so():
    rows = _rows(3)
    report = verify_rows("o", rows, head_hash=rows[-1].hash_current, head_events=3)
    assert report.is_intact and report.events_checked == 3
    assert report.as_sentence() == "The audit log for organisation 'o' is intact across 3 events."


def test_an_edited_row_breaks_at_that_row():
    rows = _rows(3)
    rows[1].payload = {"i": 99}
    (only,) = verify_rows("o", rows).breaks
    assert (only.seq, only.reason) == (2, "hash mismatch")


def test_a_removed_middle_row_breaks_the_link_after_it():
    rows = _rows(3)
    del rows[1]
    (only,) = verify_rows("o", rows).breaks
    assert (only.seq, only.reason) == (3, "broken link")


def test_a_truncated_tail_is_caught_only_with_the_head():
    rows = _rows(3)
    head = rows[-1].hash_current
    shorter = rows[:2]
    assert verify_rows("o", shorter).is_intact  # a hash chain alone cannot see it
    (only,) = verify_rows("o", shorter, head_hash=head, head_events=3).breaks
    assert only.reason == "tail truncated"
    assert "tail truncated at the end of the log" in verify_rows(
        "o", shorter, head_hash=head, head_events=3).as_sentence()
