/** A rule as served by `GET /validation/rules` (asas-validation ≥ 0.12). */
export interface Rule {
  entity: string;
  kind: string;
  /** Fields the rule reads, in kind order (`order` = [earlier, later]). `parent.field` names read from `context`. */
  fields: string[];
  /** The field a violation attaches to. */
  field: string;
  /** Unique across the catalog; the server's 422 `type` is `value_error.<code>`. */
  code: string;
  /** May contain `{name}` placeholders resolved from `params`. */
  message: string;
  params: RuleParams;
}

export interface RuleParams {
  years?: number;
  days?: number;
  /** ISO `YYYY-MM-DD` bound for `not_before` / `not_after`. */
  date?: string;
  /** `order` only: `earlier < later` instead of `<=`. */
  strict?: boolean;
  [key: string]: unknown;
}

export interface Violation {
  field: string;
  code: string;
  /** Rendered message (placeholders substituted). */
  message: string;
  params: Record<string, unknown>;
  /** Undefined for violations read back from a server 422. */
  kind: string | undefined;
}

export type DateInput = Date | string | null | undefined;

export interface EvaluateOptions {
  /** The persisted record, for fields the edit does not touch. `null` on create. */
  record?: Record<string, unknown> | null;
  /** Values for `parent.field` names. */
  context?: Record<string, unknown>;
  /** Override "today" (default: the user's local calendar date). */
  today?: Date | string;
  /** Filter `rules` to one entity before evaluating. */
  entity?: string;
}

export const BUILTIN_KINDS: readonly string[];

export function toDay(value: unknown, field?: string): number | null;
export function fromDay(day: number): string;
export function todayDay(): number;
export function shiftYears(day: number, years: number): number;
export function unsupportedKinds(rules: Rule[]): string[];
export function renderMessage(message: string, params?: Record<string, unknown>): string;
export function evaluate(rules: Rule[], changes: Record<string, unknown>, opts?: EvaluateOptions): Violation[];
export function fieldErrors(violations: Violation[]): Record<string, string>;
export function fromServer422(body: { detail?: unknown } | unknown[]): Violation[];

export interface FetchRulesOptions {
  fetch?: typeof fetch;
  entity?: string;
  /** Previous ETag; on 304 the previous `rules` are returned. */
  etag?: string | null;
  rules?: Rule[];
  headers?: Record<string, string>;
}
export function fetchRules(url: string, opts?: FetchRulesOptions): Promise<{ rules: Rule[]; etag: string | null }>;

export interface Validator {
  rulesFor(entity: string): Rule[];
  validate(entity: string, changes: Record<string, unknown>, opts?: EvaluateOptions): Violation[];
  unsupportedKinds(): string[];
  entities(): string[];
}
export function createValidator(rules: Rule[], defaults?: { today?: Date | string }): Validator;
