import { http, HttpResponse } from "msw";
import captured from "@/features/circuits/fixtures/captured.json";

/**
 * Answers of the circuit endpoints recorded from the real API (`pnpm capture:circuits`, see
 * scripts/capture-circuit-fixtures.mjs). A request that equals a recorded one gets its recorded
 * answer; any other circuit request fails loudly, so a test never passes on an invented answer.
 */
interface Captured {
  name: string;
  path: string;
  request: unknown;
  status: number;
  response: Record<string, unknown>;
}

const ENTRIES = captured as Captured[];

export function capturedAnswer(name: string): Captured {
  const entry = ENTRIES.find((item) => item.name === name);
  if (!entry) throw new Error(`no captured answer named ${name}`);
  return entry;
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

const PROBLEM = { "content-type": "application/problem+json" };

export const circuitHandlers = [
  http.get("*/api/v1/circuits/conventions", () =>
    HttpResponse.json(capturedAnswer("conventions").response),
  ),
  http.post("*/api/v1/circuits/:operation", async ({ request, params }) => {
    const path = String(params.operation);
    const body: unknown = await request.json();
    const entry = ENTRIES.find((item) => item.path === path && sameJson(item.request, body));
    if (!entry) {
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "No recording",
          status: 500,
          detail: `No recorded answer for ${path}`,
          instance: "",
        },
        { status: 500, headers: PROBLEM },
      );
    }
    const failure = entry.status >= 400;
    return HttpResponse.json(entry.response, {
      status: entry.status,
      ...(failure ? { headers: PROBLEM } : {}),
    });
  }),
];
