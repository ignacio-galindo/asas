"""Concurrent appends never fork the chain (Postgres only: SQLite serializes
writers outright, which is the property this test exists to prove on Postgres).

Many threads append to ONE organisation starting from an EMPTY chain, which is
the case a tail-row lock has nothing to lock for, and to one that already has
events. Every append must land, and the chain must verify intact."""

import threading

import pytest
from sqlmodel import Session

import asas_audit
from tests.conftest import TEST_URL

pytestmark = pytest.mark.skipif(not (TEST_URL or "").startswith("postgresql"), reason="Postgres only")


def _hammer(engine, org: str, threads: int, each: int) -> list[BaseException]:
    errors: list[BaseException] = []
    start = threading.Barrier(threads)

    def worker(n: int) -> None:
        try:
            start.wait()
            for i in range(each):
                with Session(engine) as s:
                    asas_audit.append(s, action="hit", resource_type="t", resource_id=f"{n}-{i}",
                                      actor=f"worker:{n}", org_id=org)
                    s.commit()
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    pool = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    return errors


def test_concurrent_first_appends_and_later_ones_keep_one_chain(migrated):
    assert _hammer(migrated, "busy", threads=8, each=10) == []
    assert _hammer(migrated, "busy", threads=8, each=5) == []
    with Session(migrated) as s:
        report = asas_audit.verify(s, org_id="busy")
    assert report.is_intact, report.breaks[:3]
    assert report.events_checked == 8 * 15
