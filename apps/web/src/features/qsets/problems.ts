import { isApiError, type ApiError, type ProblemField } from "@/shared/api/errors";

/** The kinds of conflict the sets API tells apart by the end of the problem `type`. */
export type ConflictKind = "stale" | "item-has-decisions";

const SLUG = /[^:]*$/;

export function problemSlug(error: ApiError): string {
  return SLUG.exec(error.type)?.[0] ?? "";
}

/** A 409 is either "the draft changed since you read it" or "this item has decisions, so its text is fixed". */
export function conflictKind(error: unknown): ConflictKind | null {
  if (!isApiError(error, 409)) return null;
  return problemSlug(error) === "item-has-decisions" ? "item-has-decisions" : "stale";
}

/** Decodes one JSON pointer into its parts (`/gold_papers/0/doc_id`, with `~1` and `~0`). */
export function pointerParts(path: string): string[] {
  if (path === "" || path === "/") return [];
  return path
    .replace(/^\//, "")
    .split("/")
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"));
}

export interface FieldProblems {
  /** Problems by the first part of their pointer: the field of the form that shows them. */
  byField: Map<string, ProblemField[]>;
  /** Problems about the item as a whole (an empty pointer). */
  whole: ProblemField[];
}

export function groupByField(errors: readonly ProblemField[]): FieldProblems {
  const byField = new Map<string, ProblemField[]>();
  const whole: ProblemField[] = [];
  for (const error of errors) {
    const [first] = pointerParts(error.path);
    if (first === undefined) {
      whole.push(error);
      continue;
    }
    const list = byField.get(first) ?? [];
    list.push(error);
    byField.set(first, list);
  }
  return { byField, whole };
}

/** The pointer without its first part, for a message shown inside a field (`/0/doc_id`). */
export function innerPath(path: string): string {
  const parts = pointerParts(path).slice(1);
  return parts.length === 0 ? "" : `/${parts.join("/")}`;
}

/** The problems about one row of a list field (`/key_facts/2/rel` is row 2, field `rel`). */
export function rowProblems(
  problems: readonly ProblemField[] | undefined,
  index: number,
): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const problem of problems ?? []) {
    const [, row, field] = pointerParts(problem.path);
    if (row !== String(index)) continue;
    const key = field ?? "";
    found.set(key, [...(found.get(key) ?? []), problem.message]);
  }
  return found;
}
