// The shared fixture, run against the browser client. The Python twin is
// tests/test_conformance.py. A case is added to conformance/cases.json, never here.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { BUILTIN_KINDS, evaluate } from "../asas-validation.js";

const here = dirname(fileURLToPath(import.meta.url));
const { cases } = JSON.parse(readFileSync(join(here, "..", "..", "conformance", "cases.json"), "utf8"));

for (const c of cases) {
  test(c.name, () => {
    const rule = {
      entity: "e",
      kind: c.rule.kind,
      fields: c.rule.fields,
      field: undefined, // let the client derive the target like the server does
      code: c.rule.code,
      message: c.rule.message ?? "bad",
      params: c.rule.params ?? {},
    };
    const got = evaluate([rule], c.changes, { record: c.record ?? null, context: c.context, today: c.today });
    assert.deepEqual(got.map((v) => v.code), c.expect);
    if (c.expect_field) assert.equal(got[0].field, c.expect_field);
    if (c.expect_message) assert.equal(got[0].message, c.expect_message);
  });
}

test("every built-in kind has a conformance case", () => {
  const covered = new Set(cases.map((c) => c.rule.kind));
  for (const k of BUILTIN_KINDS) assert.ok(covered.has(k), `no case for ${k}`);
});
