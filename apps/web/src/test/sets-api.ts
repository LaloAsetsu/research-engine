import { http, HttpResponse } from "msw";
import captured from "@/features/qsets/fixtures/captured.json";

/**
 * Answers of the question-set endpoints recorded from the real API (`pnpm capture:sets`, see
 * scripts/capture-sets-fixtures.mjs). Reads are served from the recording, with the filters and
 * the paging applied the way the API applies them; refusals are taken by name with `problem()`,
 * so a test never passes on an answer that the API cannot give.
 */
export interface Captured {
  name: string;
  method: string;
  path: string;
  request: unknown;
  status: number;
  response: Record<string, unknown> & { items?: Record<string, unknown>[] };
}

const ENTRIES = captured as Captured[];

export function capturedAnswer(name: string): Captured {
  const entry = ENTRIES.find((item) => item.name === name);
  if (!entry) throw new Error(`no captured answer named ${name}`);
  return entry;
}

/** A deep copy of a recorded answer's body, to change in a test. */
export function capturedBody<T = Record<string, any>>(name: string): T {
  return structuredClone(capturedAnswer(name).response) as T;
}

const PROBLEM = { "content-type": "application/problem+json" };

/** The recorded refusal, with its status and as a problem document. */
export function problem(name: string, overrides: Record<string, unknown> = {}) {
  const entry = capturedAnswer(name);
  return HttpResponse.json(
    { ...entry.response, ...overrides },
    { status: entry.status, headers: PROBLEM },
  );
}

export function problemBody(status: number, type: string, detail: string, extra = {}) {
  return HttpResponse.json(
    {
      type: `urn:research-api:problem:${type}`,
      title: "Problem",
      status,
      detail,
      instance: "/",
      ...extra,
    },
    { status, headers: PROBLEM },
  );
}

type Summary = Record<string, any>;

function filterItems(items: Summary[], params: URLSearchParams): Summary[] {
  const text = params.get("q")?.toLowerCase();
  const tag = params.get("tag");
  return items.filter((item) => {
    for (const key of ["form", "content", "lang", "split", "review"] as const) {
      const wanted = params.get(key);
      if (wanted && item[key] !== wanted) return false;
    }
    if (tag) {
      const [key = "", value] = tag.split("=");
      if (!(key in item.tags)) return false;
      if (value !== undefined && item.tags[key] !== value) return false;
    }
    if (text && !`${item.id} ${item.question}`.toLowerCase().includes(text)) return false;
    return true;
  });
}

export function pageAnswer(name: string, version: string, params: URLSearchParams) {
  const entry = ENTRIES.find((item) => item.name === `page ${name}/${version}`);
  if (!entry) return null;
  const body = structuredClone(entry.response) as Record<string, any>;
  const matching = filterItems(body.items, params);
  const offset = Number(params.get("offset") ?? 0);
  const limit = Number(params.get("limit") ?? 50);
  body.total = matching.length;
  body.offset = offset;
  body.limit = limit;
  body.items = matching.slice(offset, offset + limit);
  return body;
}

export const setsHandlers = [
  http.get("*/api/v1/sets", () => HttpResponse.json(capturedAnswer("catalog").response)),
  http.get("*/api/v1/sets/import/shapes", () =>
    HttpResponse.json(capturedAnswer("shapes").response),
  ),
  http.get("*/api/v1/sets/:name/versions/:version", ({ params, request }) => {
    const body = pageAnswer(
      String(params.name),
      String(params.version),
      new URL(request.url).searchParams,
    );
    return body ? HttpResponse.json(body) : problemBody(404, "not-found", "No such set.");
  }),
  http.get("*/api/v1/sets/:name/versions/:version/items/:id", ({ params }) => {
    const entry = ENTRIES.find(
      (item) =>
        item.name === `item ${String(params.name)}/${String(params.version)}/${String(params.id)}`,
    );
    return entry
      ? HttpResponse.json(entry.response)
      : problemBody(404, "not-found", "No such item.");
  }),
  http.get("*/api/v1/sets/:name/versions/:version/freeze", ({ params }) => {
    const entry = ENTRIES.find(
      (item) => item.name === `freeze ${String(params.name)}/${String(params.version)}`,
    );
    return entry
      ? HttpResponse.json(entry.response, {
          status: entry.status,
          ...(entry.status >= 400 ? { headers: PROBLEM } : {}),
        })
      : problemBody(404, "not-found", "No such set.");
  }),
];

/** `GET /auth/me` for a signed-in member who is not an admin (the mock only knows `ana` and `temp`). */
export function meAs(username: string, role: "admin" | "member" = "member") {
  return http.get("*/api/v1/auth/me", () =>
    HttpResponse.json({
      username,
      display_name: `${username} test`,
      role,
      must_change_password: false,
      language: "en",
    }),
  );
}
