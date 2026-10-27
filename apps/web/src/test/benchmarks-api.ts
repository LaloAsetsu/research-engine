import { http, HttpResponse, type HttpHandler, type JsonBodyType } from "msw";
import captured from "@/features/benchmarks/fixtures/captured.json";
import type {
  ArmName,
  Job,
  Model,
  RunItems,
  RunSummary,
  Score,
  ScoreItem,
  ScoreSummary,
  SetEntry,
} from "@/features/benchmarks/types";

/**
 * Answers of the benchmark endpoints recorded from the real API in fake mode
 * (`pnpm capture:benchmarks`, see scripts/capture-benchmarks-fixtures.mjs): four runs of the demo
 * set (bare twice, harness, oracle) with two repeats each, and a score of them with two pairs and
 * a noise pair. The tests serve them as recorded, or build a different answer from them.
 */
export interface Capture {
  status: { mode: "live" | "fake" };
  models: Model[];
  sets: SetEntry[];
  jobs: Job[];
  logs: Record<string, string>;
  runs: RunSummary[];
  scoreJob: Job;
  scoreId: string;
  score: Score;
  scores: ScoreSummary[];
  questions: { id: string; question: string }[];
  details: Record<string, JsonBodyType>;
  scoreItems: Record<string, ScoreItem[]>;
  runItems: Record<string, RunItems>;
}

export const CAPTURE = captured as unknown as Capture;

/** The recorded runs by the part they play in the recorded score: the base, the two variants and the noise partner of the base. */
export const RUNS = {
  base: CAPTURE.score.summary.pairs[0]?.base_run ?? "",
  harness: CAPTURE.score.summary.pairs[0]?.var_run ?? "",
  oracle: CAPTURE.score.summary.pairs[1]?.var_run ?? "",
  noise: String(
    (CAPTURE.score.header.noise_floor as { var_run?: string } | undefined)?.var_run ?? "",
  ),
};

const PROBLEM = { "content-type": "application/problem+json" };

export function problem(status: number, detail: string, headers: Record<string, string> = {}) {
  return HttpResponse.json(
    { type: "about:blank", title: detail, status, detail, instance: "" },
    { status, headers: { ...PROBLEM, ...headers } },
  );
}

/** The recorded resources, read only. Each test says what the API does with a write. */
export const benchHandlers: HttpHandler[] = [
  http.get("*/api/v1/models", () => HttpResponse.json(CAPTURE.models)),
  http.get("*/api/v1/sets", () => HttpResponse.json(CAPTURE.sets)),
  http.get("*/api/v1/jobs/:id/log", ({ params }) => {
    const text = CAPTURE.logs[String(params.id)];
    return text === undefined
      ? problem(404, "No such job.")
      : new HttpResponse(text, {
          headers: {
            "content-type": "text/plain",
            "x-log-next-offset": String(text.length),
            "x-log-size": String(text.length),
          },
        });
  }),
  http.get("*/api/v1/jobs/:id", ({ params }) => {
    const job = [...CAPTURE.jobs, CAPTURE.scoreJob].find((entry) => entry.id === params.id);
    return job ? HttpResponse.json(job) : problem(404, "No such job.");
  }),
  http.get("*/api/v1/jobs", () => HttpResponse.json([...CAPTURE.jobs].reverse())),
  http.get("*/api/v1/runs/:id/items", ({ params, request }) => {
    const id = new URL(request.url).searchParams.get("id") ?? "";
    const found = CAPTURE.runItems[`${String(params.id)}|${id}`];
    return found ? HttpResponse.json(found) : problem(404, "No such run.");
  }),
  http.get("*/api/v1/runs", () => HttpResponse.json(CAPTURE.runs)),
  http.get("*/api/v1/scores/:id/items", ({ params, request }) => {
    const runId = new URL(request.url).searchParams.get("run_id") ?? "";
    const items = CAPTURE.scoreItems[runId] ?? [];
    return params.id === CAPTURE.scoreId
      ? HttpResponse.json({ items, total: items.length, offset: 0, limit: 200 })
      : problem(404, "No such score.");
  }),
  http.get("*/api/v1/scores/:id", ({ params }) =>
    params.id === CAPTURE.scoreId
      ? HttpResponse.json(CAPTURE.score)
      : problem(404, "No such score."),
  ),
  http.get("*/api/v1/scores", () => HttpResponse.json(CAPTURE.scores)),
  http.get("*/api/v1/sets/:name/versions/:version/items/:id", ({ params }) => {
    const detail = CAPTURE.details[String(params.id)];
    return detail ? HttpResponse.json(detail) : problem(404, "No such item.");
  }),
  http.get("*/api/v1/sets/:name/versions/:version", () =>
    HttpResponse.json({
      items: CAPTURE.questions,
      total: CAPTURE.questions.length,
      offset: 0,
      limit: 200,
    }),
  ),
];

const FIRST_ID = "01m5yz8tkg3f8xjf1z8r7yzbq";

export interface RunBody {
  set: { name: string; version: string };
  arms: { name: ArmName }[];
  models: string[];
  repeats: number;
  notes: string;
}

export interface JobStand {
  handlers: HttpHandler[];
  jobs: Job[];
  /** What each `POST /runs` received. */
  requests: unknown[];
  /** The same as `POST /runs`, without the request: the jobs it makes. */
  start: (body: RunBody) => Job[];
  /** One answer more for the job that runs; a waiting job starts when none runs. Returns the changed jobs. */
  tick: (jobId?: string) => Job[];
  /** Lines the log has now. */
  log: (jobId: string) => string;
}

/**
 * A small stand-in of the job lane: `POST /runs` makes a job for each arm and model, the first
 * runs and the rest wait; `tick` gives the running job an answer. Cancel stops it after the
 * answer in flight; resume queues it again. Enough to follow a run on the screen.
 */
export function createJobStand(
  options: { owner?: string; total?: number; models?: Model[]; mode?: "live" | "fake" } = {},
): JobStand {
  const owner = options.owner ?? "ana";
  const total = options.total ?? 6;
  let counter = 0;
  const jobs: Job[] = [];
  const logs = new Map<string, string[]>();
  const requests: unknown[] = [];

  const idOf = (n: number) => `${FIRST_ID.slice(0, 6)}${String(n).padStart(20, "0")}`;
  const running = () => jobs.find((job) => job.state === "running" || job.state === "cancelling");
  const waiting = () => jobs.filter((job) => job.state === "queued");
  const note = (job: Job, line: string) => logs.get(job.id)?.push(`2026-10-27 10:00:00Z ${line}`);

  function renumber() {
    waiting().forEach((job, index) => {
      job.position = index + (running() ? 1 : 0);
    });
    for (const job of jobs) if (job.state !== "queued") job.position = null;
  }

  function startNext() {
    if (running()) return;
    const next = waiting()[0];
    if (!next) return;
    next.state = "running";
    next.started_at = "2026-10-27T10:00:00Z";
    note(next, `started: ${next.progress.done} answers in the file`);
  }

  const asJson = (job: Job) => HttpResponse.json(structuredClone(job));
  const find = (id: unknown) => jobs.find((job) => job.id === id);

  function start(body: RunBody): Job[] {
    const made: Job[] = [];
    for (const arm of body.arms) {
      for (const model of body.models) {
        counter += 1;
        const job: Job = {
          id: idOf(counter),
          kind: "run",
          state: "queued",
          settled: false,
          owner,
          created_at: "2026-10-27T10:00:00Z",
          started_at: null,
          ended_at: null,
          progress: { done: 0, total: total * body.repeats },
          set: body.set,
          run: {
            run_id: `${arm.name}-${body.set.name}-${body.set.version}-${String(counter).padStart(8, "0")}`,
            arm: { name: arm.name, variant: null, overrides: {} },
            model,
            repeats: body.repeats,
            notes: body.notes,
          },
          score: null,
          error: null,
          position: null,
          waiting_for: null,
        };
        jobs.push(job);
        logs.set(job.id, [`2026-10-27 10:00:00Z queued by ${owner}`]);
        made.push(job);
      }
    }
    startNext();
    renumber();
    return structuredClone(made);
  }

  const handlers: HttpHandler[] = [
    http.get("*/api/v1/models", () => HttpResponse.json(options.models ?? CAPTURE.models)),
    http.get("*/api/v1/status", () =>
      HttpResponse.json({ ...CAPTURE.status, mode: options.mode ?? "fake" }),
    ),
    http.post("*/api/v1/runs", async ({ request }) => {
      const body = (await request.json()) as RunBody;
      requests.push(body);
      return HttpResponse.json(start(body), { status: 202 });
    }),
    http.get("*/api/v1/jobs/:id/log", ({ params, request }) => {
      const lines = logs.get(String(params.id));
      if (!lines) return problem(404, "No such job.");
      const text = lines.length > 0 ? `${lines.join("\n")}\n` : "";
      const offset = Number(new URL(request.url).searchParams.get("offset") ?? "0");
      return new HttpResponse(text.slice(offset), {
        headers: {
          "content-type": "text/plain",
          "x-log-next-offset": String(text.length),
          "x-log-size": String(text.length),
        },
      });
    }),
    http.post("*/api/v1/jobs/:id/cancel", ({ params }) => {
      const job = find(params.id);
      if (!job) return problem(404, "No such job.");
      if (job.state === "queued") {
        job.state = "cancelled";
        job.settled = true;
        note(job, "cancelled while queued");
      } else if (job.state === "running") {
        job.state = "cancelling";
        note(job, "cancel asked");
      } else {
        return problem(409, `A ${job.state} job cannot be cancelled.`);
      }
      renumber();
      return asJson(job);
    }),
    http.post("*/api/v1/jobs/:id/resume", ({ params }) => {
      const job = find(params.id);
      if (!job) return problem(404, "No such job.");
      if (job.state !== "cancelled" && job.state !== "interrupted") {
        return problem(409, `A ${job.state} job cannot be resumed.`);
      }
      job.state = "queued";
      job.settled = false;
      note(job, "resumed");
      startNext();
      renumber();
      return asJson(job);
    }),
    http.get("*/api/v1/jobs/:id", ({ params }) => {
      const job = find(params.id);
      return job ? asJson(job) : problem(404, "No such job.");
    }),
    http.get("*/api/v1/jobs", () => HttpResponse.json(structuredClone([...jobs].reverse()))),
    http.get("*/api/v1/runs", () =>
      HttpResponse.json(
        jobs
          .filter((job) => job.run && job.progress.done > 0)
          .map((job): RunSummary => ({
            run_id: job.run?.run_id ?? "",
            set_name: `${job.set.name}/${job.set.version}`,
            set_md5: "cb9cb733168663d4b04e75cdf16e5030",
            arm: job.run?.arm.name ?? "",
            variant: null,
            model: job.run?.model ?? "",
            created: job.created_at,
            started: job.started_at,
            ended: job.state === "done" ? "2026-10-27T10:01:00Z" : null,
            repeats: job.run?.repeats ?? 1,
            answers: job.progress.done,
            errors: 0,
            expected: job.progress.total,
            complete: job.state === "done",
            notes: job.run?.notes ?? "",
            job_id: job.id,
            owner: job.owner,
            state: job.state,
          })),
      ),
    ),
  ];

  return {
    handlers,
    jobs,
    requests,
    start,
    log: (jobId) => (logs.get(jobId) ?? []).join("\n"),
    tick: (jobId) => {
      const job = jobId ? find(jobId) : running();
      if (!job || (job.state !== "running" && job.state !== "cancelling")) return [];
      job.progress.done += 1;
      note(job, `${job.progress.done}/${job.progress.total} answered`);
      if (job.state === "cancelling") {
        job.state = "cancelled";
        job.settled = true;
        note(job, `cancelled with ${job.progress.done} answers`);
      } else if (job.progress.done >= job.progress.total) {
        job.state = "done";
        job.settled = true;
        job.ended_at = "2026-10-27T10:01:00Z";
        note(job, "done");
      }
      startNext();
      renumber();
      return structuredClone(jobs);
    },
  };
}
