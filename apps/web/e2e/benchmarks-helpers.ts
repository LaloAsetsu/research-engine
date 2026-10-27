import { expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * The reads of the Benchmarks screen answered with empty lists. For the tests that sign in with
 * the mock of the sign-in endpoints: the real API behind it would refuse these reads (the mock
 * makes no real session). The screens themselves are tested against the real API in
 * benchmarks.spec.ts.
 */
export async function mockBenchmarkReads(page: Page) {
  const empty = (route: { fulfill: (options: object) => Promise<void> }) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  for (const path of ["models", "sets", "jobs", "runs", "scores"]) {
    await page.route(new RegExp(`/api/v1/${path}(\\?.*)?$`), empty);
  }
}

export interface JobView {
  id: string;
  kind: string;
  state: string;
  settled: boolean;
  progress: { done: number; total: number };
  started_at: string | null;
  ended_at: string | null;
  position?: number | null;
  run: { run_id: string; arm: { name: string }; model: string; repeats: number } | null;
}

export const DEMO_SET = { name: "demo-papers", version: "v1" };

/** `POST /runs` through the API, as the form does. */
export async function startRuns(
  api: APIRequestContext,
  arms: string[],
  repeats = 1,
  notes = "",
): Promise<JobView[]> {
  const response = await api.post("/api/v1/runs", {
    data: {
      set: DEMO_SET,
      arms: arms.map((name) => ({ name })),
      models: ["fake-model"],
      repeats,
      notes,
    },
  });
  expect(response.status()).toBe(202);
  return (await response.json()) as JobView[];
}

/** A value the test cannot go on without: a missing one fails the test instead of ending it quietly. */
export function must<T>(value: T | null | undefined, what: string): T {
  expect(value, what).toBeTruthy();
  return value as T;
}

export async function jobOf(api: APIRequestContext, id: string): Promise<JobView> {
  const response = await api.get(`/api/v1/jobs/${id}`);
  expect(response.ok()).toBe(true);
  return (await response.json()) as JobView;
}

/** Waits until every job has settled. */
export async function waitSettled(api: APIRequestContext, ids: string[], timeout = 120_000) {
  await expect
    .poll(
      async () => (await Promise.all(ids.map((id) => jobOf(api, id)))).every((job) => job.settled),
      {
        timeout,
        intervals: [200, 500],
      },
    )
    .toBe(true);
}

/** Waits until the lane has nothing running or waiting, so a test starts from a quiet service. */
export async function waitIdle(api: APIRequestContext, timeout = 120_000) {
  await expect
    .poll(
      async () => {
        const response = await api.get("/api/v1/jobs?limit=200");
        const jobs = (await response.json()) as JobView[];
        return jobs.filter((job) => !job.settled && job.kind === "run").length;
      },
      { timeout, intervals: [200, 500] },
    )
    .toBe(0);
}

/** `1.5` as `+1.5`, `-0.0297` as `-0.0297`, `0` as `0`: up to four decimals, as the screen writes a difference. */
export function signed(value: number): string {
  const text = String(Number(Math.abs(value).toFixed(4)));
  if (Number(text) === 0) return "0";
  return `${value < 0 ? "-" : "+"}${text}`;
}

/** Up to four decimals with no trailing zeros, as the screen writes a mean. */
export function plain(value: number): string {
  return String(Number(value.toFixed(4)));
}

/** A read through the API, typed by the caller. */
export async function getJson<T>(api: APIRequestContext, path: string): Promise<T> {
  const response = await api.get(path);
  expect(response.ok(), path).toBe(true);
  return (await response.json()) as T;
}

export interface GroupRecord {
  run_id: string;
  group: string;
  metric: string;
  n: number;
  mean: number | null;
  ci?: [number, number] | null;
}

export interface CompareRecord {
  base_run: string;
  var_run: string;
  group: string;
  metric: string;
  n: number;
  base: number;
  var: number;
  diff: number;
  ci: [number, number];
  up: number;
  down: number;
  floor?: number | null;
  counts?: boolean | null;
}

export interface ScoreBody {
  groups: GroupRecord[];
  compares: CompareRecord[];
}

export interface RunItemBody {
  id: string;
  repeat: number;
  answer: string | null;
}

export interface RunItemsBody {
  total: number;
  items: RunItemBody[];
}

export interface SetBody {
  name: string;
  versions: { status: string; md5: string }[];
}
