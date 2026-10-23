import { vi, type Mock } from "vitest";
import { ApiError } from "@/shared/api/errors";
import { answerOf, capturedAsk } from "@/test/ask-api";
import { FakeEventSource, replay } from "@/test/event-source";
import {
  createAskFlow,
  isBlank,
  MAX_RECOVERIES,
  RECOVER_AFTER_MS,
  RECOVER_RETRY_MS,
  requestFor,
  type FlowDeps,
} from "./flow";
import { DEFAULT_SETTINGS, useAskStore } from "./store";
import type { AskView } from "./types";

const store = useAskStore;

interface Deps extends FlowDeps {
  create: Mock<FlowDeps["create"]>;
  get: Mock<FlowDeps["get"]>;
  cancel: Mock<FlowDeps["cancel"]>;
}

function setup(overrides: Partial<FlowDeps> = {}) {
  const deps: Deps = {
    create: vi.fn(overrides.create ?? (() => Promise.resolve({ ask_id: "a1", position: 0 }))),
    get: vi.fn(overrides.get ?? (() => Promise.reject(new Error("get was not expected")))),
    cancel: vi.fn(overrides.cancel ?? (() => Promise.resolve(undefined))),
    eventSource: (url) => new FakeEventSource(url),
  };
  return { deps, flow: createAskFlow(deps) };
}

function entry() {
  const [first] = store.getState().entries;
  if (!first) throw new Error("no entry");
  return first;
}

const QUEUED = { ask_id: "a1", position: 0 };
const ANSWER = answerOf("transmon");

beforeEach(() => {
  store.getState().reset();
  FakeEventSource.reset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("what a question sends", () => {
  it("trims the text and carries the options", () => {
    expect(requestFor("  hi  ", { useIndex: false, bm25: "never", wholeBlocks: true })).toEqual({
      question: "hi",
      use_index: false,
      bm25: "never",
      whole_blocks: true,
      return_contexts: true,
    });
    expect(requestFor("hi", DEFAULT_SETTINGS)).toMatchObject({ bm25: null, whole_blocks: null });
  });

  it("calls nothing but blanks blank", () => {
    expect(isBlank("")).toBe(true);
    expect(isBlank(" \n\t ")).toBe(true);
    expect(isBlank(" a ")).toBe(false);
  });
});

describe("asking", () => {
  it("follows a question from the queue to its answer", async () => {
    const { deps, flow } = setup({
      create: vi.fn(() => Promise.resolve({ ask_id: "a1", position: 2 })),
    });
    const key = await flow.submit("  What limits T1?  ", DEFAULT_SETTINGS);
    expect(key).not.toBeNull();
    expect(deps.create).toHaveBeenCalledWith(
      expect.objectContaining({ question: "What limits T1?" }),
    );
    expect(entry()).toMatchObject({ askId: "a1", status: "queued", position: 2 });
    expect(FakeEventSource.last.url).toBe("/api/v1/asks/a1/events");

    FakeEventSource.last.emit("queued", { ask_id: "a1", position: 1 });
    expect(entry()).toMatchObject({ status: "queued", position: 1 });
    FakeEventSource.last.emit("retrieving", { ask_id: "a1" });
    expect(entry().status).toBe("retrieving");
    FakeEventSource.last.emit("answering", { ask_id: "a1" });
    expect(entry().status).toBe("answering");
    FakeEventSource.last.emit("answer", ANSWER);
    expect(entry()).toMatchObject({ status: "done", response: ANSWER });
    expect(FakeEventSource.last.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("plays back the events of every recorded answer", async () => {
    for (const name of ["transmon", "surface code", "teleportation", "spanish", "no evidence"]) {
      store.getState().reset();
      const { flow } = setup();
      await flow.submit(name, DEFAULT_SETTINGS);
      replay(FakeEventSource.last, capturedAsk(name).events);
      expect(entry().status, name).toBe("done");
    }
  });

  it("ignores an event that goes back a step", async () => {
    const { flow } = setup();
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.emit("answering", QUEUED);
    FakeEventSource.last.emit("retrieving", QUEUED);
    FakeEventSource.last.emit("queued", QUEUED);
    expect(entry().status).toBe("answering");
  });

  it("ends with the failure the server sends", async () => {
    const { flow } = setup();
    await flow.submit("simulate-unavailable", DEFAULT_SETTINGS);
    replay(FakeEventSource.last, capturedAsk("unavailable").events);
    expect(entry()).toMatchObject({
      status: "failed",
      failure: { kind: "server", failure: { status: 503, retry_after_s: 5 } },
    });
    expect(FakeEventSource.last.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("ends when the server says the question was cancelled", async () => {
    const { flow } = setup();
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.emit("cancelled", QUEUED);
    expect(entry().status).toBe("cancelled");
    expect(FakeEventSource.last.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("fails on an event it cannot read", async () => {
    const { flow } = setup();
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.emit("answer", { surprise: true });
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "malformed" } });
  });

  it("sends nothing for a blank question", async () => {
    const { deps, flow } = setup();
    expect(await flow.submit("   ", DEFAULT_SETTINGS)).toBeNull();
    expect(deps.create).not.toHaveBeenCalled();
    expect(store.getState().entries).toEqual([]);
  });

  it("sends one question at a time", async () => {
    const { deps, flow } = setup();
    await flow.submit("First", DEFAULT_SETTINGS);
    expect(await flow.submit("Second", DEFAULT_SETTINGS)).toBeNull();
    expect(deps.create).toHaveBeenCalledTimes(1);
    FakeEventSource.last.emit("answer", ANSWER);
    expect(await flow.submit("Second", DEFAULT_SETTINGS)).not.toBeNull();
    expect(deps.create).toHaveBeenCalledTimes(2);
  });

  it("shows the question as sending while the request is on its way", async () => {
    let finish: (value: { ask_id: string; position: number }) => void = () => undefined;
    const { flow } = setup({
      create: () => new Promise((resolve) => (finish = resolve)),
    });
    const sending = flow.submit("Q", DEFAULT_SETTINGS);
    expect(entry()).toMatchObject({ status: "sending", askId: null });
    finish({ ask_id: "a9", position: 0 });
    await sending;
    expect(entry()).toMatchObject({ status: "queued", askId: "a9" });
  });

  it("fails with the problem the API sent", async () => {
    const problem = new ApiError({ status: 503, detail: "Warming up.", retryAfter: 4 });
    const { flow } = setup({ create: () => Promise.reject(problem) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "api", error: problem } });
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("fails as a lost connection when the request never arrived", async () => {
    const { flow } = setup({ create: () => Promise.reject(new TypeError("Failed to fetch")) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "network" } });
  });

  it("drops the accepted ask when the session ended meanwhile", async () => {
    let finish: (value: { ask_id: string; position: number }) => void = () => undefined;
    const { flow } = setup({ create: () => new Promise((resolve) => (finish = resolve)) });
    const sending = flow.submit("Q", DEFAULT_SETTINGS);
    store.getState().reset();
    finish({ ask_id: "a1", position: 0 });
    await sending;
    expect(store.getState().entries).toEqual([]);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("stops following when the session ends", async () => {
    const { flow } = setup();
    await flow.submit("Q", DEFAULT_SETTINGS);
    const source = FakeEventSource.last;
    store.getState().reset();
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    source.emit("answer", ANSWER);
    expect(store.getState().entries).toEqual([]);
  });
});

describe("cancelling", () => {
  it("tells the API, stops the stream and marks the question", async () => {
    const { deps, flow } = setup();
    await flow.submit("Q", DEFAULT_SETTINGS);
    const source = FakeEventSource.last;
    await flow.cancel();
    expect(deps.cancel).toHaveBeenCalledWith("a1");
    expect(entry().status).toBe("cancelled");
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    source.emit("answer", ANSWER);
    expect(entry().status).toBe("cancelled");
  });

  it("stops waiting even when the API cannot be told", async () => {
    const { flow } = setup({ cancel: () => Promise.reject(new TypeError("offline")) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    await flow.cancel();
    expect(entry().status).toBe("cancelled");
  });

  it("does nothing when no question is active, or before the API knows it", async () => {
    const { deps, flow } = setup({ create: () => new Promise(() => undefined) });
    await flow.cancel();
    void flow.submit("Q", DEFAULT_SETTINGS);
    await flow.cancel();
    expect(deps.cancel).not.toHaveBeenCalled();
    expect(entry().status).toBe("sending");
  });
});

function view(change: Partial<AskView>): AskView {
  return { ask_id: "a1", state: "running", ...change };
}

describe("a connection that breaks", () => {
  it("asks the API at once when the browser gave up, and takes the answer from there", async () => {
    const { deps, flow } = setup({
      get: vi.fn(() => Promise.resolve(view({ state: "done", response: ANSWER }))),
    });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(deps.get).toHaveBeenCalledWith("a1");
    expect(entry()).toMatchObject({ status: "done", response: ANSWER });
  });

  it("gives the browser time to reconnect before it asks", async () => {
    const { deps, flow } = setup({
      get: vi.fn(() => Promise.resolve(view({ state: "done", response: ANSWER }))),
    });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(false);
    await vi.advanceTimersByTimeAsync(RECOVER_AFTER_MS - 1);
    expect(deps.get).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(deps.get).toHaveBeenCalledTimes(1);
    expect(entry().status).toBe("done");
  });

  it("does not ask when the browser reconnects and the events resume", async () => {
    const { deps, flow } = setup();
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(false);
    await vi.advanceTimersByTimeAsync(RECOVER_AFTER_MS / 2);
    FakeEventSource.last.emit("retrieving", QUEUED);
    await vi.advanceTimersByTimeAsync(RECOVER_AFTER_MS * 2);
    expect(deps.get).not.toHaveBeenCalled();
    expect(entry().status).toBe("retrieving");
  });

  it("reads a failure from the API", async () => {
    const failure = {
      ask_id: "a1",
      kind: "unavailable",
      status: 503,
      message: "later",
      retry_after_s: 5,
    };
    const { flow } = setup({
      get: () => Promise.resolve(view({ state: "failed", error: failure })),
    });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "server", failure } });
  });

  it("reads a cancel from the API", async () => {
    const { flow } = setup({ get: () => Promise.resolve(view({ state: "cancelled" })) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(entry().status).toBe("cancelled");
  });

  it("calls a finished question with nothing in it lost", async () => {
    for (const state of ["done", "failed"] as const) {
      store.getState().reset();
      const { flow } = setup({ get: () => Promise.resolve(view({ state })) });
      await flow.submit("Q", DEFAULT_SETTINGS);
      FakeEventSource.last.drop(true);
      await vi.advanceTimersByTimeAsync(0);
      expect(entry(), state).toMatchObject({ status: "failed", failure: { kind: "lost" } });
    }
  });

  it("opens the stream again for a question that is still going", async () => {
    const { flow } = setup({
      get: () => Promise.resolve(view({ state: "running", phase: "answering" })),
    });
    await flow.submit("Q", DEFAULT_SETTINGS);
    const first = FakeEventSource.last;
    first.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(entry().status).toBe("answering");
    FakeEventSource.last.emit("answer", ANSWER);
    expect(entry().status).toBe("done");
    expect(FakeEventSource.last.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("follows a queued question's position through the API", async () => {
    const { flow } = setup({
      get: () => Promise.resolve(view({ state: "queued", position: 3 })),
    });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(entry()).toMatchObject({ status: "queued", position: 3 });
  });

  it("calls a question the API no longer knows lost", async () => {
    const { flow } = setup({ get: () => Promise.reject(new ApiError({ status: 404 })) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "lost" } });
  });

  it("fails with the problem when the API refuses the lookup", async () => {
    const problem = new ApiError({ status: 403, detail: "No." });
    const { flow } = setup({ get: () => Promise.reject(problem) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "api", error: problem } });
  });

  it("tries again while the network is down, then gives up", async () => {
    const { deps, flow } = setup({ get: () => Promise.reject(new TypeError("offline")) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(entry().status).toBe("queued");
    for (let attempt = 1; attempt < MAX_RECOVERIES; attempt += 1) {
      expect(deps.get).toHaveBeenCalledTimes(attempt);
      await vi.advanceTimersByTimeAsync(RECOVER_RETRY_MS);
    }
    expect(deps.get).toHaveBeenCalledTimes(MAX_RECOVERIES);
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "network" } });
  });

  it("stops reopening a stream that keeps closing", async () => {
    const { deps, flow } = setup({
      get: () => Promise.resolve(view({ state: "running", phase: "retrieving" })),
    });
    await flow.submit("Q", DEFAULT_SETTINGS);
    for (let attempt = 0; attempt < 10 && entry().status !== "failed"; attempt += 1) {
      FakeEventSource.last.drop(true);
      await vi.advanceTimersByTimeAsync(0);
    }
    expect(entry()).toMatchObject({ status: "failed", failure: { kind: "network" } });
    expect(deps.get.mock.calls.length).toBeGreaterThan(1);
  });

  it("does nothing more once the question is over", async () => {
    const { deps, flow } = setup();
    await flow.submit("Q", DEFAULT_SETTINGS);
    const source = FakeEventSource.last;
    FakeEventSource.last.drop(false);
    await flow.cancel();
    await vi.advanceTimersByTimeAsync(RECOVER_AFTER_MS * 2);
    expect(deps.get).not.toHaveBeenCalled();
    source.drop(true);
    expect(entry().status).toBe("cancelled");
  });

  it("does not use an answer that arrives after a lookup that found the question over", async () => {
    let finish: (value: AskView) => void = () => undefined;
    const { flow } = setup({ get: () => new Promise((resolve) => (finish = resolve)) });
    await flow.submit("Q", DEFAULT_SETTINGS);
    FakeEventSource.last.drop(true);
    await vi.advanceTimersByTimeAsync(0);
    store.getState().reset();
    finish(view({ state: "done", response: ANSWER }));
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getState().entries).toEqual([]);
  });
});
