import type { components } from "@/shared/api/schema";

type Schemas = components["schemas"];

export type SetEntry = Schemas["SetOut"];
export type SetVersion = Schemas["SetVersionOut"];
export type ItemPage = Schemas["ItemPage"];
export type ItemSummary = Schemas["ItemSummaryOut"];
export type ItemDetail = Schemas["ItemDetail"];
export type ReviewLine = Schemas["ReviewLine"];
export type ReviewProgress = Schemas["ReviewProgressOut"];
export type FreezePlan = Schemas["FreezePlanOut"];
export type ImportShape = Schemas["ImportShapeOut"];
export type NewReview = Schemas["NewReview"];

export type ReviewState = ItemSummary["review"];
export type Verdict = NewReview["verdict"];

/** A `qset/1` item as the API stores it: an open object, checked by the server. */
export type Item = Record<string, unknown>;

export const REVIEW_STATES: readonly ReviewState[] = [
  "none",
  "needs_second",
  "accepted",
  "edited",
  "rejected",
];

export const FORMS = [
  "single",
  "multi_compare",
  "multi_synthesis",
  "multi_conflict",
  "multi_chain",
  "multi_list",
  "unanswerable",
  "neardup",
  "circuit_verify",
  "circuit_create",
  "circuit_claim",
] as const;
export const CONTENTS = ["math", "circuit", "other"] as const;
export const LANGS = ["en", "es"] as const;
export const SPLITS = ["dev", "test"] as const;
export const LANG_ORIGINS = ["native", "mirror"] as const;
export const SYNTHESIS_KINDS = [
  "order",
  "agree",
  "disagree",
  "not_comparable",
  "chain",
  "list",
  "combine",
] as const;

/** Where a set version lives in the app. */
export function versionPath(name: string, version: string): string {
  return `/sets/${encodeURIComponent(name)}/${encodeURIComponent(version)}`;
}

export function itemPath(name: string, version: string, id: string): string {
  return `${versionPath(name, version)}/items/${encodeURIComponent(id)}`;
}
