import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Job } from "./types";

/** How long a started job stays in the start form's list after it is seen settled. */
export const SETTLED_KEEP_MS = 60_000;

/**
 * The jobs the start form made, each as the list of jobs has it now. A job is dropped
 * `SETTLED_KEEP_MS` after it is first seen settled; one that is still going stays, and one that
 * is resumed before the time is up keeps its place. The jobs table lists every job either way.
 * `count` stays the number of jobs the start made, so the title does not shrink with the list.
 * A job that is no longer in `current` (the list is cut at 100 jobs) is shown as it was first
 * returned.
 */
export function useStartedJobs(current: readonly Job[] | undefined) {
  const [first, setFirst] = useState<Job[]>([]);
  const [count, setCount] = useState(0);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const jobs = useMemo(
    () => first.map((job) => current?.find((entry) => entry.id === job.id) ?? job),
    [first, current],
  );

  const stop = useCallback((id: string) => {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
  }, []);

  useEffect(() => {
    for (const job of jobs) {
      const waiting = timers.current.has(job.id);
      if (job.settled && !waiting) {
        timers.current.set(
          job.id,
          setTimeout(() => {
            timers.current.delete(job.id);
            setFirst((list) => list.filter((entry) => entry.id !== job.id));
          }, SETTLED_KEEP_MS),
        );
      } else if (!job.settled && waiting) {
        stop(job.id);
      }
    }
  }, [jobs, stop]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending.values()) clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const show = useCallback(
    (made: Job[]) => {
      for (const id of [...timers.current.keys()]) stop(id);
      setFirst(made);
      setCount(made.length);
    },
    [stop],
  );

  return { jobs, count, show };
}
