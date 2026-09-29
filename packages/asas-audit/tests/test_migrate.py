"""migrate(engine): fresh-create, idempotence, and the shared adopt guard."""

import pytest
import sqlalchemy as sa

import asas_audit
from asas_audit.migrate import VERSION_TABLE


def test_fresh_create(engine):
    asas_audit.migrate(engine)
    inspector = sa.inspect(engine)
    for table in ("asas_audit_event", "asas_audit_head", VERSION_TABLE):
        assert inspector.has_table(table), table


def test_idempotent(engine):
    asas_audit.migrate(engine)
    asas_audit.migrate(engine)


def test_an_unrelated_table_of_the_same_name_is_refused(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE asas_audit_event (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE asas_audit_head (org_id VARCHAR(64) PRIMARY KEY)"))
    with pytest.raises(RuntimeError, match="cannot adopt"):
        asas_audit.migrate(engine)


def test_a_partial_schema_is_refused(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE asas_audit_event (id INTEGER PRIMARY KEY)"))
    with pytest.raises(RuntimeError, match="missing"):
        asas_audit.migrate(engine)
