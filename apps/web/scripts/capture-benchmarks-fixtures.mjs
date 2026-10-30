// Records real answers of the benchmark endpoints as test fixtures.
// Usage: node scripts/capture-benchmarks-fixtures.mjs [http://127.0.0.1:8091]
// Start the API first, with no pause between answers:
//   uv run research-api serve --fake --port 8091 --bench-pause 0
// The unit tests read src/features/benchmarks/fixtures/captured.json and serve it from a stand-in
// of the API (src/test/benchmarks-api.ts); the end-to-end tests use the real API.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const api = process.argv[2] ?? "http://127.0.0.1:8091";
const out = fileURLToPath(
  new URL("../src/features/benchmarks/fixtures/captured.json", import.meta.url),
);

// A seeded test account of the API in fake mode (packages/research-api/tests/fixtures/seed-users.json).
const ACCOUNT = { username: "dani", password: "test-password-dani-03" };

const login = await fetch(`${api}/api/v1/auth/login`, {
  method: "POST",
  headers: { "content-type": "application/json", origin: api },
  body: JSON.stringify(ACCOUNT),
});
if (login.status !== 204) throw new Error(`sign-in answered ${login.status}`);
const cookies = login.headers.getSetCookie().map((line) => line.split(";")[0]);
const csrf = decodeURIComponent(
  cookies.find((c) => c.startsWith("re_csrf="))?.slice("re_csrf=".length) ?? "",
);
const headers = { cookie: cookies.join("; "), origin: api, "x-csrf-token": csrf };

async function call(method, path, body, as = "json") {
  const response = await fetch(`${api}/api/v1${path}`, {
    method,
    headers: { ...headers, ...(body ? { "content-type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path} answered ${response.status}: ${text}`);
  return as === "text" ? text : text ? JSON.parse(text) : null;
}

async function settled(jobIds) {
  for (let attempt = 0; attempt < 600; attempt += 1) {
    const jobs = await Promise.all(jobIds.map((id) => call("GET", `/jobs/${id}`)));
    if (jobs.every((job) => job.settled)) return jobs;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("the jobs did not settle");
}

async function pages(path, key) {
  const items = [];
  for (let offset = 0; ; offset += 200) {
    const separator = path.includes("?") ? "&" : "?";
    const page = await call("GET", `${path}${separator}offset=${offset}&limit=200`);
    items.push(...page[key]);
    if (page[key].length === 0 || offset + 200 >= page.total) return items;
  }
}

const SET = { name: "demo-papers", version: "v1" };
const status = await call("GET", "/status");
const models = await call("GET", "/models");
const sets = await call("GET", "/sets");

// Three arms in one request, then a second closed-book run: two runs of one arm give the noise floor.
const first = await call("POST", "/runs", {
  set: SET,
  arms: [{ name: "bare" }, { name: "harness" }, { name: "oracle" }],
  models: ["fake-model"],
  repeats: 2,
  notes: "captured for the tests",
});
const second = await call("POST", "/runs", {
  set: SET,
  arms: [{ name: "bare" }],
  models: ["fake-model"],
  repeats: 2,
  notes: "second pass",
});
const started = [...first, ...second];
const jobs = await settled(started.map((job) => job.id));
const runs = await call("GET", "/runs");
const logs = {};
for (const job of jobs) logs[job.id] = await call("GET", `/jobs/${job.id}/log`, undefined, "text");

const byArm = (name) => jobs.filter((job) => job.run.arm.name === name);
const bare = byArm("bare").map((job) => job.run.run_id);
const harness = byArm("harness")[0].run.run_id;
const oracle = byArm("oracle")[0].run.run_id;
const scoreJob = await call("POST", "/scores", {
  set: SET,
  runs: [bare[0], harness, oracle, bare[1]],
  pairs: [
    [bare[0], harness],
    [bare[0], oracle],
  ],
  noise: [bare[0], bare[1]],
});
const [scored] = await settled([scoreJob.id]);
const scoreId = scoreJob.id;
const score = await call("GET", `/scores/${scoreId}`);
const scores = await call("GET", "/scores");

const questions = await pages(`/sets/${SET.name}/versions/${SET.version}`, "items");
const details = {};
for (const question of questions) {
  details[question.id] = await call(
    "GET",
    `/sets/${SET.name}/versions/${SET.version}/items/${question.id}`,
  );
}
const scoreItems = {};
const runItems = {};
for (const run of runs) {
  scoreItems[run.run_id] = await pages(`/scores/${scoreId}/items?run_id=${run.run_id}`, "items");
  for (const question of questions) {
    runItems[`${run.run_id}|${question.id}`] = await call(
      "GET",
      `/runs/${run.run_id}/items?id=${question.id}&limit=50`,
    );
  }
}

const capture = {
  status,
  models,
  sets,
  jobs,
  logs,
  runs,
  scoreJob: scored,
  scoreId,
  score,
  scores,
  questions,
  details,
  scoreItems,
  runItems,
};
writeFileSync(out, `${JSON.stringify(capture, null, 2)}\n`);
console.log(
  `wrote ${out}: ${jobs.length} jobs, ${runs.length} runs, ${questions.length} questions`,
);
