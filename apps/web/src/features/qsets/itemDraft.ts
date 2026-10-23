import type { Item } from "./types";

/** JSON with the keys of every object in order, so two equal values give the same text. */
export function stable(value: unknown): string {
  return JSON.stringify(value, (_key, inner: unknown) => {
    if (inner !== null && typeof inner === "object" && !Array.isArray(inner)) {
      return Object.fromEntries(
        Object.entries(inner as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)),
      );
    }
    return inner;
  });
}

export function sameValue(a: unknown, b: unknown): boolean {
  return stable(a) === stable(b);
}

export interface Changes {
  /** Fields that are new or have another value, with the value the draft has now. */
  changed: Item;
  /** Fields the draft no longer has. */
  removed: string[];
}

/** What the draft changes in the item as it was read. */
export function changesBetween(original: Item, draft: Item): Changes {
  const changed: Item = {};
  for (const [key, value] of Object.entries(draft)) {
    if (!(key in original) || !sameValue(original[key], value)) changed[key] = value;
  }
  const removed = Object.keys(original).filter((key) => !(key in draft));
  return { changed, removed };
}

export function hasChanges(changes: Changes): boolean {
  return Object.keys(changes.changed).length > 0 || changes.removed.length > 0;
}

/** Sets a field, or takes it out when the value is empty (an optional text left blank is absent). */
export function withField(draft: Item, key: string, value: unknown): Item {
  const next = { ...draft };
  if (value === undefined || value === "" || value === null) delete next[key];
  else next[key] = value;
  return next;
}

export function text(item: Item, key: string): string {
  const value = item[key];
  return typeof value === "string" ? value : "";
}

export function isMulti(form: string): boolean {
  return form.startsWith("multi_");
}

/** The forms whose item must say how the papers combine (`synthesis`) and which passages are needed. */
export function needsSynthesis(form: string): boolean {
  return ["multi_compare", "multi_synthesis", "multi_conflict", "multi_chain"].includes(form);
}

export function isCircuitForm(form: string): boolean {
  return form.startsWith("circuit_");
}

export function needsSearchRecord(form: string): boolean {
  return form === "unanswerable" || form === "multi_list";
}

/** Fields that the form edits with their own controls. Anything else goes to "other fields". */
export const HANDLED_FIELDS = [
  "id",
  "lang",
  "lang_origin",
  "mirror_of",
  "form",
  "content",
  "corpus",
  "split",
  "question",
  "answer",
  "rationale",
  "notes",
  "tags",
  "key_facts",
  "gold_papers",
  "synthesis",
  "necessity",
  "circuit",
  "search_record",
] as const;

export function otherFields(item: Item): Item {
  const handled = new Set<string>(HANDLED_FIELDS);
  return Object.fromEntries(Object.entries(item).filter(([key]) => !handled.has(key)));
}

/** Replaces every field that is not handled by the form with `other`. */
export function withOtherFields(draft: Item, other: Item): Item {
  const handled = new Set<string>(HANDLED_FIELDS);
  const kept = Object.fromEntries(Object.entries(draft).filter(([key]) => handled.has(key)));
  return { ...kept, ...other };
}

export function newItemTemplate(corpus: string | null | undefined): Item {
  return {
    id: "",
    lang: "en",
    form: "single",
    content: "other",
    corpus: corpus ?? "",
    question: "",
    gold_papers: [],
  };
}

export type JsonParse = { ok: true; value: unknown } | { ok: false; message: string };

export function parseJson(textValue: string): JsonParse {
  try {
    return { ok: true, value: JSON.parse(textValue) as unknown };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : String(error) };
  }
}

export function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Short text for a value in a list of changes: strings as they are, anything else as JSON. */
export function describeValue(value: unknown, max = 160): string {
  const raw = typeof value === "string" ? value : (JSON.stringify(value) ?? "");
  return raw.length > max ? `${raw.slice(0, max)}...` : raw;
}

export function keyFactsOf(item: Item): Record<string, unknown>[] {
  const facts = item.key_facts;
  if (!Array.isArray(facts)) return [];
  return facts.map((fact) => (isPlainObject(fact) ? fact : {}));
}

export function tagsOf(item: Item): [string, string][] {
  const tags = item.tags;
  if (!isPlainObject(tags)) return [];
  return Object.entries(tags).map(([key, value]) => [key, typeof value === "string" ? value : ""]);
}

/** Tags from rows. A row with no key is dropped; a key written twice keeps the last. */
export function tagsFromRows(rows: readonly (readonly [string, string])[]): Item | undefined {
  const tags: Record<string, string> = {};
  for (const [key, value] of rows) {
    if (key.trim() !== "") tags[key.trim()] = value;
  }
  return Object.keys(tags).length === 0 ? undefined : tags;
}
