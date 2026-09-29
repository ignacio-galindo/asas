"""The read-only HTTP surface."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

import asas_audit


def _client(migrated) -> TestClient:
    def get_session():
        with Session(migrated) as s:
            yield s

    app = FastAPI()
    for router in asas_audit.build_routers(get_session):
        app.include_router(router)
    return TestClient(app)


def test_events_page_and_verify(migrated):
    with Session(migrated) as s:
        for i in range(3):
            asas_audit.append(s, action="a", resource_type="doc", resource_id=i, actor="u")
        s.commit()
    client = _client(migrated)
    page = client.get("/audit/events", params={"limit": 2}).json()
    assert [e["resource_id"] for e in page["items"]] == ["2", "1"]
    rest = client.get("/audit/events", params={"before_seq": page["next_before_seq"]}).json()
    assert [e["resource_id"] for e in rest["items"]] == ["0"] and rest["next_before_seq"] is None
    verdict = client.get("/audit/verify").json()
    assert verdict["intact"] is True and verdict["events_checked"] == 3
    assert verdict["summary"].startswith("The audit log for the platform is intact")


def test_there_is_no_write_route(migrated):
    client = _client(migrated)
    assert client.post("/audit/events", json={}).status_code == 405
