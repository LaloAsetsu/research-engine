import { CONTENTS, FORMS, LANGS, REVIEW_STATES, SPLITS, type ReviewState } from "./types";

export const PAGE_SIZE = 50;

export interface ItemFilters {
  form?: string;
  content?: string;
  lang?: string;
  split?: string;
  tag?: string;
  review?: ReviewState;
  q?: string;
}

export const FILTER_KEYS = ["form", "content", "lang", "split", "tag", "review", "q"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

const ENUMS: Partial<Record<FilterKey, readonly string[]>> = {
  form: FORMS,
  content: CONTENTS,
  lang: LANGS,
  split: SPLITS,
  review: REVIEW_STATES,
};

/** Reads the filters from the address. A value the API would refuse is dropped, not sent. */
export function readFilters(params: URLSearchParams): ItemFilters {
  const filters: ItemFilters = {};
  for (const key of FILTER_KEYS) {
    const value = params.get(key)?.trim();
    if (!value) continue;
    const allowed = ENUMS[key];
    if (allowed && !allowed.includes(value)) continue;
    if (key === "review") filters.review = value as ReviewState;
    else filters[key] = key === "q" ? value.slice(0, 200) : value;
  }
  return filters;
}

/** The page the address names: a whole number of items to skip, never negative. */
export function readOffset(params: URLSearchParams): number {
  const raw = params.get("offset");
  if (raw === null || !/^\d{1,9}$/.test(raw)) return 0;
  return Number(raw);
}

/** The address after some filters change: the page goes back to the first. */
export function withFilters(
  params: URLSearchParams,
  updates: Partial<Record<FilterKey, string | undefined>>,
): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const [key, value] of Object.entries(updates)) {
    const clean = value?.trim();
    if (clean) next.set(key, clean);
    else next.delete(key);
  }
  next.delete("offset");
  return next;
}

export function withOffset(params: URLSearchParams, offset: number): URLSearchParams {
  const next = new URLSearchParams(params);
  if (offset > 0) next.set("offset", String(offset));
  else next.delete("offset");
  return next;
}

export function withoutFilters(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of FILTER_KEYS) next.delete(key);
  next.delete("offset");
  return next;
}

export function hasFilters(filters: ItemFilters): boolean {
  return Object.keys(filters).length > 0;
}
