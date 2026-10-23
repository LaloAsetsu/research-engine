import { vi } from "vitest";
import { endSession, onSessionEnd } from "./session";

describe("the end of a session", () => {
  it("tells everyone who listens, each time", () => {
    const first = vi.fn();
    const second = vi.fn();
    const stopFirst = onSessionEnd(first);
    const stopSecond = onSessionEnd(second);
    endSession();
    endSession();
    expect(first).toHaveBeenCalledTimes(2);
    expect(second).toHaveBeenCalledTimes(2);
    stopFirst();
    stopSecond();
  });

  it("stops telling someone who stopped listening", () => {
    const listener = vi.fn();
    const stop = onSessionEnd(listener);
    stop();
    endSession();
    expect(listener).not.toHaveBeenCalled();
  });

  it("does not skip a listener that stops another one meanwhile", () => {
    const second = vi.fn();
    let stopSecond: () => void = () => undefined;
    const stopFirst = onSessionEnd(() => stopSecond());
    stopSecond = onSessionEnd(second);
    endSession();
    expect(second).toHaveBeenCalledTimes(1);
    stopFirst();
    stopSecond();
  });
});
