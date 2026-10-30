import { http, HttpResponse, type JsonBodyType } from "msw";
import captured from "@/features/ask/fixtures/captured.json";
import type { AskAccepted, AskRequest, AskView } from "@/features/ask/types";
import type { RecordedEvent } from "./event-source";

/**
 * Answers of the ask and paper endpoints recorded from the real API (`pnpm capture:ask`, see
 * scripts/capture-ask-fixtures.mjs). The papers and pages are served as recorded; a test that
 * needs a different answer builds it from a recorded one.
 */
export interface CapturedAsk {
  name: string;
  request: AskRequest;
  accepted: AskAccepted;
  events: RecordedEvent[];
  view: AskView;
}

interface Capture {
  list: JsonBodyType;
  documents: Record<string, JsonBodyType>;
  pages: Record<string, JsonBodyType>;
  missingPage: { status: number; body: JsonBodyType };
  status: JsonBodyType;
  asks: CapturedAsk[];
}

export const CAPTURE = captured as unknown as Capture;

export function capturedAsk(name: string): CapturedAsk {
  const ask = CAPTURE.asks.find((item) => item.name === name);
  if (!ask) throw new Error(`no captured ask named ${name}`);
  return ask;
}

/** The recorded `answer` event of an ask, as an object. */
export function answerOf(name: string) {
  const event = capturedAsk(name).events.find((item) => item.event === "answer");
  if (!event) throw new Error(`${name} has no answer event`);
  return JSON.parse(event.data) as AskView["response"] & object;
}

export function docIdOf(title: string): string {
  const found = Object.entries(CAPTURE.documents).find(([, doc]) =>
    (doc as { summary: { title: string } }).summary.title.includes(title),
  );
  if (!found) throw new Error(`no captured paper with "${title}" in its title`);
  return found[0];
}

const PROBLEM = { "content-type": "application/problem+json" };

export function problem(status: number, detail: string, headers: Record<string, string> = {}) {
  return HttpResponse.json(
    { type: "about:blank", title: detail, status, detail, instance: "" },
    { status, headers: { ...PROBLEM, ...headers } },
  );
}

/** The papers and their pages, as recorded. Asks are not here: each test says what the API does with them. */
export const askHandlers = [
  http.get("*/api/v1/documents/:docId/pages/:page", ({ params }) => {
    const page = CAPTURE.pages[`${String(params.docId)}:${String(params.page)}`];
    return page ? HttpResponse.json(page) : problem(404, "No such page.");
  }),
  http.get("*/api/v1/documents/:docId", ({ params }) => {
    const document = CAPTURE.documents[String(params.docId)];
    return document ? HttpResponse.json(document) : problem(404, "No such document.");
  }),
  http.get("*/api/v1/documents", () => HttpResponse.json(CAPTURE.list)),
];
