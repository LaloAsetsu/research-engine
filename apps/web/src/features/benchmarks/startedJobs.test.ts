import { act, renderHook } from "@testing-library/react";
import { CAPTURE } from "@/test/benchmarks-api";
import { SETTLED_KEEP_MS, useStartedJobs } from "./startedJobs";
import type { Job } from "./types";

const DONE = CAPTURE.jobs[0]!;

function job(id: string, state: Job["state"], settled: boolean): Job {
  return { ...structuredClone(DONE), id, state, settled };
}

const first = () => [job("a", "running", false), job("b", "queued", false)];

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

function open(current: Job[] | undefined = undefined) {
  const view = renderHook(({ list }) => useStartedJobs(list), { initialProps: { list: current } });
  act(() => view.result.current.show(first()));
  return view;
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

const ids = (jobs: Job[]) => jobs.map((entry) => entry.id);

describe("the jobs the start form made", () => {
  it("lists them as they were started", () => {
    const view = open();
    expect(ids(view.result.current.jobs)).toEqual(["a", "b"]);
    expect(view.result.current.count).toBe(2);
  });

  it("shows the state the list of jobs has now", () => {
    const view = open([job("a", "done", false), job("b", "running", false)]);
    expect(view.result.current.jobs.map((entry) => entry.state)).toEqual(["done", "running"]);
  });

  it("keeps a running job for as long as it runs", () => {
    const view = open([job("a", "running", false), job("b", "queued", false)]);
    advance(SETTLED_KEEP_MS * 5);
    expect(ids(view.result.current.jobs)).toEqual(["a", "b"]);
  });

  it("drops a job 60 s after it is seen settled and keeps the others", () => {
    const view = open();
    view.rerender({ list: [job("a", "done", true), job("b", "running", false)] });
    advance(SETTLED_KEEP_MS - 1);
    expect(ids(view.result.current.jobs)).toEqual(["a", "b"]);
    advance(1);
    expect(ids(view.result.current.jobs)).toEqual(["b"]);
    expect(view.result.current.count).toBe(2);
    view.rerender({ list: [job("b", "failed", true)] });
    advance(SETTLED_KEEP_MS);
    expect(view.result.current.jobs).toEqual([]);
  });

  it("counts the 60 s from the first time it is seen settled, not from each read", () => {
    const view = open();
    view.rerender({ list: [job("a", "done", true), job("b", "running", false)] });
    advance(40_000);
    view.rerender({ list: [job("a", "done", true), job("b", "running", false)] });
    advance(20_000);
    expect(ids(view.result.current.jobs)).toEqual(["b"]);
  });

  it("keeps a settled job that is resumed before the time is up", () => {
    const view = open();
    view.rerender({ list: [job("a", "cancelled", true), job("b", "running", false)] });
    advance(30_000);
    view.rerender({ list: [job("a", "queued", false), job("b", "running", false)] });
    advance(SETTLED_KEEP_MS * 2);
    expect(ids(view.result.current.jobs)).toEqual(["a", "b"]);
    view.rerender({ list: [job("a", "done", true), job("b", "running", false)] });
    advance(SETTLED_KEEP_MS);
    expect(ids(view.result.current.jobs)).toEqual(["b"]);
  });

  it("starts the count again for a new start", () => {
    const view = open();
    view.rerender({ list: [job("a", "done", true), job("b", "done", true)] });
    advance(30_000);
    act(() => view.result.current.show([job("c", "queued", false)]));
    view.rerender({ list: [job("c", "queued", false)] });
    advance(SETTLED_KEEP_MS * 2);
    expect(ids(view.result.current.jobs)).toEqual(["c"]);
    expect(view.result.current.count).toBe(1);
  });

  it("leaves no timer behind when the form goes away", () => {
    const view = open();
    view.rerender({ list: [job("a", "done", true), job("b", "done", true)] });
    expect(vi.getTimerCount()).toBe(2);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
