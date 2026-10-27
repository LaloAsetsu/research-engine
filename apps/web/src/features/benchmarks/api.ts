import { useEffect, useRef } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { $api, EXPECTS_UNAUTHORIZED, fetchClient } from "@/shared/api/client";
import type { ItemPage, Job, RunRequest, RunSummary, ScoreItem, ScoreRequest } from "./types";
import { readQuestionDetail, type QuestionDetail } from "./types";

const STATUS = "/api/v1/status";
const ME = "/api/v1/auth/me";
const MODELS = "/api/v1/models";
const SETS = "/api/v1/sets";
const SET_VERSION = "/api/v1/sets/{name}/versions/{version}";
const SET_ITEM = "/api/v1/sets/{name}/versions/{version}/items/{item_id}";
const JOBS = "/api/v1/jobs";
const JOB = "/api/v1/jobs/{job_id}";
const JOB_LOG = "/api/v1/jobs/{job_id}/log";
const JOB_CANCEL = "/api/v1/jobs/{job_id}/cancel";
const JOB_RESUME = "/api/v1/jobs/{job_id}/resume";
const RUNS = "/api/v1/runs";
const RUN_ITEMS = "/api/v1/runs/{run_id}/items";
const SCORES = "/api/v1/scores";
const SCORE = "/api/v1/scores/{score_id}";
const SCORE_ITEMS = "/api/v1/scores/{score_id}/items";

/** How often open things are read again, in milliseconds. Tests shorten them. */
export const polling = {
  /** One job, its log and the lists while a job is open (the scope: every 2 s). */
  jobMs: 2000,
  /** The lists when nothing is open: another member may start something. */
  idleMs: 15_000,
  /** Models answer or not as their servers come and go. */
  modelsMs: 30_000,
  /** The service status, which the top bar reads with the same key. */
  statusMs: 30_000,
};

const PAGE = 200;

// ---- reads -----------------------------------------------------------------------------

/** The service status. The top bar reads the same endpoint with the same key. */
export function useServiceStatus() {
  return $api.useQuery("get", STATUS, {}, { retry: false, refetchInterval: polling.statusMs });
}

/** The signed-in user, from the cache the route guard filled. Who may cancel a job depends on it. */
export function useMe() {
  return $api.useQuery(
    "get",
    ME,
    {},
    { staleTime: 5 * 60_000, retry: false, meta: EXPECTS_UNAUTHORIZED },
  );
}

export function useModels() {
  return $api.useQuery("get", MODELS, {}, { refetchInterval: polling.modelsMs });
}

export function useSets() {
  return $api.useQuery("get", SETS, {});
}

/** The jobs, newest first. They are read again every 2 s while one is open. */
export function useJobs() {
  return $api.useQuery(
    "get",
    JOBS,
    { params: { query: { limit: 100 } } },
    {
      refetchInterval: (query) =>
        query.state.data?.some((job) => !job.settled) ? polling.jobMs : polling.idleMs,
    },
  );
}

/** The runs, finished and partial. `refreshing` is true while a job is open, so a new answer count shows. */
export function useRuns(refreshing: boolean) {
  return $api.useQuery(
    "get",
    RUNS,
    {},
    { refetchInterval: refreshing ? polling.jobMs : polling.idleMs },
  );
}

/** One job; read every 2 s until it is settled (done, failed, cancelled or interrupted). */
export function useJob(jobId: string | undefined, enabled = true) {
  return $api.useQuery(
    "get",
    JOB,
    { params: { path: { job_id: jobId ?? "" } } },
    {
      enabled: enabled && jobId !== undefined,
      retry: false,
      refetchInterval: (query) => (query.state.data?.settled === false ? polling.jobMs : false),
    },
  );
}

export interface LogTail {
  text: string;
  /** Where the next read starts: bytes of the log already read. */
  offset: number;
  /**
   * True once the log was read to its end by a read that began a poll interval after the job
   * settled and found nothing new. Until then the closing lines may still be missing.
   */
  closed: boolean;
}

/** The most pages one read follows: a guard against a server that never reaches the end. */
const MAX_LOG_PAGES = 1000;

/**
 * The log of a job, from the byte where the last read stopped, appended to what is held. The
 * query cache keeps the text. The API cuts a read at its page size, so a read follows the pages
 * until it reaches the size of the log. A log that shrank (the file was replaced) is read again
 * from 0. The runner settles a job before it writes its closing lines, so the reading goes on
 * after the job settles, until a read that began one poll interval after it settled adds
 * nothing: the read made at once may still come before the runner's closing write.
 */
export function useJobLog(jobId: string | undefined, settled: boolean | undefined) {
  const queryClient = useQueryClient();
  const key = ["bench", "log", jobId] as const;
  // when the job was first seen settled: only a read begun a poll interval later can close the log
  const settledAt = useRef<number | null>(null);
  const query = useQuery<LogTail>({
    queryKey: key,
    enabled: jobId !== undefined,
    retry: false,
    staleTime: 0,
    refetchInterval: (current) =>
      settled === true && current.state.data?.closed === true ? false : polling.jobMs,
    queryFn: async () => {
      const held = queryClient.getQueryData<LogTail>(key) ?? {
        text: "",
        offset: 0,
        closed: false,
      };
      const afterSettled =
        settled === true &&
        settledAt.current !== null &&
        Date.now() - settledAt.current >= polling.jobMs;
      const read = async (offset: number) => {
        const { data, response } = await fetchClient.GET(JOB_LOG, {
          params: { path: { job_id: jobId ?? "" }, query: { offset } },
          parseAs: "text",
        });
        const next = Number(response.headers.get("X-Log-Next-Offset"));
        const size = Number(response.headers.get("X-Log-Size"));
        return {
          chunk: data ?? "",
          next: Number.isFinite(next) && next >= offset ? next : offset,
          size,
        };
      };
      let text = held.text;
      let offset = held.offset;
      let grew = false;
      let replaced = false;
      for (let pages = 0; pages < MAX_LOG_PAGES; pages += 1) {
        const result = await read(offset);
        if (!replaced && offset > 0 && Number.isFinite(result.size) && result.size < offset) {
          // The log is shorter than what was read: it was replaced, so read it again from its start.
          text = "";
          offset = 0;
          replaced = true;
          continue;
        }
        if (result.next > offset) grew = true;
        text += result.chunk;
        const more = Number.isFinite(result.size) && result.next < result.size;
        if (!more || result.next === offset) {
          offset = result.next;
          break;
        }
        offset = result.next;
      }
      return { text, offset, closed: afterSettled && !grew && !replaced };
    },
  });
  // The moment the job is seen settled, read once more instead of waiting for the next tick.
  const { refetch } = query;
  useEffect(() => {
    if (settled !== true) {
      settledAt.current = null;
      return;
    }
    settledAt.current = Date.now();
    const current = queryClient.getQueryData<LogTail>(["bench", "log", jobId]);
    if (current !== undefined && !current.closed) void refetch();
  }, [settled, jobId, queryClient, refetch]);
  return query;
}

export function useScores() {
  return $api.useQuery("get", SCORES, {}, { refetchInterval: polling.idleMs });
}

export function useScore(scoreId: string | undefined, enabled = true) {
  return $api.useQuery(
    "get",
    SCORE,
    { params: { path: { score_id: scoreId ?? "" } } },
    { enabled: enabled && scoreId !== undefined, retry: false, staleTime: Infinity },
  );
}

/**
 * Every item record of a score for one run: the API pages at 200, so a long set takes more than
 * one request. A score never changes once written.
 */
export function useScoreItems(scoreId: string | undefined, runId: string | undefined) {
  return useQuery<ScoreItem[]>({
    queryKey: ["bench", "score-items", scoreId, runId],
    enabled: scoreId !== undefined && runId !== undefined,
    staleTime: Infinity,
    retry: false,
    queryFn: async () => {
      const items: ScoreItem[] = [];
      for (let offset = 0; ; offset += PAGE) {
        const { data } = await fetchClient.GET(SCORE_ITEMS, {
          params: {
            path: { score_id: scoreId ?? "" },
            query: { run_id: runId ?? "", offset, limit: PAGE },
          },
        });
        if (!data) throw new Error("The API answered without a body.");
        items.push(...data.items);
        if (data.items.length === 0 || offset + PAGE >= data.total) return items;
      }
    },
  });
}

/** The questions of a set version (id and text), in the set's order. */
export function useSetQuestions(name: string | undefined, version: string | undefined) {
  return useQuery<ItemPage["items"]>({
    queryKey: ["bench", "questions", name, version],
    enabled: name !== undefined && version !== undefined,
    staleTime: Infinity,
    retry: false,
    queryFn: async () => {
      const items: ItemPage["items"] = [];
      for (let offset = 0; ; offset += PAGE) {
        const { data } = await fetchClient.GET(SET_VERSION, {
          params: {
            path: { name: name ?? "", version: version ?? "" },
            query: { offset, limit: PAGE },
          },
        });
        if (!data) throw new Error("The API answered without a body.");
        items.push(...data.items);
        if (data.items.length === 0 || offset + PAGE >= data.total) return items;
      }
    },
  });
}

/** One question of the set with its key facts and gold papers. */
export function useQuestionDetail(
  name: string | undefined,
  version: string | undefined,
  id: string | undefined,
) {
  return useQuery<QuestionDetail | null>({
    queryKey: ["bench", "question", name, version, id],
    enabled: name !== undefined && version !== undefined && id !== undefined,
    staleTime: Infinity,
    retry: false,
    queryFn: async () => {
      const { data } = await fetchClient.GET(SET_ITEM, {
        params: { path: { name: name ?? "", version: version ?? "", item_id: id ?? "" } },
      });
      return readQuestionDetail(data);
    },
  });
}

/** All repeats of one question in one run, joined with the question (answer, citations, contexts, timings). */
export function useRunQuestion(runId: string | undefined, id: string | undefined) {
  return $api.useQuery(
    "get",
    RUN_ITEMS,
    {
      params: {
        path: { run_id: runId ?? "" },
        query: { id: id ?? "", limit: 50, contexts: true },
      },
    },
    { enabled: runId !== undefined && id !== undefined, staleTime: Infinity, retry: false },
  );
}

// ---- writes ----------------------------------------------------------------------------

/** The key every `$api.useQuery("get", path, ...)` of a path starts with, whatever its parameters. */
const keyOf = (path: string) => ["get", path] as const;

/** Reads the lists again: a write changes what jobs, runs and scores say. */
export function refreshBenchmarks(queryClient: QueryClient, jobId?: string): Promise<unknown> {
  const pending = [JOBS, RUNS, SCORES, STATUS].map((path) =>
    queryClient.invalidateQueries({ queryKey: keyOf(path) }),
  );
  if (jobId) {
    pending.push(
      queryClient.invalidateQueries({
        queryKey: $api.queryOptions("get", JOB, { params: { path: { job_id: jobId } } }).queryKey,
      }),
      queryClient.invalidateQueries({ queryKey: ["bench", "log", jobId] }),
    );
  }
  return Promise.all(pending);
}

/**
 * One request at a time for each key. A button is disabled only after the next render, so a
 * double click sends two calls; the second one gets the answer of the first instead of a request
 * of its own.
 */
function useSingleFlight<T>() {
  const flights = useRef(new Map<string, Promise<T>>());
  return (key: string, send: () => Promise<T>): Promise<T> => {
    const held = flights.current.get(key);
    if (held) return held;
    const flight = send().finally(() => flights.current.delete(key));
    flights.current.set(key, flight);
    return flight;
  };
}

/** Starts one job for each arm and model. */
export function useStartRuns() {
  const queryClient = useQueryClient();
  const once = useSingleFlight<Job[]>();
  return useMutation<Job[], Error, RunRequest>({
    mutationFn: (body) =>
      once(JSON.stringify(body), async () => {
        const { data } = await fetchClient.POST(RUNS, { body });
        if (!data) throw new Error("The API answered without a body.");
        return data;
      }),
    onSettled: () => refreshBenchmarks(queryClient),
  });
}

export function useCancelJob() {
  const queryClient = useQueryClient();
  const once = useSingleFlight<Job>();
  return useMutation<Job, Error, string>({
    mutationFn: (jobId) =>
      once(jobId, async () => {
        const { data } = await fetchClient.POST(JOB_CANCEL, {
          params: { path: { job_id: jobId } },
        });
        if (!data) throw new Error("The API answered without a body.");
        return data;
      }),
    onSettled: (_data, _error, jobId) => refreshBenchmarks(queryClient, jobId),
  });
}

export function useResumeJob() {
  const queryClient = useQueryClient();
  const once = useSingleFlight<Job>();
  return useMutation<Job, Error, string>({
    mutationFn: (jobId) =>
      once(jobId, async () => {
        const { data } = await fetchClient.POST(JOB_RESUME, {
          params: { path: { job_id: jobId } },
        });
        if (!data) throw new Error("The API answered without a body.");
        return data;
      }),
    onSettled: (_data, _error, jobId) => refreshBenchmarks(queryClient, jobId),
  });
}

/** Starts a scoring (a job of the CPU lane). Its job id is the id of the score. */
export function useStartScore() {
  const queryClient = useQueryClient();
  const once = useSingleFlight<Job>();
  return useMutation<Job, Error, ScoreRequest>({
    mutationFn: (body) =>
      once(JSON.stringify(body), async () => {
        const { data } = await fetchClient.POST(SCORES, { body });
        if (!data) throw new Error("The API answered without a body.");
        return data;
      }),
    onSettled: () => refreshBenchmarks(queryClient),
  });
}

/** The jobs that made these runs, by run: what ended last tells whether a score is still current. */
export function useRunJobs(runs: readonly RunSummary[]) {
  return useQueries({
    queries: runs.map((run) => ({
      ...$api.queryOptions("get", JOB, { params: { path: { job_id: run.job_id ?? "" } } }),
      enabled: run.job_id !== undefined && run.job_id !== null,
      retry: false,
      refetchInterval: (query: { state: { data?: Job } }) =>
        query.state.data?.settled === false ? polling.jobMs : false,
    })),
    combine: (results) => ({
      pending: results.some((result) => result.isPending && result.fetchStatus !== "idle"),
      jobs: results.map((result) => result.data ?? null),
    }),
  });
}
