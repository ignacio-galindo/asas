# Changelog — `asas-validation`

Versions follow semver, and the git tag matches this file: `asas-validation/v0.12.0`.
Pre-1.0, a breaking change bumps the **minor**.

Release procedure and the historical tag mapping: [`RELEASING.md`](../../RELEASING.md).

## 0.12.0 — 2026-09-11

Completes the package along the asas roadmap: the rule set is now enforced on both
sides from one catalog, and the engine no longer trusts the server's clock or the
value's type.

**Breaking (why this is a minor, not a patch):**

- `assert_rules_known()` now also rejects a catalog with the wrong number of fields
  for a kind, a missing required param, a non-integer `years`/`days`, an unparseable
  `date` param, or two rules sharing a `code`. A host whose catalog has any of these
  fails at boot instead of on the first matching request (a duplicate `code`
  previously went unnoticed; the browser client keys errors by it).
- `GET /validation/rules` items gain an `entity` key (additive on the wire; noted
  because the ETag of every representation changes once, so clients revalidate once).

**Added:**

- **Browser client** `client/asas-validation.js` (+ `.d.ts`): `evaluate`,
  `createValidator`, `fetchRules` (ETag-aware), `fieldErrors`, `fromServer422`.
  Zero dependencies, ESM, `node --test`. Both engines run `conformance/cases.json`;
  a CI job runs the node suite.
- **Kinds** `min_age`, `max_future`, `not_before`, `not_after`, `max_span`,
  `min_span`, and `order` with `params={"strict": True}`. Year arithmetic clamps
  Feb 29; `years` and `days` combine, years first.
- **`configure(timezone=…, today=…)`** host hook — "today" in the users' calendar,
  not the server's. An aware `datetime` value is read in that zone. `evaluate` and
  `raise_if_invalid` take `today=` to override per call.
- **`register_kind(name, check, arity=, params=)`** for host-specific, server-only
  kinds; `known_kinds()` / `builtin_kinds()`.
- Message templating: `{years}`, `{days}`, `{date}`, `{strict}` placeholders,
  rendered identically on both sides. The 422 item carries the params as `ctx`
  (Pydantic v2's slot), only when there are any.
- `Violation.params`, `to_detail(violations)`, `serialize(rules)`, `as_date`,
  `shift_years`, `render_message`, `today` exported. A `Mapping` record is read by key.

**Fixed:**

- A `datetime` (or ISO string) in `changes` raised `TypeError` against `date.today()`
  → 500. Values are now reduced to calendar dates; a value that cannot be a date
  raises `ValueError` naming the field.

## 0.11.0 — 2026-08-25

- Licensed under **Apache 2.0** (was proprietary/all-rights-reserved). `LICENSE` and `NOTICE` ship inside the wheel and the metadata carries `License-Expression: Apache-2.0` (Teamy TEAMY-797).
- Added `tests/test_host_contract.py`: `__all__` declared and resolving, contract names callable rather than shadowed by a submodule, module exports declared deliberately (Teamy TEAMY-798).

## Before 2026-08-25

Earlier releases were cut as **repo-wide** tags (`v0.1.0` … `v0.15.0`) under the
lockstep scheme in DR 0017, which decayed: from `v0.11.0` onward the repo tag no
longer matched any package's own version, so `asas-validation @ v0.15.0` did not install
`asas-validation` 0.15.0. `RELEASING.md` carries the full tag-to-version table for
decoding an old pin. Individual changes are in the git history.
