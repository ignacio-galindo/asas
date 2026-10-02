// Client-specific behaviour: date handling pitfalls, unknown kinds, the 422
// round-trip, the validator wrapper, ETag-aware fetching.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  createValidator,
  evaluate,
  fetchRules,
  fieldErrors,
  fromDay,
  fromServer422,
  renderMessage,
  shiftYears,
  toDay,
  unsupportedKinds,
} from "../asas-validation.js";

const rule = (over) => ({ entity: "m", kind: "not_future", fields: ["dob"], field: "dob", code: "m.dob_future", message: "future", params: {}, ...over });

test("ISO date strings are read literally, never shifted by the UTC offset", () => {
  // `new Date("2026-09-11")` is midnight UTC, which is the 10th west of Greenwich.
  assert.equal(fromDay(toDay("2026-09-11")), "2026-09-11");
});

test("a Date is read by its local calendar components", () => {
  const d = new Date(2026, 8, 11, 23, 59); // local 11 Sep, whatever the zone
  assert.equal(fromDay(toDay(d)), "2026-09-11");
});

test("absent values are null; garbage throws with the field name", () => {
  assert.equal(toDay(null), null);
  assert.equal(toDay(undefined), null);
  assert.equal(toDay(""), null);
  assert.throws(() => toDay("yesterday", "dob"), /'dob'.*not an ISO date/);
  assert.throws(() => toDay(20260101, "dob"), /'dob'.*number/);
  assert.throws(() => toDay("2026-02-30"), /invalid calendar date/);
});

test("shiftYears clamps Feb 29 like the server", () => {
  assert.equal(fromDay(shiftYears(toDay("2028-02-29"), -18)), "2010-02-28");
  assert.equal(fromDay(shiftYears(toDay("2028-02-29"), 4)), "2032-02-29");
  assert.equal(fromDay(shiftYears(toDay("2028-02-29"), -28)), "2000-02-29"); // 2000 is leap
  assert.equal(fromDay(shiftYears(toDay("2028-02-29"), -128)), "1900-02-28"); // 1900 is not
});

test("today defaults to the local calendar date", () => {
  const now = new Date();
  const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  assert.deepEqual(evaluate([rule()], { dob: iso }), []);
});

test("unknown kinds are skipped and reported, never treated as failures", () => {
  const rules = [rule(), rule({ kind: "weekday", code: "m.weekday" })];
  assert.deepEqual(unsupportedKinds(rules), ["weekday"]);
  assert.deepEqual(evaluate(rules, { dob: "2000-01-01" }, { today: "2026-09-11" }), []);
});

test("violations carry rendered message, params and kind; fieldErrors keeps the first per field", () => {
  const rules = [
    rule({ kind: "min_age", code: "m.too_young", params: { years: 18 }, message: "At least {years}." }),
    rule({ kind: "not_before", code: "m.pre", params: { date: "2001-01-01" }, message: "Not before {date}." }),
  ];
  const got = evaluate(rules, { dob: "2000-01-01" }, { today: "2010-01-01" });
  assert.deepEqual(got, [
    { field: "dob", code: "m.too_young", message: "At least 18.", params: { years: 18 }, kind: "min_age" },
    { field: "dob", code: "m.pre", message: "Not before 2001-01-01.", params: { date: "2001-01-01" }, kind: "not_before" },
  ]);
  assert.deepEqual(fieldErrors(got), { dob: "At least 18." });
});

test("renderMessage leaves unknown placeholders and non-templates alone", () => {
  assert.equal(renderMessage("{years} and {nope}", { years: 3 }), "3 and {nope}");
  assert.equal(renderMessage("plain", {}), "plain");
  assert.equal(renderMessage("{}", null), "{}");
});

test("fromServer422 yields the same shape as evaluate, for ours and Pydantic's", () => {
  const body = {
    detail: [
      { loc: ["body", "dob"], msg: "At least 18.", type: "value_error.m.too_young", ctx: { years: 18 } },
      { loc: ["body", "address", "zip"], msg: "Field required", type: "missing" },
      { loc: ["query", "page"], msg: "Input should be >= 1", type: "greater_than_equal", ctx: { ge: 1 } },
    ],
  };
  const got = fromServer422(body);
  assert.deepEqual(got.map((v) => [v.field, v.code, v.message, v.params]), [
    ["dob", "m.too_young", "At least 18.", { years: 18 }],
    ["address.zip", "missing", "Field required", {}],
    ["page", "greater_than_equal", "Input should be >= 1", { ge: 1 }],
  ]);
  assert.deepEqual(fieldErrors(got), { dob: "At least 18.", "address.zip": "Field required", page: "Input should be >= 1" });
  assert.deepEqual(fromServer422({}), []);
  assert.deepEqual(fromServer422(null), []);
  assert.deepEqual(fromServer422(body.detail).length, 3);
});

test("client and server agree on a violation for the same edit", () => {
  // The server's 422 for this rule (see tests/test_engine.py) read back through
  // fromServer422 equals what the client says before submit.
  const r = rule({ kind: "min_age", code: "m.too_young", params: { years: 18 }, message: "At least {years}." });
  const client = evaluate([r], { dob: "2020-01-01" }, { today: "2026-09-11" });
  const server = fromServer422({ detail: [{ loc: ["body", "dob"], msg: "At least 18.", type: "value_error.m.too_young", ctx: { years: 18 } }] });
  assert.deepEqual(fieldErrors(client), fieldErrors(server));
  assert.equal(client[0].code, server[0].code);
});

test("createValidator scopes by entity and applies defaults", () => {
  const v = createValidator(
    [rule(), rule({ entity: "p", kind: "order", fields: ["start", "end"], field: "end", code: "p.order" }), rule({ entity: "p", kind: "custom", code: "p.custom" })],
    { today: "2026-09-11" },
  );
  assert.deepEqual(v.entities(), ["m", "p"]);
  assert.equal(v.rulesFor("p").length, 2);
  assert.deepEqual(v.validate("m", { dob: "2026-09-12" }).map((x) => x.code), ["m.dob_future"]);
  assert.deepEqual(v.validate("m", { dob: "2026-09-12" }, { today: "2026-09-13" }), []);
  assert.deepEqual(v.validate("p", { start: "2026-01-02", end: "2026-01-01" }).map((x) => x.field), ["end"]);
  assert.deepEqual(v.validate("zzz", { anything: "2026-01-01" }), []);
  assert.deepEqual(v.unsupportedKinds(), ["custom"]);
});

test("evaluate can filter a mixed catalog by entity", () => {
  const rules = [rule(), rule({ entity: "p", code: "p.dob_future" })];
  assert.deepEqual(evaluate(rules, { dob: "2030-01-01" }, { today: "2026-09-11", entity: "p" }).map((x) => x.code), ["p.dob_future"]);
});

test("fetchRules sends the entity filter and revalidates with the ETag", async () => {
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push({ url, headers: init.headers });
    if (init.headers["If-None-Match"] === 'W/"abc"') return { status: 304, ok: false, headers: { get: () => 'W/"abc"' } };
    return { status: 200, ok: true, headers: { get: (h) => (h === "ETag" ? 'W/"abc"' : null) }, json: async () => [rule()] };
  };
  const first = await fetchRules("/api/validation/rules", { fetch: fakeFetch, entity: "m" });
  assert.equal(calls[0].url, "/api/validation/rules?entity=m");
  assert.equal(first.etag, 'W/"abc"');
  assert.equal(first.rules.length, 1);

  const second = await fetchRules("/api/validation/rules", { fetch: fakeFetch, entity: "m", etag: first.etag, rules: first.rules });
  assert.equal(calls[1].headers["If-None-Match"], 'W/"abc"');
  assert.deepEqual(second, first);

  await assert.rejects(
    fetchRules("/x", { fetch: async () => ({ status: 500, ok: false, headers: { get: () => null } }) }),
    /500 from \/x/,
  );
});
