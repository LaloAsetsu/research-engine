import type { Locator, Page } from "@playwright/test";
import {
  DEMO_SET,
  getJson,
  jobOf,
  plain,
  signed,
  startRuns,
  waitIdle,
  waitSettled,
  type CompareRecord,
  type JobView,
  type RunItemsBody,
  type ScoreBody,
  type SetBody,
} from "./benchmarks-helpers";
import { DANI, expect as baseExpect, expectNoAxeViolations, signIn, test } from "./fixtures";

// Acceptance checks B1 to B5 and B7 of the Benchmarks screens, against the real API in fake mode:
// the real jobs, the real lane (one run at a time, a quarter of a second per answer), the real
// scorer and the invented papers. Each test signs in as `dani` through the sign-in page. The
// tests of this file run one after the other, and each starts when the lane is quiet, so the
// order of the jobs is the one the test made.
const expect = baseExpect.configure({ timeout: 20_000 });

test.describe.configure({ mode: "default" });
// Asks from the other files go before the next question of a run, so a run can wait for a while.
test.setTimeout(180_000);

/** Signs in through the sign-in page and waits until the app has opened. */
async function signedIn(page: Page) {
  await page.goto("/login");
  await signIn(page, DANI);
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

async function openBenchmarks(page: Page) {
  await signedIn(page);
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Benchmarks" })
    .click();
  await expect(page.getByRole("heading", { level: 1, name: "Benchmarks" })).toBeVisible();
}

const startForm = (page: Page) => page.getByRole("region", { name: "Start a run" });
const status = (page: Page) => page.getByTestId("job-status");

test.beforeEach(async ({ api }) => {
  await waitIdle(api);
});

test.describe("B1: start a run", () => {
  test("lists only frozen sets with their md5 and only models that answer, and makes one job per arm and model", async ({
    page,
    api,
  }) => {
    await openBenchmarks(page);
    const form = startForm(page);

    // the sets: the demo has a frozen version, a draft of the same name and a draft of circuits
    const sets = form.getByLabel("Question set");
    await expect(sets.locator("option")).toHaveCount(1);
    await expect(sets.locator("option")).toContainText(
      /demo-papers v1 \(md5 [0-9a-f]{8}, 6 questions\)/,
    );
    const all = await getJson<SetBody[]>(api, "/api/v1/sets");
    const frozen = all.flatMap((entry) =>
      entry.versions.filter((version) => version.status === "frozen"),
    );
    expect(frozen).toHaveLength(1);
    await expect(sets.locator("option")).toContainText(frozen[0]?.md5.slice(0, 8) ?? "");

    // the models: one answers, one does not and cannot be chosen
    await expect(form.getByRole("checkbox", { name: "fake-model (demo)" })).toBeChecked();
    await expect(form.getByRole("checkbox", { name: "fake-offline (demo, down)" })).toBeDisabled();
    await expect(form.getByText("Not answering, so it cannot be chosen.")).toBeVisible();

    // on test data the oracle arm is available
    await expect(form.getByRole("checkbox", { name: "oracle" })).toBeEnabled();
    await form.getByRole("checkbox", { name: "oracle" }).check();
    await form.getByLabel("Notes").fill("B1 from the browser");
    await expect(form.getByText(/This creates 3 jobs, one for each arm and model/)).toBeVisible();
    await expectNoAxeViolations(page);

    // a double click on Start sends one request, so the API makes three jobs and not six
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/runs") {
        posts.push(request.url());
      }
    });
    await form.getByRole("button", { name: "Start" }).dblclick();
    await expect(form.getByText("Started 3 jobs")).toBeVisible();
    expect(posts).toHaveLength(1);
    const started = form.getByRole("list").getByRole("listitem");
    await expect(started).toHaveCount(3);
    await expect(started.nth(0)).toContainText("bare on fake-model");
    await expect(started.nth(1)).toContainText("harness on fake-model");
    await expect(started.nth(1)).toContainText("Queued");
    await expect(started.nth(2)).toContainText("oracle on fake-model");
    await expect(started.nth(2)).toContainText("2 jobs ahead");

    // the jobs table shows them, and the API made exactly one job for each arm
    const jobs = await getJson<JobView[]>(api, "/api/v1/jobs?mine=true");
    const mine = jobs.filter((job) => job.run && job.progress.total === 6);
    expect(mine.map((job) => job.run?.arm.name).sort()).toEqual(["bare", "harness", "oracle"]);
    const table = page.getByRole("table", { name: "Benchmark jobs, newest first" });
    await expect(table.getByRole("row")).not.toHaveCount(1);
    await waitSettled(
      api,
      mine.map((job) => job.id),
    );
    await expectNoAxeViolations(page);
  });

  test("shows the start form in Spanish with the terms of the formats unchanged", async ({
    page,
  }) => {
    await openBenchmarks(page);
    await page.getByRole("button", { name: "Language: English" }).click();
    await page.getByRole("menuitemradio", { name: /Español/ }).click();
    const form = page.getByRole("region", { name: "Lanzar una corrida" });
    await expect(form.getByLabel("Conjunto de preguntas")).toBeVisible();
    await expect(form.getByRole("checkbox", { name: "harness" })).toBeVisible();
    await expect(form.getByRole("checkbox", { name: "oracle" })).toBeVisible();
    await expect(form.getByText(/No responde, así que no se puede elegir/)).toBeVisible();
    await expectNoAxeViolations(page);
  });
});

test.describe("B7: two runs never share the GPU", () => {
  test("the second run stays queued until the first one ends", async ({ page, api }) => {
    await openBenchmarks(page);
    const [first, second] = await startRuns(api, ["bare", "harness"], 3, "B7");
    expect(first && second).toBeTruthy();
    if (!first || !second) return;
    // watch the lane while they run: never two running, and the second queued while the first runs
    const seen: string[] = [];
    await expect
      .poll(
        async () => {
          const [a, b] = await Promise.all([jobOf(api, first.id), jobOf(api, second.id)]);
          expect(a.state === "running" && b.state === "running").toBe(false);
          if (a.state === "running") expect(b.state).toBe("queued");
          seen.push(`${a.state}/${b.state}`);
          return a.settled && b.settled;
        },
        { timeout: 120_000, intervals: [100] },
      )
      .toBe(true);
    expect(seen).toContain("running/queued");
    const [a, b] = await Promise.all([jobOf(api, first.id), jobOf(api, second.id)]);
    expect(Date.parse(b.started_at ?? "")).toBeGreaterThanOrEqual(Date.parse(a.ended_at ?? ""));

    // and the screen said the same while it happened: the page of the second job
    const [third, fourth] = await startRuns(api, ["bare", "harness"], 3, "B7 page");
    if (!third || !fourth) return;
    await page.goto(`/benchmarks/jobs/${fourth.id}`);
    await expect(status(page)).toContainText("Queued. 1 job ahead");
    await expect(status(page)).toContainText("Running");
    await waitSettled(api, [third.id, fourth.id]);
  });
});

test.describe("B2: follow the progress", () => {
  test("shows the answers, the time and the log without a reload", async ({ page, api }) => {
    await openBenchmarks(page);
    const [job] = await startRuns(api, ["harness"], 4, "B2");
    if (!job) return;
    await page.goto(`/benchmarks/jobs/${job.id}`);
    await page.evaluate(() => {
      (window as unknown as { __kept: boolean }).__kept = true;
    });
    await expect(status(page)).toContainText(/(Queued|Running)/);
    const bar = page.getByRole("progressbar", { name: "Answers done" });
    await expect(bar).toHaveAttribute("aria-valuemax", "24");
    // the number of answers grows by itself
    await expect(status(page)).toContainText(/Running\. [1-9]\d* of 24 answers/);
    const log = page.getByRole("log", { name: "Job log" });
    await expect(log).toContainText("started: 0 of 24 answers");
    await expect(log).toContainText("answered in");
    await expect(page.getByText("Time taken").locator("xpath=following-sibling::dd")).toContainText(
      /\d+ s|\d+ min/,
    );
    await expectNoAxeViolations(page);
    await expect(status(page)).toContainText("Done. 24 of 24 answers");
    await expect(log).toContainText("done: 24 answers");
    // the page was never reloaded
    expect(await page.evaluate(() => (window as unknown as { __kept?: boolean }).__kept)).toBe(
      true,
    );
    await expect(page.getByRole("progressbar", { name: "Answers done" })).toHaveAttribute(
      "aria-valuenow",
      "24",
    );
    await expect(page.getByRole("link", { name: "See results" })).toBeVisible();
  });
});

test.describe("B3: cancel and resume", () => {
  test("asks first, stops after the question in flight, keeps the partial run and resumes it to the end", async ({
    page,
    api,
  }) => {
    await openBenchmarks(page);
    const [job] = await startRuns(api, ["bare"], 10, "B3");
    if (!job?.run) return;
    const runId = job.run.run_id;
    await page.goto(`/benchmarks/jobs/${job.id}`);
    await expect(status(page)).toContainText(
      /Running\. [3-9]\d* of 60 answers|Running\. [1-9]\d+ of 60/,
    );

    // "keep it" changes nothing
    await page.getByRole("button", { name: "Cancel job" }).click();
    const dialog = page.getByRole("dialog", { name: "Cancel this job?" });
    await expect(dialog).toContainText("It stops before the next question.");
    await expectNoAxeViolations(page);
    await dialog.getByRole("button", { name: "Keep it" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Cancel job" })).toBeFocused();
    await expect(status(page)).toContainText("Running");

    // cancel for real
    await page.getByRole("button", { name: "Cancel job" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Yes, cancel" }).click();
    await expect(status(page)).toContainText(/Cancel(ling|led)\./);
    await expect(status(page)).toContainText("Cancelled.");
    const stopped = await jobOf(api, job.id);
    expect(stopped.state).toBe("cancelled");
    expect(stopped.progress.done).toBeGreaterThan(0);
    expect(stopped.progress.done).toBeLessThan(60);
    await expect(status(page)).toContainText(`${stopped.progress.done} of 60 answers`);

    // the partial run is listed with its count and marked
    await page.getByRole("link", { name: "Back to the benchmarks" }).click();
    const row = page.locator(`tr[data-run="${runId}"]`);
    await expect(row).toContainText("partial");
    await expect(row).toContainText(`${stopped.progress.done} of 60`);
    await expect(row).toContainText("Cancelled");
    await expectNoAxeViolations(page);

    // resume from the job page: it ends with every question and repeat once
    await row.getByRole("link", { name: runId }).click();
    // a double click resumes once: the second click does not meet a refusal
    await page.getByRole("button", { name: "Resume" }).dblclick();
    await expect(status(page)).toContainText("Done. 60 of 60 answers", { timeout: 45_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    const items = await getJson<RunItemsBody>(
      api,
      `/api/v1/runs/${runId}/items?limit=200&contexts=false`,
    );
    expect(items.total).toBe(60);
    const pairs = new Set(items.items.map((item) => `${item.id}|${item.repeat}`));
    expect(pairs.size).toBe(60);
    expect(new Set(items.items.map((item) => item.repeat))).toEqual(
      new Set([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
    );
  });

  test("cancels a queued job at once", async ({ page, api }) => {
    await openBenchmarks(page);
    const [first, second] = await startRuns(api, ["bare", "harness"], 2, "B3 queued");
    if (!first || !second) return;
    await page.goto(`/benchmarks/jobs/${second.id}`);
    await expect(status(page)).toContainText("Queued");
    await page.getByRole("button", { name: "Cancel job" }).click();
    const dialog = page.getByRole("dialog", { name: "Cancel this job?" });
    await expect(dialog).toContainText("It leaves the line and nothing is run.");
    await dialog.getByRole("button", { name: "Yes, cancel" }).click();
    await expect(status(page)).toContainText("Cancelled. 0 of 12 answers");
    await waitSettled(api, [first.id]);
  });
});

test.describe("B3 and B4: a run resumed after it was scored", () => {
  test("does not open the score of the partial run for the results of the whole one", async ({
    page,
    api,
  }) => {
    await openBenchmarks(page);
    const [job] = await startRuns(api, ["bare"], 4, "B3 stale score");
    if (!job?.run) return;
    const runId = job.run.run_id;
    await expect
      .poll(async () => (await jobOf(api, job.id)).progress.done, { intervals: [100] })
      .toBeGreaterThan(0);
    expect((await api.post(`/api/v1/jobs/${job.id}/cancel`)).ok()).toBe(true);
    await waitSettled(api, [job.id]);
    // it stopped in the first question or so: fewer questions than the whole run answers
    expect((await jobOf(api, job.id)).progress.done).toBeLessThan(4);

    // score what the run has so far
    await page.goto(`/benchmarks/compare?runs=${runId}`);
    await page.getByRole("button", { name: "Score the run" }).click();
    await expect(
      page.getByRole("table", { name: /Mean and 95% interval of each metric/ }),
    ).toBeVisible();
    const partial = new URL(page.url()).searchParams.get("score") ?? "";
    expect(partial).toMatch(/^[0-9a-z]{26}$/);
    const before = await getJson<ScoreBody>(api, `/api/v1/scores/${partial}`);

    // resume it to its end, and ask for the results
    await page.goto(`/benchmarks/jobs/${job.id}`);
    await page.getByRole("button", { name: "Resume" }).click();
    await expect(status(page)).toContainText(/Queued|Running/);

    // while it is being resumed the partial score is not offered as the results
    await page.goto(`/benchmarks/compare?runs=${runId}`);
    await expect(page.getByText(/is still going/)).toBeVisible();
    expect(new URL(page.url()).searchParams.get("score")).toBeNull();
    // and the score's own address says it is older
    await page.goto(`/benchmarks/compare?runs=${runId}&score=${partial}`);
    await expect(page.getByRole("status").filter({ hasText: "This score is older" })).toBeVisible();

    await page.goto(`/benchmarks/jobs/${job.id}`);
    await expect(status(page)).toContainText("Done. 24 of 24 answers", { timeout: 90_000 });
    await page.getByRole("link", { name: "See results" }).click();
    const starter = page.getByRole("region", { name: "Score these runs" });
    await expect(starter).toContainText("was made before a run in it last changed");
    expect(new URL(page.url()).searchParams.get("score")).toBeNull();
    await expectNoAxeViolations(page);

    // the older score is still there to open, and says that it is older
    await starter.getByRole("link", { name: "Open it anyway" }).click();
    await expect(page.getByRole("status").filter({ hasText: "This score is older" })).toBeVisible();
    await page.getByRole("link", { name: "Score the runs again" }).click();

    // a new score holds all the questions of the whole run
    await page.getByRole("button", { name: "Score the run" }).click();
    await expect(
      page.getByRole("table", { name: /Mean and 95% interval of each metric/ }),
    ).toBeVisible();
    const whole = new URL(page.url()).searchParams.get("score") ?? "";
    expect(whole).toMatch(/^[0-9a-z]{26}$/);
    expect(whole).not.toBe(partial);
    const after = await getJson<ScoreBody>(api, `/api/v1/scores/${whole}`);
    const questionsOf = (score: ScoreBody) =>
      score.groups.find(
        (record) =>
          record.run_id === runId && record.group === "all" && record.metric === "all_keys",
      )?.n ?? 0;
    expect(questionsOf(after)).toBeGreaterThan(questionsOf(before));
    // and a visit to the results finds that score from now on
    await page.goto(`/benchmarks/compare?runs=${runId}`);
    await expect(page.getByRole("table", { name: /Mean and 95% interval/ })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("score")).toBe(whole);
  });
});

/** The finished runs the results tests read: a closed-book run and a harness run. */
async function twoFinishedRuns(api: Parameters<typeof startRuns>[0], repeats = 1) {
  const jobs = await startRuns(api, ["bare", "harness"], repeats, "B4 B5");
  await waitSettled(
    api,
    jobs.map((job) => job.id),
  );
  const runs = jobs.map((job) => job.run?.run_id ?? "");
  return { bare: runs[0] ?? "", harness: runs[1] ?? "" };
}

function row(table: Locator, group: string, metric: string): Locator {
  return table
    .getByRole("row")
    .filter({ has: table.page().getByRole("rowheader", { name: group, exact: true }) })
    .filter({ has: table.page().getByRole("cell", { name: metric, exact: true }) });
}

test.describe("B4 and B5: results and comparison", () => {
  test("scores two runs from the screen and shows the numbers exactly as the API returns them", async ({
    page,
    api,
  }) => {
    const { bare, harness } = await twoFinishedRuns(api);
    // a second closed-book run on the same set: with the first one it is the noise pair
    const [again] = await startRuns(api, ["bare"], 1, "B5 noise");
    await waitSettled(api, [again?.id ?? ""]);
    const noise = again?.run?.run_id ?? "";
    await openBenchmarks(page);

    // pick the two runs: the first is the base
    await page.getByRole("checkbox", { name: `Pick run ${bare}` }).check();
    await page.getByRole("checkbox", { name: `Pick run ${harness}` }).check();
    await expect(page.getByText(`Base: ${bare}. Variant: ${harness}.`)).toBeVisible();
    await page.getByRole("link", { name: "Compare" }).click();

    // these runs have not been scored: score them with the two closed-book runs as the noise pair
    const starter = page.getByRole("region", { name: "Score these runs" });
    await expect(starter).toBeVisible();
    await starter.getByLabel("First run").selectOption(bare);
    await starter.getByLabel("Second run").selectOption(noise);
    await expectNoAxeViolations(page);
    // a double click scores once: one score holds this pair
    const scorings: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/scores") {
        scorings.push(request.url());
      }
    });
    await starter.getByRole("button", { name: "Score and compare" }).dblclick();
    const summary = page.getByRole("table", { name: /Mean and 95% interval of each metric/ });
    await expect(summary).toBeVisible();
    const scoreId = new URL(page.url()).searchParams.get("score") ?? "";
    expect(scoreId).toMatch(/^[0-9a-z]{26}$/);
    const score = await getJson<ScoreBody>(api, `/api/v1/scores/${scoreId}`);

    // B4: the mean and the interval of a group, for each run
    const mean = (runId: string, group: string, metric: string) => {
      const found = score.groups.find(
        (record) => record.run_id === runId && record.group === group && record.metric === metric,
      );
      if (!found?.mean && found?.mean !== 0)
        throw new Error(`no mean for ${runId} ${group} ${metric}`);
      return found;
    };
    for (const group of ["all", "lang=es", "content=math"]) {
      const cells = row(summary, group, "all_keys").getByRole("cell");
      for (const [index, runId] of [bare, harness].entries()) {
        const record = mean(runId, group, "all_keys");
        const text = (await cells.nth(index + 1).textContent()) ?? "";
        expect(text).toContain(plain(record.mean ?? 0));
        if (record.ci) expect(text).toContain(`[${plain(record.ci[0])}, ${plain(record.ci[1])}]`);
        else expect(text).toContain("no interval");
        expect(text).toContain(`n ${record.n}`);
      }
    }

    // B5: the paired bootstrap, number by number
    const compare = page.getByTestId("compare-table");
    const recorded: CompareRecord[] = score.compares.filter(
      (record) => record.base_run === bare && record.var_run === harness,
    );
    expect(recorded.length).toBeGreaterThan(10);
    for (const [group, metric] of [
      ["all", "all_keys"],
      ["all", "key_share"],
      ["lang=es", "all_keys"],
      ["form=single", "all_keys"],
    ] as const) {
      const record = recorded.find((entry) => entry.group === group && entry.metric === metric);
      if (!record) throw new Error(`no ${group} ${metric}`);
      const cells = await row(compare, group, metric).getByRole("cell").allTextContents();
      expect(record.floor ?? null).not.toBeNull();
      expect(cells.slice(0, 8)).toEqual([
        metric,
        String(record.n),
        plain(record.base),
        plain(record.var),
        signed(record.diff),
        `[${signed(record.ci[0])}, ${signed(record.ci[1])}]`,
        String(record.up),
        String(record.down),
      ]);
      // the noise floor and whether the difference counts, as the scorer returned them
      expect(cells[8]).toBe(plain(record.floor ?? 0));
      expect(cells[9]).toMatch(record.counts ? /^Yes/ : /^No$/);
    }
    expect(scorings).toHaveLength(1);
    await expectNoAxeViolations(page);

    // the filter lands on the questions where the arms disagree: the same count as the scorer's
    const all = recorded.find((entry) => entry.group === "all" && entry.metric === "all_keys");
    await page.getByLabel("Show").selectOption("The arms disagree");
    const disagree = await page.getByTestId("question-count").textContent();
    expect(Number(/^(\d+) of/.exec(disagree ?? "")?.[1])).toBe((all?.up ?? 0) + (all?.down ?? 0));
    await page.getByLabel("Show").selectOption("The variant is better");
    expect(
      Number(/^(\d+) of/.exec((await page.getByTestId("question-count").textContent()) ?? "")?.[1]),
    ).toBe(all?.up);
    await page.getByLabel("Show").selectOption("All questions");
    await expect(page.getByTestId("question-count")).toContainText("6 of 6 questions");
  });

  test("opens a question with both answers, the keys hit and missed, the gold papers, the contexts and the timings", async ({
    page,
    api,
  }) => {
    const { bare, harness } = await twoFinishedRuns(api, 2);
    await signedIn(page);
    await page.goto(`/benchmarks/compare?runs=${bare},${harness}`);
    await page.getByRole("button", { name: "Score and compare" }).click();
    await expect(page.getByTestId("questions-table")).toBeVisible();

    await page.getByRole("button", { name: "Open question DP-002 side by side" }).click();
    const section = page.getByRole("region", { name: "Question DP-002 side by side" });
    await expect(section.getByRole("heading", { level: 2 })).toBeFocused();
    const left = section.locator(`[data-run="${bare}"]`);
    const right = section.locator(`[data-run="${harness}"]`);
    await expect(left).toContainText("bare, fake-model");
    await expect(right).toContainText("harness, fake-model");

    // the answers are the ones the runs hold
    const answerOf = async (runId: string, repeat: number) => {
      const body = await getJson<RunItemsBody>(
        api,
        `/api/v1/runs/${runId}/items?id=DP-002&limit=50`,
      );
      return body.items.find((item) => item.repeat === repeat)?.answer?.trim() ?? "";
    };
    const bareAnswer = await answerOf(bare, 1);
    const harnessAnswer = await answerOf(harness, 1);
    expect(bareAnswer).not.toBe("");
    await expect(left.getByText(bareAnswer, { exact: false }).first()).toBeVisible();
    await expect(right.getByText(harnessAnswer, { exact: false }).first()).toBeVisible();

    // keys: every key fact is found, missed or found only by the lenient rule
    await expect(left.getByTestId("keys-summary")).toContainText(/\d of 2 keys found, \d missing/);
    await expect(right.getByTestId("keys-summary")).toContainText(/\d of 2 keys found, \d missing/);
    expect(await left.locator("[data-key-status]").count()).toBe(2);
    // gold papers: the harness cites some, the closed book none
    await expect(left.getByTestId("gold-summary")).toContainText("0 of 2 gold papers cited");
    await expect(right.getByTestId("gold-summary")).toContainText(/[12] of 2 gold papers cited/);
    // what the model read, and how long it took
    await expect(left.getByText("What the model read (0)")).toBeVisible();
    await expect(right.getByText(/What the model read \([1-9]\)/)).toBeVisible();
    await right.getByText(/What the model read/).click();
    await expect(right.locator("details li").first()).toBeVisible();
    await expect(right.getByText("Total time")).toBeVisible();
    await expect(right.getByText("Search")).toBeVisible();
    await expectNoAxeViolations(page);

    // the repeat is the same for both columns
    await section.getByLabel("Repeat").selectOption("2");
    await expect(left.getByTestId("keys-summary")).toBeVisible();
    // close: focus returns to the question
    await section.getByRole("button", { name: "Close" }).click();
    await expect(
      page.getByRole("button", { name: "Open question DP-002 side by side" }),
    ).toBeFocused();
  });

  test("opens the results of a single run", async ({ page, api }) => {
    const { harness } = await twoFinishedRuns(api);
    await signedIn(page);
    await page.goto(`/benchmarks/compare?runs=${harness}`);
    await page.getByRole("button", { name: "Score the run" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Results of a run" })).toBeVisible();
    await expect(
      page.getByRole("table", { name: /Mean and 95% interval of each metric/ }),
    ).toBeVisible();
    await expect(page.getByTestId("compare-table")).toHaveCount(0);
    await expect(page.getByLabel("Show")).toHaveCount(0);
    await expectNoAxeViolations(page);
  });

  test.describe("in the dark theme", () => {
    test.use({ colorScheme: "dark" });

    test("the results and the side by side have no accessibility violations", async ({
      page,
      api,
    }) => {
      const { bare, harness } = await twoFinishedRuns(api);
      await signedIn(page);
      await page.goto(`/benchmarks/compare?runs=${bare},${harness}`);
      await page.getByRole("button", { name: "Score and compare" }).click();
      await expect(page.getByTestId("compare-table")).toBeVisible();
      await page.getByRole("button", { name: "Open question DP-002 side by side" }).click();
      await expect(
        page.getByRole("region", { name: "Question DP-002 side by side" }),
      ).toBeVisible();
      await expect(page.locator("html")).toHaveClass(/dark/);
      await expectNoAxeViolations(page);
    });
  });
});

test.describe("the screens keep out of what is not theirs", () => {
  test("a job that does not exist and an address that is not valid say so", async ({ page }) => {
    await signedIn(page);
    await page.goto("/benchmarks/jobs/01m5yz00000000000000000000");
    await expect(page.getByRole("alert")).toContainText("No such job");
    await page.goto("/benchmarks/compare?runs=a,b,c");
    await expect(page.getByRole("alert")).toContainText("A comparison takes one or two runs.");
    await expectNoAxeViolations(page);
  });

  test("the set of the demo is the one the runs name", async ({ api }) => {
    const sets = await getJson<SetBody[]>(api, "/api/v1/sets");
    expect(sets.map((entry) => entry.name)).toContain(DEMO_SET.name);
  });
});
