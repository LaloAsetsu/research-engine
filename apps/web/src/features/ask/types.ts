import type { components } from "@/shared/api/schema";

type Schemas = components["schemas"];

export type AskRequest = Schemas["AskRequestBody"];
export type AskAccepted = Schemas["AskAccepted"];
export type AskResponse = Schemas["AskResponse"];
export type AskView = Schemas["AskView"];
export type AskFailure = Schemas["AskErrorOut"];
export type AskStatus = Schemas["AskStatus"];
export type Citation = Schemas["CitationOut"];
export type AskContext = Schemas["ContextOut"];
export type DocumentSummary = Schemas["DocumentSummaryOut"];
export type DocumentPage = Schemas["DocumentPageOut"];

export type KeywordMode = "english" | "always" | "never";
export const KEYWORD_MODES: readonly KeywordMode[] = ["english", "always", "never"];

/** The words of the event stream. `error` here is an event the server sends, not a broken connection. */
export const STREAM_EVENTS = [
  "queued",
  "retrieving",
  "answering",
  "answer",
  "error",
  "cancelled",
] as const;

export type StreamEvent =
  | { kind: "queued"; position: number }
  | { kind: "retrieving" }
  | { kind: "answering" }
  | { kind: "answer"; response: AskResponse }
  | { kind: "error"; failure: AskFailure }
  | { kind: "cancelled" };

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}

function readCitation(value: unknown): Citation | null {
  if (!isRecord(value)) return null;
  const { key, doc_id, first_page, last_page } = value;
  if (!isString(key) || !isString(doc_id) || !isCount(first_page) || !isCount(last_page)) {
    return null;
  }
  return { key, doc_id, first_page, last_page };
}

function readContext(value: unknown): AskContext | null {
  if (!isRecord(value)) return null;
  const { doc_id, name, start, first_page, last_page, is_reference, text, score } = value;
  if (
    !isString(doc_id) ||
    !isString(name) ||
    !isCount(start) ||
    !isCount(first_page) ||
    !isCount(last_page) ||
    typeof is_reference !== "boolean" ||
    !isString(text) ||
    !(score === undefined || score === null || isNumber(score))
  ) {
    return null;
  }
  return { doc_id, name, start, first_page, last_page, is_reference, text, score: score ?? null };
}

function readList<T>(value: unknown, read: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) return null;
  const items: T[] = [];
  for (const raw of value) {
    const item = read(raw);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

/** The body of an `answer` event, checked: a server that changes shape fails loudly, not as a blank answer. */
export function readAnswer(value: unknown): AskResponse | null {
  if (!isRecord(value)) return null;
  const { ask_id, answer, references_text, index_generation, sources, warnings, timings } = value;
  const citations = readList(value.citations, readCitation);
  const contexts = readList(value.contexts, readContext);
  if (
    !isString(ask_id) ||
    !isString(answer) ||
    !isString(references_text) ||
    !(index_generation === null || isString(index_generation)) ||
    !isStringList(sources) ||
    !isStringList(warnings) ||
    citations === null ||
    contexts === null ||
    !isRecord(timings)
  ) {
    return null;
  }
  const { retrieve_s, answer_s, wall_s } = timings;
  if (!isNumber(retrieve_s) || !isNumber(answer_s) || !isNumber(wall_s)) return null;
  return {
    ask_id,
    answer,
    references_text,
    citations,
    contexts,
    sources,
    warnings,
    index_generation,
    timings: { retrieve_s, answer_s, wall_s },
  };
}

/** The body of an `error` event. */
export function readFailure(value: unknown): AskFailure | null {
  if (!isRecord(value)) return null;
  const { ask_id, kind, status, message, retry_after_s } = value;
  if (!isString(ask_id) || !isString(kind) || !isCount(status) || !isString(message)) return null;
  if (!(retry_after_s === undefined || retry_after_s === null || isCount(retry_after_s))) {
    return null;
  }
  return { ask_id, kind, status, message, retry_after_s: retry_after_s ?? null };
}

/** One event of the stream: its name and its `data` text. Null when it is not what the API sends. */
export function readStreamEvent(name: string, data: unknown): StreamEvent | null {
  if (!isString(data)) return null;
  let body: unknown;
  try {
    body = JSON.parse(data);
  } catch {
    return null;
  }
  switch (name) {
    case "queued": {
      if (!isRecord(body) || !isCount(body.position)) return null;
      return { kind: "queued", position: body.position };
    }
    case "retrieving":
      return { kind: "retrieving" };
    case "answering":
      return { kind: "answering" };
    case "cancelled":
      return { kind: "cancelled" };
    case "answer": {
      const response = readAnswer(body);
      return response === null ? null : { kind: "answer", response };
    }
    case "error": {
      const failure = readFailure(body);
      return failure === null ? null : { kind: "error", failure };
    }
    default:
      return null;
  }
}
