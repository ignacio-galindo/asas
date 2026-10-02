# asas-validation

Declarative temporal and cross-field validation, declared **once** and enforced
**twice**: the host writes its rules as data in its own code, the server evaluates
them on every create/update and answers with FastAPI's native 422 envelope, and the
same catalog is served over `GET /validation/rules` to a bundled browser client that
evaluates it identically before submit. Both engines are held to one conformance
fixture, so "the form said yes and the server said no" cannot happen for a declared
rule.

Table-less variant of the Asas host contract: no session dependency, no `seed`, no
`migrate`. Contract rows: **Routers** (`build_router`) and **Host hooks**
(`configure`). See the repo README for the full contract.

Pydantic already covers *shape* (is this a date?). This package covers *meaning*
(may this date be in the future? after that one? more than 18 years ago?) — the
class of rule that otherwise ends up as an `if` in each router and a second `if` in
each form.

## Wiring

```python
import asas_validation as validation
from asas_validation import Rule

ENTITY = "member"

RULES = (
    Rule(ENTITY, "not_future", ("date_of_birth",),
         "Date of birth cannot be in the future.", "member.dob_future"),
    Rule(ENTITY, "min_age", ("date_of_birth",),
         "Members must be at least {years} years old.", "member.too_young",
         params={"years": 18}),
    Rule(ENTITY, "max_age", ("date_of_birth",),
         "Check the date of birth.", "member.too_old", params={"years": 100}),
    Rule(ENTITY, "order", ("available_from", "available_until"),
         "Availability must end after it starts.", "member.availability_order",
         params={"strict": True}),
    Rule(ENTITY, "max_span", ("available_from", "available_until"),
         "An availability window is at most {days} days.", "member.availability_span",
         params={"days": 90}),
    # cross-entity: the parent's value arrives in `context`
    Rule("appointment", "order", ("case.opened_on", "scheduled_on"),
         "An appointment cannot precede its case.", "appointment.before_case"),
)

# boot
validation.configure(timezone="Asia/Dubai")      # the calendar your users see
validation.register_fields(ENTITY, Member.model_fields)
validation.register_fields("appointment", Appointment.model_fields)
validation.register_fields("case", Case.model_fields)
validation.declare_rules(RULES)
validation.assert_rules_known()                  # malformed catalog → boot fails here
app.include_router(validation.build_router(), dependencies=[Depends(require_user)])

# routers
@router.post("")
def create(payload: MemberCreate, session=Depends(get_session)):
    row = Member(**payload.model_dump(exclude_none=True))
    validation.raise_if_invalid(ENTITY, None, row.model_dump())   # effective record, not payload
    ...

@router.patch("/{id}")
def update(id: int, payload: MemberUpdate, session=Depends(get_session)):
    row = _get_or_404(session, id)
    changes = payload.model_dump(exclude_unset=True)
    validation.raise_if_invalid(ENTITY, row, changes)
    ...

@router.post("/{id}/appointments")
def schedule(id: int, payload: AppointmentCreate, session=Depends(get_session)):
    parent = _get_or_404(session, id)
    validation.raise_if_invalid("appointment", None, payload.model_dump(),
                                context={"case.opened_on": parent.opened_on})
```

Two behaviours to know before writing a rule:

- A rule fires only when the edit **touches** one of its fields. An unrelated edit
  is never blocked by pre-existing data, and a bad historical row does not freeze
  the record.
- A rule is **skipped when any value it reads is absent** (`None` or `""`), so
  optional fields need no null-guards. On a *create* path this means validating the
  **effective** record (defaults filled in), not the raw payload — see the
  reference host's `tickets.py` for why.

`assert_rules_known()` fails the boot on: a field no entity registered (a typo, or a
renamed model field), an unknown kind, the wrong number of fields for the kind, a
missing required param, an unparseable `date` param, a non-integer `years`/`days`,
or two rules sharing a `code`. An entity that never registered fields is not
checked, so adoption can be progressive.

## Kinds

Every kind reads calendar **dates**. A `datetime` is reduced to its date (an aware
one in the configured zone), an ISO string is parsed; anything else raises, because
a value that cannot be a date is a wiring bug and must not become a silent pass.

| kind | fields | params | passes when |
| --- | --- | --- | --- |
| `not_future` | `(f)` | — | `f <= today` |
| `not_past` | `(f)` | — | `f >= today` |
| `max_age` | `(f)` | `years` | `f >= today - years` (not older than) |
| `min_age` | `(f)` | `years` | `f <= today - years` (at least this old) |
| `max_future` | `(f)` | `years` and/or `days` | `f <= today + years + days` |
| `not_before` | `(f)` | `date` (ISO) | `f >= date` |
| `not_after` | `(f)` | `date` (ISO) | `f <= date` |
| `order` | `(earlier, later)` | `strict` (optional) | `earlier <= later`, or `<` when strict |
| `max_span` | `(earlier, later)` | `years` and/or `days` | `later <= earlier + years + days` |
| `min_span` | `(earlier, later)` | `years` and/or `days` | `later >= earlier + years + days` |

Year arithmetic clamps a Feb-29 anchor to Feb-28 in a non-leap target year; when
both `years` and `days` are given, years apply first. Both engines share the exact
rule and the fixture pins it.

A message may use `{years}`, `{days}`, `{date}`, `{strict}` placeholders — plain
`{name}` only, no format specs — and both sides render it the same way. Unknown
placeholders are left as written.

**Host-specific kinds**: `register_kind(name, check, arity=…, params=…)` adds one,
where `check(values, today, rule) -> bool` receives the field values already reduced
to dates. Such a kind is **server-only**: the browser client skips rules of a kind it
does not know and lists them in `unsupportedKinds()`, and the server remains
authoritative. A built-in name cannot be overridden, or the two sides would disagree
silently.

## "Today" is the users' calendar

`date.today()` answers in the *server's* zone. A host running in UTC and used from
the Gulf sees the calendar roll four hours late: after 20:00 UTC a date the user
typed as "today" is refused as `not_future`. Name the zone once:

```python
asas_validation.configure(timezone="Asia/Dubai")          # IANA name or tzinfo
asas_validation.configure(today=lambda: some_date)         # a clock, for tests / replays
asas_validation.configure()                                # back to date.today()
```

Precedence: `today=` passed to `evaluate`/`raise_if_invalid` → configured `today`
callable → configured `timezone` → `date.today()`. The browser client uses the
user's local calendar, which is normally the same one.

## The 422 envelope

Violations become FastAPI's native shape, so semantic errors and Pydantic's
body-shape errors reach the frontend through **one** code path. `ctx` carries the
rule's params (as Pydantic v2 does for its constrained types) and is present only
when there are any, so rules without params produce the pre-0.12 envelope exactly.

```json
{"detail": [
  {"loc": ["body", "date_of_birth"], "msg": "Members must be at least 18 years old.",
   "type": "value_error.member.too_young", "ctx": {"years": 18}}
]}
```

`to_detail(violations)` gives the list without raising, for hosts that aggregate.

## The browser client

`client/asas-validation.js` (ESM, zero dependencies, hand-written `.d.ts`) evaluates
the served catalog with the same semantics. Vendor the file or install the folder as
a workspace package; there is no build step.

```ts
import { createValidator, fetchRules, fieldErrors, fromServer422 }
  from "@asas/validation-client";

const { rules, etag } = await fetchRules(`${API}/validation/rules`, { headers: auth });
const validator = createValidator(rules);

// before submit — same answers the server would give
const local = validator.validate("member", form.values, { record: current });
if (local.length) return setErrors(fieldErrors(local));

// after submit — the server's 422 (ours or Pydantic's) into the same shape
const res = await post(`${API}/members`, form.values);
if (res.status === 422) setErrors(fieldErrors(fromServer422(await res.json())));
```

Facts the client gets right that a quick port would not: an ISO `YYYY-MM-DD` string
is read literally (never through `new Date`, which shifts it by the UTC offset); a
`Date` is read by its local components; arithmetic runs on day numbers so a DST
transition cannot make two adjacent dates compare equal; Feb-29 clamps exactly as
the server does. `fetchRules` sends `If-None-Match` and returns the cached rules on
304, matching the endpoint's weak ETag.

Run its tests with `cd client && node --test` (Node ≥ 20). The Python suite and the
node suite both execute `conformance/cases.json`; **add a case there**, never on one
side only.

## Wire form of `/validation/rules`

A list, filterable by `?entity=`, ETag-cached (weak tag, per representation):

```json
[{"entity": "member", "kind": "min_age", "fields": ["date_of_birth"],
  "field": "date_of_birth", "code": "member.too_young",
  "message": "Members must be at least {years} years old.", "params": {"years": 18}}]
```

`message` is the raw template; the client renders it. `field` is the input a
violation attaches to: the rule's last own (non-namespaced) field. Changes to this
form are additive only — the client is vendored into hosts and upgrades on its own
schedule.

## Design notes

- Rules are **developer invariants, not admin data**: they live in host code, so a
  deployment cannot switch off "an appointment cannot precede its case".
- The engine is **model-free**: it reads a record by attribute (or by key for a
  mapping) and overlays the changes; it never imports host models.
- The four registries (rules, fields, kinds, clock) are module-level, like every
  Asas `configure_*` seam; test suites reset all four (see `tests/conftest.py`).
- Extracted from Teamy (epic TEAMY-466; DR 0007 for the rule model, DR 0017 for the
  extraction). The 0.12 completion follows the asas package roadmap: the server side
  was covered, the browser side was unused because nothing consumed the endpoint.
