import { CAPTURE } from "@/test/ask-api";
import { readAnswer, readFailure, readStreamEvent } from "./types";

describe("reading the events of the real API", () => {
  it("reads every event it recorded", () => {
    for (const ask of CAPTURE.asks) {
      for (const event of ask.events) {
        expect(
          readStreamEvent(event.event, event.data),
          `${ask.name}: ${event.event}`,
        ).not.toBeNull();
      }
    }
  });

  it("reads the position of a queued event", () => {
    expect(readStreamEvent("queued", '{"ask_id":"a","position":2}')).toEqual({
      kind: "queued",
      position: 2,
    });
  });

  it("reads the phases, which carry only the ask id", () => {
    expect(readStreamEvent("retrieving", '{"ask_id":"a"}')).toEqual({ kind: "retrieving" });
    expect(readStreamEvent("answering", '{"ask_id":"a"}')).toEqual({ kind: "answering" });
    expect(readStreamEvent("cancelled", '{"ask_id":"a"}')).toEqual({ kind: "cancelled" });
  });

  it("reads an answer with its citations, contexts and timings", () => {
    const ask = CAPTURE.asks.find((item) => item.name === "transmon")!;
    const answer = ask.events.find((item) => item.event === "answer")!;
    const event = readStreamEvent("answer", answer.data);
    expect(event?.kind).toBe("answer");
    if (event?.kind !== "answer") return;
    expect(event.response.citations.length).toBeGreaterThan(0);
    expect(event.response.contexts.length).toBeGreaterThan(0);
    expect(event.response.answer).not.toContain("References");
  });

  it("reads a failure with its retry hint", () => {
    const ask = CAPTURE.asks.find((item) => item.name === "unavailable")!;
    const error = ask.events.find((item) => item.event === "error")!;
    const event = readStreamEvent("error", error.data);
    expect(event).toMatchObject({
      kind: "error",
      failure: { status: 503, kind: "unavailable", retry_after_s: 5 },
    });
  });
});

describe("refusing what the API does not send", () => {
  it("refuses a body that is not JSON", () => {
    expect(readStreamEvent("answer", "not json")).toBeNull();
  });

  it("refuses data that is not text", () => {
    expect(readStreamEvent("answer", undefined)).toBeNull();
    expect(readStreamEvent("answer", 12)).toBeNull();
  });

  it("refuses an event it does not know", () => {
    expect(readStreamEvent("surprise", "{}")).toBeNull();
  });

  it("refuses a queued event without a position", () => {
    expect(readStreamEvent("queued", '{"ask_id":"a"}')).toBeNull();
    expect(readStreamEvent("queued", '{"ask_id":"a","position":-1}')).toBeNull();
    expect(readStreamEvent("queued", '{"ask_id":"a","position":"1"}')).toBeNull();
    expect(readStreamEvent("queued", "[]")).toBeNull();
  });

  it("refuses an answer with a missing or mistyped field", () => {
    const good = JSON.parse(
      CAPTURE.asks[0]!.events.find((item) => item.event === "answer")!.data,
    ) as Record<string, unknown>;
    expect(readAnswer(good)).not.toBeNull();
    const broken: [string, unknown][] = [
      ["answer", 3],
      ["ask_id", undefined],
      ["references_text", null],
      ["index_generation", 4],
      ["sources", "x"],
      ["sources", [1]],
      ["warnings", [null]],
      ["citations", "x"],
      ["citations", [{ key: "k" }]],
      ["citations", [{ key: "k", doc_id: "d", first_page: 1.5, last_page: 2 }]],
      ["contexts", [{ doc_id: "d" }]],
      ["timings", null],
      ["timings", { retrieve_s: 1, answer_s: "2", wall_s: 3 }],
    ];
    for (const [field, value] of broken) {
      expect(readAnswer({ ...good, [field]: value }), `${field} = ${String(value)}`).toBeNull();
    }
    expect(readAnswer(null)).toBeNull();
    expect(readAnswer([])).toBeNull();
  });

  it("refuses a context with a mistyped field and accepts a score", () => {
    const base = JSON.parse(
      CAPTURE.asks[0]!.events.find((item) => item.event === "answer")!.data,
    ) as { contexts: Record<string, unknown>[] } & Record<string, unknown>;
    const context = base.contexts[0]!;
    expect(readAnswer({ ...base, contexts: [{ ...context, score: 0.5 }] })).not.toBeNull();
    expect(readAnswer({ ...base, contexts: [{ ...context, score: "high" }] })).toBeNull();
    expect(readAnswer({ ...base, contexts: [{ ...context, is_reference: "no" }] })).toBeNull();
    expect(readAnswer({ ...base, contexts: [{ ...context, text: 1 }] })).toBeNull();
  });

  it("refuses a failure with a missing or mistyped field", () => {
    const good = { ask_id: "a", kind: "unavailable", status: 503, message: "try later" };
    expect(readFailure(good)).toMatchObject({ retry_after_s: null });
    expect(readFailure({ ...good, retry_after_s: 5 })).toMatchObject({ retry_after_s: 5 });
    expect(readFailure({ ...good, retry_after_s: "5" })).toBeNull();
    expect(readFailure({ ...good, status: "503" })).toBeNull();
    expect(readFailure({ ...good, message: undefined })).toBeNull();
    expect(readFailure("failed")).toBeNull();
  });
});
