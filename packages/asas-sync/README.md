# asas-sync

Keep a local copy of a remote paginated collection (an ERP's requisitions, an
HRIS's workers, a CRM's accounts) up to date incrementally, so you can list,
search and join it without calling the remote every time, and trust that the
copy is complete.

```python
import asas_sync

class Workers:  # your adapter over the remote API
    async def fetch(self, *, since, after_key, offset, limit, purpose):
        body = await http.get("/workers", params=...)  # stamp >= since, key > after_key, ordered by key
        return asas_sync.RemotePage(rows=body["items"], has_more=body["hasMore"])

    async def exists(self, key):
        response = await http.get(f"/workers/{key}")
        return {200: True, 404: False}.get(response.status_code)  # None: cannot say

spec = asas_sync.SyncSpec(
    resource="workers", collection=Workers(),
    key=lambda r: r["PersonId"], stamp=lambda r: parse(r["LastUpdateDate"]),
    upsert=upsert_workers, on_deleted=mark_workers_deleted,
    key_ordered=True, page_size=500, offset_ceiling=10_000,
)

asas_sync.migrate(engine)                                   # boot
await asas_sync.run_pass(session_factory, spec, org_id=org)  # on a schedule or an event
await asas_sync.reconcile(session_factory, spec, org_id=org) # weekly
```

## What it gets right, so you do not have to

- **Unstable tie order loses rows under offset paging.** Many APIs return
  rows that share a timestamp in a different order on each request, so paging
  by offset across such a block skips some and repeats others. A key-ordered
  walk re-anchors on the last key with offset 0; a stamp-ordered one re-anchors
  on the page's greatest stamp with offset 0 and uses an offset only inside a
  block longer than a page.
- **"No more rows" is a lie at the offset ceiling.** Some APIs stop at a fixed
  offset and say `has_more=False` there with rows left. `offset_ceiling` turns
  that into a re-anchor.
- **The watermark never passes the walk's own start** (less `clock_skew_s`)
  nor the greatest stamp actually read, so a row modified while the walk ran is
  read by the next pass instead of being lost for good.
- **Deletion takes two misses.** A key is deleted only when two consecutive
  complete reconcile walks missed it. The first reconcile only lays a baseline,
  and an incomplete walk deletes nothing. Where the walk is not exact
  (stamp-ordered), a twice-missed key is read by key, and only an explicit
  "gone" deletes it.
- **Capped passes resume.** `max_pages` bounds one pass; the next continues
  after the last committed key instead of starting over.
- **One pass per collection at a time,** by a compare-and-set lease on the
  cursor row that is renewed per page and expires if the holder dies. Portable
  to SQLite and Postgres; no advisory lock.
- **Each page commits on its own,** so a failure stops the walk with the cursor
  at the last committed page, and the error is recorded on the cursor.

## Host contract

Table-owning, router-less, no seed.

- `migrate(engine)`: package Alembic chain (`alembic_version_asas_sync`).
- `run_pass(session_factory, spec, org_id=..., full=False)` and
  `reconcile(session_factory, spec, org_id=...)`: async. The remote call is
  async; the database work runs in short sync transactions off the event loop.
- `cursor_status(session, spec, org_id=...)`: the cursor, for an admin card.

Your `upsert` must be idempotent: rows at a re-anchor are read twice, and a
pass that failed after a commit repeats a page.

Extracted from the ad-recruiter platform's Oracle Fusion thin index (D303, D309).
