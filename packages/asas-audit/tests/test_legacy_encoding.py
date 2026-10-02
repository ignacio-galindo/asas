"""Adopting a hash chain a host already keeps.

The case this exists for: a host ran its own audit chain before this package
existed (this package was extracted from one), the two drifted, and the host's
stored fingerprints no longer match what this package would compute. They cannot
be recomputed, because the append-only trigger refuses the UPDATE and because a
history rewritten to satisfy a new verifier is not evidence of anything. So the
old rows keep their old bytes, name the encoding that produced them, and verify
under it.

``host_*`` below is a **copy** of the reference host's algorithm, kept minimal
and deliberately not imported: the test must pin the bytes that host wrote in
production, not whatever a future version of it computes. It differs from this
package's current encoding in the tenant key (``tenant_id``) and in spelling the
timestamp with a bare ``datetime.isoformat()``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from sqlalchemy import JSON, DateTime, LargeBinary, bindparam, text
from sqlmodel import Session

import asas_audit
import asas_tenancy
from asas_audit import chain
from asas_audit.chain import (
    CURRENT_ENCODING,
    ChainEncoding,
    UnknownEncodingError,
    verify_rows,
)

# ── the host's algorithm, copied ────────────────────────────────────────────


def host_canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def host_chain_payload(
    *, event_id, tenant_id, actor, action, resource_type, resource_id, payload,
    occurred_at,
) -> dict[str, Any]:
    return {
        "id": str(event_id),
        "tenant_id": str(tenant_id),
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "payload": payload,
        "occurred_at": occurred_at.isoformat(),
    }


def host_compute_hash(hash_prev: Optional[bytes], payload: dict[str, Any]) -> bytes:
    return hashlib.sha256((hash_prev or b"") + host_canonical_bytes(payload)).digest()


# ── the host's encoding, as a host would register it ────────────────────────

#: Only the key differs. The timestamp keeps the package's formatter, which is
#: byte-identical to ``isoformat()`` for the aware UTC values the host always
#: wrote (``datetime.now(UTC)``), and unlike ``isoformat()`` survives a driver
#: that hands the moment back naive (SQLite) or in the server's zone (psycopg
#: under a non-UTC ``TimeZone``). See ``test_the_timestamp_formatter_*``.
LEGACY = ChainEncoding("host-legacy/1", keys={"org_id": "tenant_id"})
chain.register_encoding(LEGACY)

TENANT = uuid.UUID("7d1f0c52-3c55-4a4e-9a0e-2f1b3b1c9a10")


@dataclass
class Row:
    """A stored row under this package's column names (``org_id`` and
    ``occurred_at`` for the host's ``tenant_id`` and ``created_at``)."""

    id: Any
    seq: int
    actor: str
    action: str
    resource_type: str
    resource_id: Any
    payload: dict
    occurred_at: datetime
    hash_prev: Optional[bytes]
    hash_current: bytes
    encoding: str = CURRENT_ENCODING


_PAYLOADS = [
    {"status": "submitted", "note": "café مرحبا"},
    {"amount": 1200, "ratio": 0.25, "flags": [True, False, None]},
    {"nested": {"b": 2, "a": [1, {"z": "last", "y": "first"}]}},
    {},
    {"entity_id": str(uuid.uuid4()), "count": 0},
]


def host_rows(n: int, *, start: Optional[datetime] = None) -> list[Row]:
    """``n`` rows written exactly as the host's ``append_event`` wrote them."""
    when = start or datetime(2026, 6, 13, 9, 30, 15, 123456, tzinfo=timezone.utc)
    rows: list[Row] = []
    prev: Optional[bytes] = None
    for i in range(n):
        event_id, resource_id = uuid.uuid4(), uuid.uuid4()
        # One value with no microseconds, which isoformat() spells without the
        # fraction: a formatter that always printed six digits would miss it.
        occurred_at = when.replace(microsecond=0) if i == 1 else when + timedelta(seconds=i)
        payload = _PAYLOADS[i % len(_PAYLOADS)]
        digest = host_compute_hash(prev, host_chain_payload(
            event_id=event_id, tenant_id=TENANT, actor="entra|alice",
            action=f"document.step{i}", resource_type="document",
            resource_id=resource_id, payload=payload, occurred_at=occurred_at,
        ))
        rows.append(Row(
            id=event_id, seq=i + 1, actor="entra|alice", action=f"document.step{i}",
            resource_type="document", resource_id=resource_id, payload=payload,
            occurred_at=occurred_at, hash_prev=prev, hash_current=digest,
            encoding=LEGACY.name,
        ))
        prev = digest
    return rows


def package_rows(after: list[Row], n: int) -> list[Row]:
    """``n`` rows appended by this package after ``after``, current encoding."""
    rows: list[Row] = []
    prev = after[-1].hash_current if after else None
    base_seq = after[-1].seq if after else 0
    for i in range(n):
        row = Row(
            id=str(uuid.uuid4()), seq=base_seq + i + 1, actor="svc:asas",
            action=f"document.after{i}", resource_type="document",
            resource_id=str(uuid.uuid4()), payload={"i": i},
            occurred_at=datetime(2026, 9, 28, 10, i, tzinfo=timezone.utc),
            hash_prev=prev, hash_current=b"",
        )
        row.hash_current = chain.compute_hash(prev, chain.chain_payload(
            event_id=row.id, org_id=TENANT, actor=row.actor, action=row.action,
            resource_type=row.resource_type, resource_id=row.resource_id,
            payload=row.payload, occurred_at=row.occurred_at,
        ))
        prev = row.hash_current
        rows.append(row)
    return rows


# ── the bytes ───────────────────────────────────────────────────────────────


def test_the_legacy_encoding_reproduces_the_host_bytes_exactly():
    """The claim everything else rests on, asserted on the bytes rather than
    only on a passing verify: same dict, same JSON, same digest."""
    for row in host_rows(6):
        ours = chain.chain_payload(
            event_id=row.id, org_id=TENANT, actor=row.actor, action=row.action,
            resource_type=row.resource_type, resource_id=row.resource_id,
            payload=row.payload, occurred_at=row.occurred_at, encoding=LEGACY.name,
        )
        theirs = host_chain_payload(
            event_id=row.id, tenant_id=TENANT, actor=row.actor, action=row.action,
            resource_type=row.resource_type, resource_id=row.resource_id,
            payload=row.payload, occurred_at=row.occurred_at,
        )
        assert chain.canonical_bytes(ours) == host_canonical_bytes(theirs)
        assert chain.compute_hash(row.hash_prev, ours) == row.hash_current


def test_the_current_encoding_is_the_0_1_0_bytes():
    """Naming the encoding must not have changed it: a chain written by 0.1.0,
    which had no name for its encoding, is ``CURRENT_ENCODING`` byte for byte."""
    when = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    got = chain.canonical_bytes(chain.chain_payload(
        event_id="i", org_id="o", actor="a", action="b", resource_type="t",
        resource_id="r", payload={"k": 1}, occurred_at=when,
    ))
    assert got == (
        b'{"action":"b","actor":"a","id":"i","occurred_at":"2026-09-10T12:00:00+00:00",'
        b'"org_id":"o","payload":{"k":1},"resource_id":"r","resource_type":"t"}'
    )


# ── verifying a host chain ──────────────────────────────────────────────────


def test_a_host_chain_verifies_under_its_registered_encoding():
    report = verify_rows(str(TENANT), host_rows(6))
    assert report.is_intact, report.breaks
    assert report.events_checked == 6


def test_the_same_chain_fails_under_the_current_encoding():
    """Why this feature exists: without the label, every one of the host's rows
    reports as edited, over data nobody touched."""
    rows = host_rows(4)
    for r in rows:
        r.encoding = CURRENT_ENCODING
    report = verify_rows(str(TENANT), rows)
    assert len(report.breaks) == 4
    assert all("edited" in b.detail for b in report.breaks)


def test_the_handover_legacy_rows_then_package_rows_is_one_chain():
    """The adoption itself: the first row this package writes links to the last
    fingerprint the host wrote, and the whole history verifies as one."""
    legacy = host_rows(5)
    new = package_rows(legacy, 3)
    assert new[0].hash_prev == legacy[-1].hash_current
    report = verify_rows(str(TENANT), legacy + new)
    assert report.is_intact, report.breaks
    assert report.events_checked == 8


def test_an_edited_legacy_row_is_still_detected():
    rows = host_rows(5)
    rows[2].payload = {"status": "approved"}
    report = verify_rows(str(TENANT), rows)
    assert len(report.breaks) == 1
    assert report.first_break.seq == 3
    assert "edited" in report.first_break.detail
    assert report.first_break.encoding == LEGACY.name


def test_a_deleted_legacy_row_is_still_detected():
    rows = host_rows(5)
    del rows[1]
    report = verify_rows(str(TENANT), rows)
    assert report.first_break.seq == 3
    assert "deleted" in report.first_break.detail


def test_a_break_at_the_seam_is_detected():
    """Deleting the last legacy row leaves the first package row linking to a
    fingerprint that is no longer there, so the handover point is protected like
    any other link."""
    legacy = host_rows(4)
    new = package_rows(legacy, 2)
    report = verify_rows(str(TENANT), legacy[:-1] + new)
    assert report.first_break.seq == new[0].seq
    assert report.first_break.encoding == CURRENT_ENCODING
    assert "deleted" in report.first_break.detail


def test_relabelling_a_row_does_not_make_it_verify():
    """The label is not hashed, so it is worth saying what changing it buys: a
    package row relabelled as legacy stops verifying, it does not start hiding."""
    legacy = host_rows(2)
    new = package_rows(legacy, 2)
    new[0].encoding = LEGACY.name
    report = verify_rows(str(TENANT), legacy + new)
    assert report.first_break.seq == new[0].seq


# ── failing loudly ──────────────────────────────────────────────────────────


def test_an_unknown_encoding_fails_loudly_rather_than_as_a_break():
    """A name nobody registered is a configuration fault, not tampering. Reporting
    it as breaks would make a whole adopted history look rewritten."""
    rows = host_rows(3)
    rows[1].encoding = "never-registered"
    with pytest.raises(UnknownEncodingError, match="never-registered") as info:
        verify_rows(str(TENANT), rows)
    assert "seq=2" in str(info.value)
    assert "register_encoding" in str(info.value)


def test_chain_payload_refuses_an_unknown_encoding():
    with pytest.raises(UnknownEncodingError):
        chain.chain_payload(
            event_id="i", org_id="o", actor="a", action="b", resource_type="t",
            resource_id="r", payload={}, occurred_at=datetime.now(timezone.utc),
            encoding="nope",
        )


def test_a_name_means_one_definition():
    """Rows store the name, so a second definition under it would let old rows
    verify under whichever registered last."""
    chain.register_encoding(ChainEncoding("host-legacy/1", keys={"org_id": "tenant_id"}))
    with pytest.raises(ValueError, match="already registered"):
        chain.register_encoding(ChainEncoding("host-legacy/1", keys={"org_id": "tid"}))
    with pytest.raises(ValueError, match="already registered"):
        chain.register_encoding(ChainEncoding(CURRENT_ENCODING, keys={"org_id": "x"}))


@pytest.mark.parametrize("bad", [
    {"keys": {"tenant": "tenant_id"}},          # not a chain field
    {"keys": {"org_id": "actor"}},              # collides with another key
    {"name": ""},
    {"name": "x" * 33},                         # wider than the column
])
def test_a_malformed_encoding_is_refused_at_definition(bad):
    kwargs = {"name": "ok", **bad}
    with pytest.raises(ValueError):
        ChainEncoding(**kwargs)


# ── the timestamp ───────────────────────────────────────────────────────────


def test_the_timestamp_formatter_matches_isoformat_for_utc_values():
    """The host spelled the moment with ``isoformat()``. For an aware UTC value
    that is the same string ``canonical_timestamp`` produces, with and without
    microseconds, which is why the host's encoding only renames a key."""
    for v in (
        datetime(2026, 6, 13, 9, 30, 15, 123456, tzinfo=timezone.utc),
        datetime(2026, 6, 13, 9, 30, 15, tzinfo=timezone.utc),
        datetime.now(timezone.utc),
    ):
        assert chain.canonical_timestamp(v) == v.isoformat()


def test_the_timestamp_formatter_survives_the_driver_but_isoformat_does_not():
    """The same stored moment comes back naive from SQLite and in the server's
    zone from psycopg (a Postgres with ``TimeZone=Asia/Dubai`` returns +04:00).
    A bare ``isoformat()`` formatter would then hash a different string on
    verify than the host hashed on write, so it is the wrong thing to register
    even though it is what the host's code literally called."""
    written = datetime(2026, 6, 13, 9, 30, 15, 5, tzinfo=timezone.utc)
    read_back = [
        written.replace(tzinfo=None),
        written.astimezone(timezone(timedelta(hours=4))),
    ]
    for v in read_back:
        assert chain.canonical_timestamp(v) == written.isoformat()
        assert v.isoformat() != written.isoformat()

    rows = host_rows(3)
    rows[0].occurred_at = rows[0].occurred_at.replace(tzinfo=None)
    rows[1].occurred_at = rows[1].occurred_at.astimezone(timezone(timedelta(hours=4)))
    assert verify_rows(str(TENANT), rows).is_intact


def _dubai(value: datetime) -> str:
    return value.astimezone(timezone(timedelta(hours=4))).isoformat()


def test_a_host_that_wrote_another_offset_supplies_its_own_formatter():
    """The formatter is the one place a host's timestamp spelling can differ. A
    writer that hashed local +04:00 values registers a formatter that spells
    them that way, whatever zone the driver returns."""
    enc = chain.register_encoding(ChainEncoding(
        "host-dubai/1", keys={"org_id": "tenant_id"}, timestamp=_dubai,
    ))
    local = datetime(2026, 6, 13, 13, 30, tzinfo=timezone(timedelta(hours=4)))
    digest = host_compute_hash(None, host_chain_payload(
        event_id="e", tenant_id=TENANT, actor="a", action="b", resource_type="t",
        resource_id="r", payload={}, occurred_at=local,
    ))
    row = Row(id="e", seq=1, actor="a", action="b", resource_type="t",
              resource_id="r", payload={}, occurred_at=local.astimezone(timezone.utc),
              hash_prev=None, hash_current=digest, encoding=enc.name)
    assert verify_rows(str(TENANT), [row]).is_intact


# ── through the database ────────────────────────────────────────────────────


_INSERT = text(
    "INSERT INTO audit_event (id, org_id, actor, action, resource_type,"
    " resource_id, payload, occurred_at, hash_prev, hash_current{enc_col})"
    " VALUES (:id, :org, :actor, :action, :rt, :rid, :payload, :ts, :prev,"
    " :cur{enc_val})"
)


def _insert_host_rows(engine, rows: list[Row], *, with_encoding: bool) -> None:
    """Write the host's rows directly, the way its own writer did: this package's
    ``append`` would compute current-encoding hashes, which is the point."""
    sql = text(_INSERT.text.format(
        enc_col=", encoding" if with_encoding else "",
        enc_val=", :enc" if with_encoding else "",
    )).bindparams(
        bindparam("payload", type_=JSON()),
        bindparam("ts", type_=DateTime(timezone=True)),
        bindparam("prev", type_=LargeBinary()),
        bindparam("cur", type_=LargeBinary()),
    )
    with Session(engine) as s:
        asas_tenancy.set_tenant_guc(s, str(TENANT))
        for r in rows:
            params = {
                "id": str(r.id), "org": str(TENANT), "actor": r.actor,
                "action": r.action, "rt": r.resource_type, "rid": str(r.resource_id),
                "payload": r.payload, "ts": r.occurred_at,
                "prev": r.hash_prev, "cur": r.hash_current,
            }
            if with_encoding:
                params["enc"] = r.encoding
            s.execute(sql, params)
        s.commit()


def _append_and_verify(engine, n: int):
    with Session(engine, expire_on_commit=False) as s:
        asas_tenancy.set_tenant_guc(s, str(TENANT))
        appended = [
            asas_audit.append(
                s, org_id=str(TENANT), actor="svc:asas", action=f"after.{i}",
                resource_type="document", resource_id=str(i),
            )
            for i in range(n)
        ]
        s.commit()
        asas_tenancy.set_tenant_guc(s, str(TENANT))
        return appended, asas_audit.verify(s, str(TENANT))


def test_the_migration_labels_existing_rows_with_the_current_encoding(engine):
    """Rows written before the column existed were written by 0.1.0, whose bytes
    are ``CURRENT_ENCODING``. The column default labels them in the DDL, which
    neither the append-only trigger nor row-level security can interfere with."""
    from alembic import command

    from asas_audit.migrate import _config

    command.upgrade(_config(engine), "0001")
    rows = package_rows([], 3)
    _insert_host_rows(engine, rows, with_encoding=False)

    asas_audit.migrate(engine)
    with Session(engine) as s:
        asas_tenancy.set_tenant_guc(s, str(TENANT))
        labels = s.execute(text("SELECT encoding FROM audit_event")).scalars().all()
        assert labels == [CURRENT_ENCODING] * 3
    _, report = _append_and_verify(engine, 2)
    assert report.is_intact, report.breaks
    assert report.events_checked == 5


def test_an_adopted_chain_verifies_through_the_database(migrated):
    """Legacy rows stored, then this package's ``append`` continuing the same
    chain: the first appended row links to the host's last fingerprint and the
    round trip through the driver (naive on SQLite, server zone on Postgres)
    does not disturb either encoding."""
    legacy = host_rows(4)
    _insert_host_rows(migrated, legacy, with_encoding=True)
    appended, report = _append_and_verify(migrated, 3)
    assert bytes(appended[0].hash_prev) == legacy[-1].hash_current
    assert report.is_intact, report.breaks
    assert report.events_checked == 7


def test_migrate_adopts_a_table_whose_host_already_added_the_column(engine):
    """The adoption path end to end, portable part: a host table already renamed
    to this package's columns, with ``encoding`` added by the host's own
    migration defaulting to its legacy name. ``migrate`` adopts it (the guard
    passes, the baseline is stamped), leaves the host's column and its labels
    alone, and appends continue the chain."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE audit_event ("
            " seq BIGSERIAL PRIMARY KEY, id VARCHAR(64) NOT NULL,"
            " org_id VARCHAR(64) NOT NULL, actor VARCHAR(200) NOT NULL,"
            " action VARCHAR(128) NOT NULL, resource_type VARCHAR(64) NOT NULL,"
            " resource_id VARCHAR(64) NOT NULL, payload JSON NOT NULL,"
            " occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,"
            " hash_prev BYTEA, hash_current BYTEA NOT NULL,"
            f" encoding VARCHAR(32) NOT NULL DEFAULT '{LEGACY.name}')"
            if engine.dialect.name == "postgresql" else
            "CREATE TABLE audit_event ("
            " seq INTEGER PRIMARY KEY, id VARCHAR(64) NOT NULL,"
            " org_id VARCHAR(64) NOT NULL, actor VARCHAR(200) NOT NULL,"
            " action VARCHAR(128) NOT NULL, resource_type VARCHAR(64) NOT NULL,"
            " resource_id VARCHAR(64) NOT NULL, payload JSON NOT NULL,"
            " occurred_at DATETIME NOT NULL,"
            " hash_prev BLOB, hash_current BLOB NOT NULL,"
            f" encoding VARCHAR(32) NOT NULL DEFAULT '{LEGACY.name}')"
        ))
    legacy = host_rows(3)
    # No encoding in the INSERT: the host's own writer never knew the column,
    # so its rows are labelled by the default, exactly as in production.
    _insert_host_rows(engine, legacy, with_encoding=False)

    asas_audit.migrate(engine)
    with Session(engine) as s:
        labels = s.execute(text("SELECT encoding FROM audit_event")).scalars().all()
    assert labels == [LEGACY.name] * 3
    appended, report = _append_and_verify(engine, 2)
    assert report.is_intact, report.breaks
    assert appended[0].encoding == CURRENT_ENCODING


# ── the documented recipe, against the host's real table shape ──────────────

#: The host's own DDL, verbatim in the parts that matter: UUID identity, a
#: ``tenant_id`` with a foreign key, ``created_at``, a sequence-backed ``seq``
#: that is not the primary key, JSONB, its own trigger, and a UUID-typed policy.
_HOST_SCHEMA = [
    "CREATE TABLE tenant (id uuid PRIMARY KEY)",
    "CREATE SEQUENCE audit_event_seq",
    """CREATE TABLE audit_event (
        id uuid PRIMARY KEY,
        tenant_id uuid NOT NULL REFERENCES tenant (id),
        seq bigint NOT NULL DEFAULT nextval('audit_event_seq'::regclass),
        actor varchar(200) NOT NULL,
        action varchar(128) NOT NULL,
        resource_type varchar(64) NOT NULL,
        resource_id uuid NOT NULL,
        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL,
        hash_prev bytea,
        hash_current bytea NOT NULL,
        CONSTRAINT uq_audit_event_seq UNIQUE (seq)
    )""",
    "CREATE INDEX ix_audit_event_tenant_id ON audit_event (tenant_id)",
    "CREATE INDEX ix_audit_event_tenant_seq ON audit_event (tenant_id, seq DESC)",
    """CREATE FUNCTION audit_event_block_mutation() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_event is append-only; % is forbidden', TG_OP;
    END;
    $$ LANGUAGE plpgsql""",
    """CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event
       FOR EACH ROW EXECUTE FUNCTION audit_event_block_mutation()""",
    "ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_event FORCE ROW LEVEL SECURITY",
    """CREATE POLICY audit_event_tenant_isolation ON audit_event
       USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
       WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)""",
]

#: The README's "Adopting an existing chain" migration, as SQL. The host runs it
#: in its own chain, with its legacy writer stopped, before ``migrate()``.
_HOST_ADOPTION = [
    # The policy and the foreign key both pin the UUID type, so they go first.
    "DROP POLICY audit_event_tenant_isolation ON audit_event",
    "ALTER TABLE audit_event DROP CONSTRAINT audit_event_tenant_id_fkey",
    "ALTER TABLE audit_event RENAME COLUMN tenant_id TO org_id",
    "ALTER TABLE audit_event RENAME COLUMN created_at TO occurred_at",
    """ALTER TABLE audit_event
         ALTER COLUMN id TYPE varchar(64) USING id::text,
         ALTER COLUMN org_id TYPE varchar(64) USING org_id::text,
         ALTER COLUMN resource_id TYPE varchar(64) USING resource_id::text""",
    f"ALTER TABLE audit_event ADD COLUMN encoding varchar(32) NOT NULL"
    f" DEFAULT '{LEGACY.name}'",
]


def test_the_readme_recipe_adopts_the_hosts_real_table(engine):
    """End to end on Postgres, against the reference host's actual column types:
    rows the host wrote, the documented rename, ``migrate()``, this package's
    appends, and one intact history. JSONB is kept as it is, and the host's own
    trigger keeps refusing edits."""
    if engine.dialect.name != "postgresql":
        pytest.skip("the host's schema is Postgres DDL: uuid, jsonb, plpgsql")
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with engine.begin() as conn:
        for ddl in _HOST_SCHEMA:
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO tenant (id) VALUES (:t)"), {"t": TENANT})

    legacy = host_rows(5)
    insert = text(
        "INSERT INTO audit_event (id, tenant_id, actor, action, resource_type,"
        " resource_id, payload, created_at, hash_prev, hash_current)"
        " VALUES (:id, :t, :actor, :action, :rt, :rid, :payload, :ts, :prev, :cur)"
    ).bindparams(
        bindparam("payload", type_=JSON()),
        bindparam("ts", type_=DateTime(timezone=True)),
        bindparam("prev", type_=LargeBinary()),
        bindparam("cur", type_=LargeBinary()),
    )
    with Session(engine) as s:
        asas_tenancy.set_tenant_guc(s, str(TENANT))
        for r in legacy:
            s.execute(insert, {
                "id": r.id, "t": TENANT, "actor": r.actor, "action": r.action,
                "rt": r.resource_type, "rid": r.resource_id, "payload": r.payload,
                "ts": r.occurred_at, "prev": r.hash_prev, "cur": r.hash_current,
            })
        s.commit()

    with engine.begin() as conn:
        for ddl in _HOST_ADOPTION:
            conn.execute(text(ddl))
        # The policy back, through the one shared definition, on the new type.
        with Operations.context(MigrationContext.configure(conn)):
            asas_tenancy.enable_rls(
                "audit_event", column="org_id", column_type="character varying"
            )

    asas_audit.migrate(engine)

    appended, report = _append_and_verify(engine, 3)
    assert bytes(appended[0].hash_prev) == legacy[-1].hash_current
    assert report.is_intact, report.breaks
    assert report.events_checked == 8

    with Session(engine) as s:
        asas_tenancy.set_tenant_guc(s, str(TENANT))
        labels = s.execute(
            text("SELECT encoding FROM audit_event ORDER BY seq")
        ).scalars().all()
        assert labels == [LEGACY.name] * 5 + [CURRENT_ENCODING] * 3
        history = asas_audit.history(s, org_id=str(TENANT), limit=10)
        assert history[-1].payload == legacy[0].payload
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("UPDATE audit_event SET actor = 'mallory'"))
