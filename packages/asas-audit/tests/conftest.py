"""Standalone package fixtures.

Engine: a SQLite temp file by default; Postgres when TEST_DATABASE_URL is set
(the CI matrix runs both). Schema always comes from the package's own
migration chain, so the chain and its triggers are exercised on every run.
"""

import os
import tempfile
import uuid

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

import asas_audit

TEST_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture()
def engine():
    if TEST_URL:
        eng = create_engine(TEST_URL)
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    else:
        path = os.path.join(tempfile.gettempdir(), f"asas_audit_{uuid.uuid4().hex}.db")
        eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    yield eng
    eng.dispose()
    if not TEST_URL:
        os.unlink(path)


@pytest.fixture()
def migrated(engine):
    asas_audit.migrate(engine)
    return engine


@pytest.fixture()
def session(migrated):
    with Session(migrated) as s:
        yield s
    asas_audit.configure_org_resolver(None)


def drop_append_only_triggers(engine) -> None:
    """What an attacker with raw database access would do first."""
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("DROP TRIGGER asas_audit_event_no_change ON asas_audit_event"))
        else:
            conn.execute(text("DROP TRIGGER asas_audit_event_no_update"))
            conn.execute(text("DROP TRIGGER asas_audit_event_no_delete"))
