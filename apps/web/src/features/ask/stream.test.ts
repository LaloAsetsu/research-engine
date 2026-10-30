import { vi } from "vitest";
import { FakeEventSource } from "@/test/event-source";
import { askEventsUrl, followStream, type StreamHandlers } from "./stream";

function handlers() {
  return {
    onEvent: vi.fn<StreamHandlers["onEvent"]>(),
    onMalformed: vi.fn<StreamHandlers["onMalformed"]>(),
    onBroken: vi.fn<StreamHandlers["onBroken"]>(),
  };
}

function open(callbacks: StreamHandlers) {
  FakeEventSource.reset();
  const stream = followStream("ask 1", callbacks, (url) => new FakeEventSource(url));
  return { stream, source: FakeEventSource.last };
}

describe("following the events of an ask", () => {
  it("opens the events of the ask, with the id made safe for a path", () => {
    const { source } = open(handlers());
    expect(source.url).toBe("/api/v1/asks/ask%201/events");
    expect(askEventsUrl("a/b")).toBe("/api/v1/asks/a%2Fb/events");
  });

  it("hands over each event the server sends", () => {
    const callbacks = handlers();
    const { source } = open(callbacks);
    source.emit("queued", { ask_id: "a", position: 1 });
    source.emit("retrieving", { ask_id: "a" });
    expect(callbacks.onEvent.mock.calls).toEqual([
      [{ kind: "queued", position: 1 }],
      [{ kind: "retrieving" }],
    ]);
  });

  it("tells an error the server sent from a connection that broke", () => {
    const callbacks = handlers();
    const { source } = open(callbacks);
    source.emit("error", { ask_id: "a", kind: "unavailable", status: 503, message: "later" });
    expect(callbacks.onEvent).toHaveBeenCalledWith({
      kind: "error",
      failure: expect.objectContaining({ status: 503 }) as unknown,
    });
    expect(callbacks.onBroken).not.toHaveBeenCalled();

    source.drop(true);
    expect(callbacks.onBroken).toHaveBeenCalledWith(true);
    source.drop(false);
    expect(callbacks.onBroken).toHaveBeenLastCalledWith(false);
    expect(callbacks.onEvent).toHaveBeenCalledTimes(1);
  });

  it("reports an event whose body is not what the API sends", () => {
    const callbacks = handlers();
    const { source } = open(callbacks);
    source.emit("answer", "not json");
    expect(callbacks.onMalformed).toHaveBeenCalledTimes(1);
    expect(callbacks.onEvent).not.toHaveBeenCalled();
  });

  it("ignores everything after it is closed", () => {
    const callbacks = handlers();
    const { stream, source } = open(callbacks);
    expect(stream.closed).toBe(false);
    stream.close();
    expect(stream.closed).toBe(true);
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    source.emit("retrieving", {});
    source.drop(true);
    expect(callbacks.onEvent).not.toHaveBeenCalled();
    expect(callbacks.onBroken).not.toHaveBeenCalled();
  });

  it("uses the browser's EventSource when none is given", () => {
    FakeEventSource.reset();
    vi.stubGlobal("EventSource", FakeEventSource);
    try {
      const stream = followStream("x", handlers());
      expect(FakeEventSource.last.url).toBe("/api/v1/asks/x/events");
      stream.close();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("a stream that has ended", () => {
  it("counts as closed when the browser gave up, not only when we closed it", () => {
    const { stream, source } = open(handlers());
    expect(stream.closed).toBe(false);
    source.drop(false);
    expect(stream.closed).toBe(false);
    source.drop(true);
    expect(stream.closed).toBe(true);
  });
});
