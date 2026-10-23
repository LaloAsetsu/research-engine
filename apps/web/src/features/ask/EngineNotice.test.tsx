import { render, screen } from "@testing-library/react";
import { blocksAsking, EngineNotice, engineView, isStarting } from "./EngineNotice";
import type { AskStatus } from "./types";

const READY: AskStatus = {
  state: "ready",
  reason: null,
  index_generation: null,
  queued: 0,
  busy: false,
};

describe("reading the engine state", () => {
  it("knows nothing before the status arrives, and when it cannot be read", () => {
    expect(engineView(undefined, false)).toEqual({ kind: "unknown" });
    expect(engineView({ ask: READY }, true)).toEqual({ kind: "unreachable" });
    expect(engineView(undefined, true)).toEqual({ kind: "unreachable" });
  });

  it("tells a starting engine, one with a problem and a ready one apart", () => {
    expect(engineView({ ask: { ...READY, state: "warming_up" } }, false)).toEqual({
      kind: "warming",
    });
    expect(
      engineView({ ask: { ...READY, state: "unavailable", reason: "No index." } }, false),
    ).toEqual({ kind: "unavailable", reason: "No index." });
    expect(engineView({ ask: { ...READY, state: "unavailable" } }, false)).toEqual({
      kind: "unavailable",
      reason: null,
    });
    expect(engineView({ ask: READY }, false)).toEqual({ kind: "ready" });
  });

  it("treats a state it does not know as ready, so a newer API never locks the form", () => {
    const newer = { ask: { ...READY, state: "resting" as unknown as AskStatus["state"] } };
    expect(engineView(newer, false)).toEqual({ kind: "ready" });
  });

  it("closes the form while the engine starts or reports a problem (A5)", () => {
    expect(blocksAsking({ kind: "warming" })).toBe(true);
    expect(blocksAsking({ kind: "unavailable", reason: "x" })).toBe(true);
    for (const view of [{ kind: "unknown" }, { kind: "unreachable" }, { kind: "ready" }] as const) {
      expect(blocksAsking(view), view.kind).toBe(false);
    }
  });

  it("stops a retry only while the engine starts", () => {
    expect(isStarting({ kind: "warming" })).toBe(true);
    expect(isStarting({ kind: "unavailable", reason: null })).toBe(false);
    expect(isStarting({ kind: "ready" })).toBe(false);
  });
});

describe("the notice", () => {
  it("shows nothing for a ready or an unknown engine", () => {
    const { container, rerender } = render(<EngineNotice view={{ kind: "ready" }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<EngineNotice view={{ kind: "unknown" }} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
