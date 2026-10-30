import type { components } from "@/shared/api/schema";

type Schemas = components["schemas"];

/**
 * The client types a fixed-length list of the schema as a plain array when a call returns it, so
 * the few records that hold one (a job's pairs, an interval) are typed here as the calls give them.
 */
export type Job = Omit<Schemas["JobOut"], "score"> & {
  score?: { score_id: string; runs: string[]; pairs: string[][] } | null;
};
export type JobState = Schemas["JobState"];
export type Model = Schemas["ModelOut"];
export type SetEntry = Schemas["SetOut"];
export type SetVersion = Schemas["SetVersionOut"];
export type RunSummary = Schemas["RunSummaryOut"];
export type RunItem = Schemas["RunItemOut"];
export type RunItems = Schemas["RunItemsOut"];
export type ScoreSummary = Schemas["ScoreSummaryOut"];
export type Score = Omit<Schemas["ScoreOut"], "compares" | "groups"> & {
  compares: ScoreCompare[];
  groups: ScoreGroup[];
};
export type ScoreGroup = Omit<Schemas["ScoreGroupOut"], "ci"> & { ci?: number[] | null };
export type ScoreCompare = Omit<Schemas["ScoreCompareOut"], "ci"> & { ci: number[] };
export type ScoreItem = Schemas["ScoreItemOut"];
export type RunRequest = Schemas["RunRequestBody"];
export type ScoreRequest = Schemas["ScoreRequestBody"];
export type ItemPage = Schemas["ItemPage"];
export type Status = Schemas["Status"];

/** The arms of a run, as the API names them. The words stay as they are in both languages. */
export const ARMS = ["bare", "harness", "oracle"] as const;
export type ArmName = (typeof ARMS)[number];

export function isArmName(value: string): value is ArmName {
  return (ARMS as readonly string[]).includes(value);
}

/** One key fact of a question, as `qset/1` writes it (only what the screen shows). */
export interface KeyFact {
  type: string;
  /** Text or a number, shown as written. */
  value: string;
  unit: string | null;
  paper: string | null;
}

export interface GoldPaper {
  docId: string;
  /** The paper's file name, when the set gives one. */
  doc: string | null;
}

/** What the scorer found for one key fact of one answer: `i` is the index in the question's `key_facts`. */
export interface KeyResult {
  index: number;
  exact: boolean;
  lenient: boolean;
}

/** The question as the set holds it, reduced to what the comparison shows. */
export interface QuestionDetail {
  id: string;
  question: string;
  answer: string | null;
  keyFacts: KeyFact[];
  goldPapers: GoldPaper[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}

/** The key results of a score item. A malformed entry is left out rather than guessed. */
export function readKeyResults(keys: unknown): KeyResult[] {
  if (!Array.isArray(keys)) return [];
  return keys.flatMap((entry: unknown) => {
    if (!isRecord(entry)) return [];
    const { i, exact, lenient } = entry;
    if (typeof i !== "number" || !Number.isInteger(i) || i < 0) return [];
    return [{ index: i, exact: exact === true, lenient: lenient === true }];
  });
}

/** The question of `GET /sets/{name}/versions/{v}/items/{id}`; the item there is an open `qset/1` object. */
export function readQuestionDetail(body: unknown): QuestionDetail | null {
  if (!isRecord(body) || !isRecord(body.item)) return null;
  const item = body.item;
  const id = text(item.id);
  const question = text(item.question);
  if (id === null || question === null) return null;
  const keyFacts = Array.isArray(item.key_facts)
    ? item.key_facts.flatMap((fact: unknown): KeyFact[] => {
        if (!isRecord(fact)) return [];
        const value = text(fact.value);
        if (value === null) return [];
        return [
          {
            type: text(fact.type) ?? "text",
            value,
            unit: text(fact.unit),
            paper: text(fact.paper),
          },
        ];
      })
    : [];
  const goldPapers = Array.isArray(item.gold_papers)
    ? item.gold_papers.flatMap((paper: unknown): GoldPaper[] => {
        if (!isRecord(paper)) return [];
        const docId = text(paper.doc_id);
        return docId === null ? [] : [{ docId, doc: text(paper.doc) }];
      })
    : [];
  return { id, question, answer: text(item.answer), keyFacts, goldPapers };
}
