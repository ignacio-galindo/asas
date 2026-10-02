# @asas/validation-client

Browser mirror of [asas-validation](../README.md): evaluates the rule catalog served
by `GET /validation/rules` with the same semantics as the Python engine, so a form
refuses exactly what the server would refuse before the round trip.

- ESM, zero dependencies, no build step. Vendor `asas-validation.js` +
  `asas-validation.d.ts`, or add this folder as a workspace package.
- Requires asas-validation **≥ 0.12** on the server (the payload carries `entity`).
- Tests: `node --test` (Node ≥ 20). `test/conformance.test.js` runs the same
  `../conformance/cases.json` the Python suite runs.

Usage and the API surface are documented in the package README under
**The browser client**.
