/**
 * asas-validation — browser client.
 *
 * Evaluates the rule catalog served by `GET /validation/rules` (asas-validation
 * ≥ 0.12) with the same semantics as the Python engine, so a form can refuse what
 * the server would refuse before the round trip, from one rule set. The Python
 * side stays authoritative: this module is pre-submit feedback, never a substitute.
 *
 * Both engines are held to one fixture, `conformance/cases.json`; a semantic change
 * on either side that is not also a fixture change is a bug.
 *
 * Zero dependencies, ESM, no build step: vendor the file or install the folder.
 * Dates are handled as calendar days in the user's local zone (a `Date` is read by
 * its local components; `"YYYY-MM-DD"` is read literally, never through `new Date`,
 * which would shift it by the UTC offset). The server reads its dates in the zone the
 * host `configure(timezone=…)`d — normally the same calendar the user is looking at.
 */

const DAY_MS = 86400000;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

// ── calendar days ──────────────────────────────────────────────────────────────
// A day is an integer: days since 1970-01-01, computed through Date.UTC so DST
// transitions and the user's UTC offset can never make two dates one day apart
// compare as equal (or vice versa).

function dayOf(y, m, d) {
  const n = Date.UTC(y, m - 1, d);
  if (Number.isNaN(n)) throw new RangeError(`invalid calendar date ${y}-${m}-${d}`);
  // Date.UTC rolls an out-of-range day (Feb 30) forward; refuse rather than accept.
  const t = new Date(n);
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== m - 1 || t.getUTCDate() !== d) {
    throw new RangeError(`invalid calendar date ${y}-${m}-${d}`);
  }
  return Math.round(n / DAY_MS);
}

function partsOf(day) {
  const t = new Date(day * DAY_MS);
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() };
}

/**
 * Reduce a value to a calendar day. `null`, `undefined` and `""` are "absent"
 * (→ null, the rule is skipped). Anything that cannot be a date throws — the
 * same stance as the server: a wiring bug must not become a silent pass.
 * @param {unknown} value
 * @param {string} [field]
 * @returns {number|null}
 */
export function toDay(value, field = "?") {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) throw new RangeError(`validation field '${field}': invalid Date`);
    return dayOf(value.getFullYear(), value.getMonth() + 1, value.getDate());
  }
  if (typeof value === "string") {
    const m = ISO_DATE.exec(value);
    if (m) return dayOf(Number(m[1]), Number(m[2]), Number(m[3]));
    const t = new Date(value); // ISO datetime, with or without offset → local calendar
    if (!Number.isNaN(t.getTime()) && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
      return dayOf(t.getFullYear(), t.getMonth() + 1, t.getDate());
    }
    throw new RangeError(`validation field '${field}': '${value}' is not an ISO date or datetime`);
  }
  throw new RangeError(`validation field '${field}': cannot read a date from ${typeof value}`);
}

/** The `YYYY-MM-DD` form of a day number (for messages and tests). */
export function fromDay(day) {
  const { y, m, d } = partsOf(day);
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** Today as a calendar day, in the user's local zone. */
export function todayDay() {
  return toDay(new Date());
}

/** `day` moved `years` years (negative = back); a Feb-29 anchor clamps to Feb-28 in a
 * non-leap target year — exactly the Python `shift_years`. */
export function shiftYears(day, years) {
  const { y, m, d } = partsOf(day);
  const ty = y + years;
  if (m === 2 && d === 29 && !isLeap(ty)) return dayOf(ty, 2, 28);
  return dayOf(ty, m, d);
}

function isLeap(y) {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

// Years first, then days — the Python `_bound` order, so the Feb-29 clamp applies
// before the day offset on both sides.
function bound(anchor, params, sign) {
  let out = anchor;
  if (params.years !== undefined && params.years !== null) out = shiftYears(out, sign * Number(params.years));
  if (params.days !== undefined && params.days !== null) out = out + sign * Number(params.days);
  return out;
}

// ── kinds (mirror of engine._BUILTIN_KINDS) ────────────────────────────────────

const KINDS = {
  not_future: (v, today) => v[0] <= today,
  not_past: (v, today) => v[0] >= today,
  max_age: (v, today, p) => v[0] >= bound(today, p, -1),
  min_age: (v, today, p) => v[0] <= bound(today, p, -1),
  max_future: (v, today, p) => v[0] <= bound(today, p, +1),
  not_before: (v, _t, p) => v[0] >= toDay(p.date, "params.date"),
  not_after: (v, _t, p) => v[0] <= toDay(p.date, "params.date"),
  order: (v, _t, p) => (p.strict ? v[0] < v[1] : v[0] <= v[1]),
  max_span: (v, _t, p) => v[1] <= bound(v[0], p, +1),
  min_span: (v, _t, p) => v[1] >= bound(v[0], p, +1),
};

/** The kinds this client implements — the server's `builtin_kinds()`. */
export const BUILTIN_KINDS = Object.freeze(Object.keys(KINDS));

/**
 * Kinds in `rules` this client does not implement (host-registered, server-only).
 * Rules of these kinds are skipped by `evaluate`; the server still enforces them.
 * @param {Rule[]} rules
 * @returns {string[]}
 */
export function unsupportedKinds(rules) {
  return [...new Set(rules.map((r) => r.kind).filter((k) => !(k in KINDS)))];
}

// ── messages ───────────────────────────────────────────────────────────────────

/** Substitute simple `{name}` placeholders from `params`; unknown names stay as
 * written. Same rule as the Python `render_message`. */
export function renderMessage(message, params) {
  return String(message).replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (whole, name) =>
    params && Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : whole,
  );
}

// ── evaluation ─────────────────────────────────────────────────────────────────

function targetOf(rule) {
  if (rule.field) return rule.field;
  const own = rule.fields.filter((f) => !f.includes("."));
  return own.length ? own[own.length - 1] : rule.fields[rule.fields.length - 1];
}

function effective(record, changes, context, field) {
  if (Object.prototype.hasOwnProperty.call(changes, field)) return changes[field];
  if (context && Object.prototype.hasOwnProperty.call(context, field)) return context[field];
  if (field.includes(".")) return null; // namespaced parent field not supplied → skip
  return record ? record[field] : null;
}

/**
 * Evaluate `rules` against applying `changes` to `record` (`null` on create).
 *
 * Mirrors the server: a rule fires only when `changes` touches one of its fields,
 * is skipped when any value it reads is absent, reads `parent.field` names from
 * `context`, and reports violations in catalog order attached to the rule's target
 * field. Rules of a kind this client does not know are skipped (see
 * `unsupportedKinds`).
 *
 * @param {Rule[]} rules  rules for one entity (or pass `entity` in opts to filter)
 * @param {Record<string, unknown>} changes
 * @param {{record?: object|null, context?: Record<string, unknown>, today?: Date|string, entity?: string}} [opts]
 * @returns {Violation[]}
 */
export function evaluate(rules, changes, opts = {}) {
  const today = opts.today === undefined ? todayDay() : toDay(opts.today, "today");
  const record = opts.record ?? null;
  const context = opts.context ?? null;
  const scoped = opts.entity ? rules.filter((r) => r.entity === opts.entity) : rules;
  const out = [];
  for (const rule of scoped) {
    if (!rule.fields.some((f) => Object.prototype.hasOwnProperty.call(changes, f))) continue;
    const check = KINDS[rule.kind];
    if (!check) continue;
    const vals = rule.fields.map((f) => toDay(effective(record, changes, context, f), f));
    if (vals.some((v) => v === null)) continue;
    const params = rule.params ?? {};
    if (!check(vals, today, params)) {
      out.push({
        field: targetOf(rule),
        code: rule.code,
        message: renderMessage(rule.message, params),
        params,
        kind: rule.kind,
      });
    }
  }
  return out;
}

/**
 * First message per field, in catalog order — the shape most form libraries take
 * as `errors`. Works on client violations and on `fromServer422` output alike,
 * which is the point: one renderer for both paths.
 * @param {Violation[]} violations
 * @returns {Record<string, string>}
 */
export function fieldErrors(violations) {
  const out = {};
  for (const v of violations) if (!(v.field in out)) out[v.field] = v.message;
  return out;
}

/**
 * Read a FastAPI 422 body (`{detail: [{loc, msg, type, ctx?}]}`) — ours or
 * Pydantic's — into the same `Violation` shape `evaluate` returns. `type`
 * `value_error.<code>` yields `code`; any other type is kept verbatim as the code.
 * `loc` `["body", "field"]` yields `field`; a nested `loc` joins with dots.
 * @param {{detail?: unknown}|unknown[]} body
 * @returns {Violation[]}
 */
export function fromServer422(body) {
  const detail = Array.isArray(body) ? body : body && Array.isArray(body.detail) ? body.detail : [];
  return detail.map((d) => {
    const loc = Array.isArray(d.loc) ? d.loc.filter((p, i) => !(i === 0 && (p === "body" || p === "query" || p === "path"))) : [];
    const type = typeof d.type === "string" ? d.type : "";
    return {
      field: loc.map(String).join(".") || "",
      code: type.startsWith("value_error.") ? type.slice("value_error.".length) : type,
      message: typeof d.msg === "string" ? d.msg : "",
      params: d.ctx && typeof d.ctx === "object" ? d.ctx : {},
      kind: undefined,
    };
  });
}

/**
 * Fetch the catalog. Sends `If-None-Match` when `etag` is given and returns the
 * cached `rules` on 304, so a host can keep one copy per session cheaply.
 * @param {string} url  e.g. `${API}/validation/rules`
 * @param {{fetch?: typeof fetch, entity?: string, etag?: string|null, rules?: Rule[], headers?: Record<string,string>}} [opts]
 * @returns {Promise<{rules: Rule[], etag: string|null}>}
 */
export async function fetchRules(url, opts = {}) {
  const f = opts.fetch ?? globalThis.fetch;
  if (!f) throw new Error("fetchRules: no fetch available; pass opts.fetch");
  const target = opts.entity ? `${url}${url.includes("?") ? "&" : "?"}entity=${encodeURIComponent(opts.entity)}` : url;
  const headers = { Accept: "application/json", ...(opts.headers ?? {}) };
  if (opts.etag) headers["If-None-Match"] = opts.etag;
  const res = await f(target, { headers });
  if (res.status === 304 && opts.rules) return { rules: opts.rules, etag: opts.etag ?? null };
  if (!res.ok) throw new Error(`fetchRules: ${res.status} from ${target}`);
  return { rules: await res.json(), etag: res.headers?.get?.("ETag") ?? null };
}

/**
 * A validator bound to one catalog: `validate(entity, changes, opts)` and
 * `rulesFor(entity)`. Convenience over `evaluate` for hosts that fetch the whole
 * catalog once at boot.
 * @param {Rule[]} rules
 * @param {{today?: Date|string}} [defaults]
 */
export function createValidator(rules, defaults = {}) {
  const byEntity = new Map();
  for (const r of rules) {
    if (!byEntity.has(r.entity)) byEntity.set(r.entity, []);
    byEntity.get(r.entity).push(r);
  }
  return {
    rulesFor: (entity) => byEntity.get(entity) ?? [],
    validate: (entity, changes, opts = {}) =>
      evaluate(byEntity.get(entity) ?? [], changes, { ...defaults, ...opts }),
    unsupportedKinds: () => unsupportedKinds(rules),
    entities: () => [...byEntity.keys()],
  };
}
