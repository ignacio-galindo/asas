# Changelog: `asas-oracle`

Versions follow semver, and the git tag matches this file: `asas-oracle/v0.1.0`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure and the historical tag mapping: [`RELEASING.md`](../../RELEASING.md).

## 0.3.0 (unreleased)

What a host that WRITES to Fusion had to grow beside the client, none of it
about any product. Measured on a Fusion recruiting instance in October 2026.

- **The OIC invocation id is surfaced for logging.** Oracle Integration
  Cloud stamps `X-Oracle-Ics-Instance-Id` on every call it routes (a gateway
  address; a direct-to-Fusion call has none). A host had no view of the
  response and so no way to log it, which is what ties one of our calls to the
  exact OIC run when AD Connect has to trace a failure. It now rides on
  `RequestEvent.instance_id` for a call that answered, and on
  `OracleUpstreamError.instance_id` for one that failed; the header name is
  exported as `ICS_INSTANCE_ID_HEADER`. Absent or empty reads as `None`.
- **Fusion's real duplicate is recognised.** Fusion Recruiting refuses a taken
  number with "You need to enter a different number. There's already
  requisition number <n>. (IRC-1590053)", which matched none of the generic
  words, so a retried create whose first attempt had landed raised the plain
  upstream error instead of `OracleAlreadyExistsError`, the one answer the
  read-back-and-recover path rests on. The number and the two phrases are
  now matched.
- **Refusals through an API gateway (AD Connect) are read as the gateway's.**
  On a gateway address (`/gateway/` in the path), a WRITE refused with a 405,
  a 415, or a 404 that is not Fusion's own JSON error document now raises
  `OracleGatewayError`; before, the 404 raised `OracleNotFoundError`, which an
  outbox reads as "this record is gone" and fails the row, losing a write the
  gateway simply was not registered to carry yet. A READ is the gateway's only
  on a 405 or in the gateway's own words: a bare 404 on a read stays "no such
  record", so one missing person does not look like an unregistered
  operation. Fusion's own words (the lock, the lifecycle, the duplicate)
  still win over the gateway's on every route. Direct connections are
  unchanged.
- **`OracleWrongAddressError`**: an HTML 404 page, which comes from a server in
  front of Fusion's REST tier, means the base URL or version prefix is wrong,
  not that a record is missing.
- **One more lifecycle refusal**, IRC-1590385 ("The job application can't be
  moved to the provided phase and state"), is an `OracleStateRefusedError`.
- **`message_codes`**, and `OracleUpstreamError.message_codes`: Fusion's
  message numbers (`IRC-1590142`, `FND-2808`) read off a refusal, in order,
  each once. The number is the stable half of a Fusion error, and safe to log.
- **`OracleLifecycleDesyncError`**, a subclass of `OracleStateRefusedError`, for
  "Context current state differs from lifecycle instance": the record and its
  lifecycle disagree, and every status write is refused until a person walks
  the record back in Fusion. Final like its parent, named apart because the
  fix is a person's.
- **`missing_fields`**: the field labels a "you need to enter a value for the
  ... field" refusal names, beside `refused_fields` for "a valid value".
- **`identify` and `FUSION_REFUSALS`**: which refusal it was, as a stable key
  (`value_required`, `state_not_adjacent`, `position_full`, ...), matched by
  message number first and by Fusion's words second, from a catalogue of 16
  measured refusals a host can extend with its own entries.
- **`asas_oracle.outbox`**: what a write outbox does with each failure.
  `classify(exc)` answers `transient`, `blocked` or `permanent` (and whether
  it is an outage that should stop the sweep) from the exception's type and
  status (a gateway refusal and the wrong address are `blocked`: held, never
  failed); `backoff_s`, `budget_s`, and a fixed per-row `jitter_fraction` a
  database can compute too (`jitter_sql`).
- **`WriteAllowlist`**: a fail-closed allow-list for writes about people on a
  shared test pod, where a write can make Fusion notify a real person. Both
  the address and the record must match; an empty list allows nothing.

## 0.2.0 (unreleased)

Reads and helpers a host had grown beside the client, none of them about any
product:

- **`common_lookup`, `installed_languages` and `fscm_path`.** Some of an
  instance's reference lists live under `fscmRestApi`, not `hcmRestApi`, on the
  same host with the same credentials; the `hcmRestApi` path 404s, which reads
  as "no such list". `fscm_path` makes the hop (`None` behind a gateway, where
  no such route exists, so the reads answer `[]` rather than guess).
  `common_lookup` reads one `commonLookupsLOV` lookup, paged. `installed_languages`
  reads `languagesLOV` and keeps the base and installed, active languages: on
  one measured instance two of 37, against the 39 of `PER_CORRESP_LANG`, which
  lists every language Fusion can correspond in and not the ones a record can
  be published in.
- **`stored_of` and `differences`.** What Oracle's answer to a create or a PATCH
  holds against what was sent: a field can be defaulted or overridden by the
  instance's own configuration with a 200 all the same. A missing or empty
  echo is unknown, never a difference.
- **`refused_fields`.** The field LABELS a Fusion validation refusal names
  ("valid value for the Candidate Selection Process field"), labels only, so a
  host can act on it without the prose, which never leaves the exception.
- **`person_id_for_address`.** The `PersonId` behind an address through its user
  account, by the same spellings as `find_account` (suffixed first on a
  scrubbing pod), asked at once.
- **`worker_address(row, suffix="")`** takes the scrubbing pod's suffix off a
  `Username` fallback, so the address matches an account on the host's side.

## 0.1.1 (unreleased)

- **Fusion's lifecycle refusals are their own classes.** `OracleStateRefusedError`
  for a phase or state move the record's lifecycle will not allow, and
  `OraclePhaseEntryRequiredError` (a subclass) for the one such refusal a second
  attempt can pass, by entering the target phase at a non-terminal state first.
  Both are 400s with prose, so they are matched on Oracle's words like the
  duplicate and the lock already were, and both are deliberately NOT transient:
  the move is a verdict on the record, so a caller with an outbox must fail the
  row rather than retry it, which is the opposite of what `OracleLockedError`
  asks for. Measured across 169 phase and state moves on a Fusion recruiting
  instance. The subclassing is the safe default: a caller that knows only the
  parent treats the recoverable one as final.

## 0.1.0 (unreleased)

First release. Extracted from a product's working Oracle Fusion integration and
generalised to be **domain-agnostic**: it knows Fusion (the query grammar, the
media types, the paging limits, attachments, the HCM reference data) and
nothing about recruiting or any other product. What a resource means, which
resources a product reads and which it caches stay with the product.

- **`OracleSettings`** replaces the product's settings import: an explicit
  object validated at construction, where an empty base URL means "Oracle is
  off" rather than an error, plus the optional gateway API key header.
- **`OracleFusionClient`** is a plain object the host owns (or hands its own
  `httpx.AsyncClient`), with `get`, `get_collection` (a `CollectionPage`, `-1`
  totals read as `None`), `iter_collection`, `get_bytes`, `post` and `patch`,
  each with the media type Oracle insists on. Errors are a typed hierarchy with
  no HTTP-status baggage and never carry Oracle's body.
- **The read cache is a seam.** A four-method `Cache` protocol with
  `MemoryCache` (default) and `NullCache`, and a `CachePolicy` saying which
  resources are cached, for how long, and which writes make them stale. The
  default caches the HCM reference data alone (`reference_ttls()`); a product
  adds its own resources.
- **`OracleLookups`** holds what were module-level caches as instance state:
  id-to-name lookups for eight kinds, people by person id, a worker and their
  department by email address, and a reference set's departments.
- The `q` grammar helpers and row readers, with the instance's traps (no OR,
  `;` for AND, no quote escaping, case-sensitive equality) documented.
- Fusion's paging limits and attachments on any resource: `capped_page` (the
  page cap and the offset ceiling, `at_ceiling` where `hasMore=false` lies),
  `attachments` (with the `links` the enclosure key lives in),
  `enclosure_key`, `download_enclosure`.
- **Upstream health.** A per-client `Breaker` makes reads fail at once with
  `OracleUnavailableError` after consecutive faults (transport, 5xx, 429),
  then lets one probe decide; writes are counted, never refused.
  `client.health.snapshot()` carries the breaker and per-resource call
  statistics; `count_calls()` counts the Oracle requests one host request made.
- **Gateway-only authentication.** Basic auth is sent only when a username is
  set, so a gateway that authenticates to the integration layer itself takes
  the API key alone; a `base_url` needs credentials, a key, or both.
- **A bounded, warm connection pool** (`max_connections`) and retries for a
  connection that could not be opened (`connect_retries`), on the client the
  library owns.
- **`use_cache=False`** on `get`, `get_collection` and `iter_collection`.
- **`LookupStore`**, an optional persistent home for lookup answers shared by
  every process: stale answers served and refreshed in the background,
  negative answers kept, failures never stored, refreshes that skip the read
  cache. `MemoryLookupStore` is the in-process implementation.
- **`OracleLookups.positions()`**: a position's name and budget flag in one
  request.
- **`asas-oracle-check`** (`check(client, probes)`): each read in a manifest
  called once, to verify a gateway registration; values chain between probes,
  writes are listed and never called, no body is printed. The default manifest
  is the HCM reference reads; `examples/recruiting-probes.json` is a recruiting
  one.
- **Credentials are a seam** (`Auth`): `BasicAuth`, `ApiKeyAuth`,
  `BearerToken`, `OAuthClientCredentials` (a client-credentials token, cached,
  refreshed before expiry and renewed after a 401 with one retry) and
  `CompositeAuth`, built by `OracleSettings.auth()` from the settings
  (`oauth_*` fields), or passed as `auth=`. Basic and OAuth are refused
  together; no credential ever reaches a log or an exception.
- **Breakers per resource plus one for the host** (`BreakerPolicy`), counting
  only real outages (`is_outage`: no response, 502/503/504, 429). A 500 is an
  answer, so one broken operation cannot switch every read off.
- **`OracleAuthError`** for 401/403 (never transient, never an outage) and
  **`retry_after_seconds`** on a 429/503.
- **`OracleQueryError`**: `eq`, `like` and `literal` refuse a quote or a `;`
  instead of guessing (an unquoted value must be a plain id; field names must
  be attribute names), so caller input cannot widen a query.
  `strip_quotes=True` is the explicit opt-in for free text.
- **`client.request(...)`** is public, with `raise_for_status` and `guard`;
  the gateway check uses it instead of the client's internals.
- **`OracleLookups(kinds=...)`** adds a host's own id kinds; **`extra_headers`**
  ride on every request; an **`on_request`** hook sees every request
  (`RequestEvent`).
- Depends on `httpx` only.
