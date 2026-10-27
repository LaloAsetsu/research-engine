import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { CAPTURE, problem, RUNS } from "@/test/benchmarks-api";
import { renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";
import { polling } from "./api";
import { formatInterval, formatNumber, formatSigned, questionCells } from "./domain";
import { useCompareStore } from "./store";
import type { Job, ScoreCompare } from "./types";

type User = ReturnType<typeof userEvent.setup>;

const originalPolling = { ...polling };
const SCORE = CAPTURE.scoreId;

beforeEach(() => {
  polling.jobMs = 20;
  polling.idleMs = 40;
  useCompareStore.getState().reset();
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

afterEach(() => {
  Object.assign(polling, originalPolling);
});

function address(runs: string[], score: string | null = SCORE): string {
  const query = new URLSearchParams({ runs: runs.join(",") });
  if (score) query.set("score", score);
  return `/benchmarks/compare?${query.toString()}`;
}

async function open(path: string, heading = "Comparison of two runs") {
  const user = userEvent.setup({ delay: null });
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: heading });
  return { user, app };
}

/** Opens the recorded comparison and waits until the three tables are on the page, with the names of the runs. */
async function openPair() {
  const opened = await open(address([RUNS.base, RUNS.harness]));
  await screen.findByRole("table", { name: /Variant \(harness, fake-model\) minus base/ });
  await screen.findByRole("table", { name: /Questions with the mean of/ });
  return opened;
}

const summary = () => screen.getByRole("table", { name: /Mean and 95% interval of each metric/ });
const compare = () => screen.getByTestId("compare-table");
const questions = () => screen.getByRole("table", { name: /Questions with the mean of/ });

/** The row of a table for a group and a metric. */
function rowOf(table: HTMLElement, group: string, metric: string): HTMLElement {
  const row = within(table)
    .getAllByRole("row")
    .find(
      (candidate) =>
        within(candidate).queryByRole("rowheader", { name: group }) &&
        within(candidate).queryByRole("cell", { name: metric }),
    );
  if (!row) throw new Error(`no row for ${group} ${metric}`);
  return row;
}

function recorded(group: string, metric: string, pair = RUNS.harness): ScoreCompare {
  const found = CAPTURE.score.compares.find(
    (row) =>
      row.base_run === RUNS.base &&
      row.var_run === pair &&
      row.group === group &&
      row.metric === metric,
  );
  if (!found) throw new Error(`no recorded comparison for ${group} ${metric}`);
  return found;
}

describe("the summary by group", () => {
  it("shows the mean, the interval and n of each run as the scorer wrote them", async () => {
    await openPair();
    const table = summary();
    for (const header of ["Group", "Metric", "bare, fake-model", "harness, fake-model"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    const all = rowOf(table, "all", "all_keys");
    const cells = within(all).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("0.375 [0, 0.75] n 4");
    expect(cells[2]).toHaveTextContent("0.75 [0.25, 1] n 4");
    // a group of one question has a mean and no interval
    expect(within(rowOf(table, "lang=es", "all_keys")).getAllByRole("cell")[1]).toHaveTextContent(
      "0 (no interval) n 1",
    );
  });

  it("starts with the usual metrics and lets the person add and remove metrics and groups", async () => {
    const { user } = await openPair();
    const table = summary();
    expect(within(table).getAllByRole("cell", { name: "all_keys" }).length).toBeGreaterThan(0);
    expect(within(table).getAllByRole("cell", { name: "key_share" }).length).toBeGreaterThan(0);
    expect(within(table).queryByRole("cell", { name: "wall_s" })).not.toBeInTheDocument();

    await user.click(screen.getByText(/Metrics \(3 of /));
    await user.click(screen.getByRole("checkbox", { name: "wall_s" }));
    expect(within(summary()).getAllByRole("cell", { name: "wall_s" }).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("checkbox", { name: "key_share" }));
    expect(within(summary()).queryByRole("cell", { name: "key_share" })).not.toBeInTheDocument();

    await user.click(screen.getByText(/Groups \(all /));
    for (const group of ["all", "lang=en"]) {
      await user.click(screen.getByRole("checkbox", { name: group }));
    }
    const rows = within(summary()).getAllByRole("row").slice(1);
    const shown = new Set(rows.map((row) => within(row).getByRole("rowheader").textContent));
    expect(shown.has("all")).toBe(false);
    expect(shown.has("lang=en")).toBe(false);
    expect(shown.has("lang=es")).toBe(true);
    expect(screen.getByText(/Groups \(\d+ of /)).toBeInTheDocument();
  });

  it("shows only a run on a screen for one run", async () => {
    await open(address([RUNS.harness]), "Results of a run");
    await screen.findByRole("table", { name: /Questions with the mean of/ });
    expect(
      await within(summary()).findByRole("columnheader", { name: "harness, fake-model" }),
    ).toBeInTheDocument();
    expect(
      within(summary()).queryByRole("columnheader", { name: "bare, fake-model" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /minus base/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Show")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the benchmarks" })).toBeInTheDocument();
  });
});

describe("the paired comparison", () => {
  it("shows the difference, its interval, the questions up and down, and the noise floor as the API returns them", async () => {
    await openPair();
    const table = compare();
    for (const header of [
      "Group",
      "Metric",
      "n",
      "Base",
      "Variant",
      "Difference",
      "95% interval",
      "Up",
      "Down",
      "Noise floor",
      "Counts",
    ]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    const all = recorded("all", "all_keys");
    const cells = within(rowOf(table, "all", "all_keys")).getAllByRole("cell");
    expect(cells.map((cell) => cell.textContent)).toEqual([
      "all_keys",
      "4",
      formatNumber(all.base, "en"),
      formatNumber(all.var, "en"),
      formatSigned(all.diff, "en"),
      formatInterval(all.ci, "en", true),
      String(all.up),
      String(all.down),
      formatNumber(all.floor ?? 0, "en"),
      "No",
    ]);
    // the numbers of the recorded score, not a rounding of them
    expect(cells[4]).toHaveTextContent("+0.375");
    expect(cells[5]).toHaveTextContent("[0, +0.75]");
    expect(cells[6]).toHaveTextContent("2");
    expect(cells[7]).toHaveTextContent("0");
  });

  it("says in words whether a difference counts", async () => {
    await openPair();
    const es = recorded("lang=es", "all_keys");
    expect(es.counts).toBe(true);
    expect(
      within(rowOf(compare(), "lang=es", "all_keys"))
        .getAllByRole("cell")
        .at(-1),
    ).toHaveTextContent("Yes: the interval leaves 0 and the difference is above the floor");
  });

  it("takes the pair in the order of the address and says when the score did not compare it", async () => {
    await open(address([RUNS.harness, RUNS.base]));
    expect(
      await screen.findByText("This score did not compare these two runs in this order."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /minus base/ })).not.toBeInTheDocument();
  });

  it("compares the second recorded pair too", async () => {
    await open(address([RUNS.base, RUNS.oracle]));
    const table = await screen.findByRole("table", {
      name: /Variant \(oracle, fake-model\) minus base/,
    });
    const row = recorded("all", "all_keys", RUNS.oracle);
    expect(within(rowOf(table, "all", "all_keys")).getAllByRole("cell")[4]).toHaveTextContent(
      formatSigned(row.diff, "en"),
    );
  });

  it("writes the numbers in the language of the screen", async () => {
    server.use(
      http.get("*/api/v1/auth/me", () =>
        HttpResponse.json({
          username: "ana",
          display_name: "Ana",
          role: "admin",
          must_change_password: false,
          language: "es",
        }),
      ),
    );
    const { user } = await openPair();
    expect(within(rowOf(compare(), "all", "all_keys")).getAllByRole("cell")[4]).toHaveTextContent(
      "+0.375",
    );
    await user.click(screen.getByRole("button", { name: "Language: English" }));
    await user.click(await screen.findByRole("menuitemradio", { name: /Español/ }));
    await waitFor(() =>
      expect(within(rowOf(compare(), "all", "all_keys")).getAllByRole("cell")[4]).toHaveTextContent(
        "+0,375",
      ),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Comparación de dos corridas" }),
    ).toBeInTheDocument();
    expect(within(compare()).getByRole("columnheader", { name: "Diferencia" })).toBeInTheDocument();
  });
});

const questionRow = (id: string): HTMLElement => {
  const row = questions().querySelector<HTMLElement>(`[data-question="${id}"]`);
  if (!row) throw new Error(`no row for ${id}`);
  return row;
};
const column = (runId: string): HTMLElement => {
  const element = document.querySelector<HTMLElement>(`[data-run="${runId}"]`);
  if (!element) throw new Error(`no column for ${runId}`);
  return element;
};
const openQuestion = (user: User, id: string) =>
  user.click(screen.getByRole("button", { name: `Open question ${id} side by side` }));

describe("the questions", () => {
  it("lists every question of the set with the metric and the keys found by each run", async () => {
    await openPair();
    const table = questions();
    expect(within(table).getAllByRole("row")).toHaveLength(1 + CAPTURE.questions.length);
    expect(screen.getByTestId("question-count")).toHaveTextContent("6 of 6 questions");
    for (const header of ["Question", "bare, fake-model", "harness, fake-model", "Change"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    const cells = (id: string) =>
      within(questionRow(id))
        .getAllByRole("cell")
        .map((cell) => cell.textContent);
    // the metric is the mean over the two repeats, and the keys found are averaged the same way
    expect(cells("DP-001")).toEqual([
      "11 of 1 keys found, 0 missing",
      "11 of 1 keys found, 0 missing",
      "0",
    ]);
    expect(cells("DP-001-es")).toEqual([
      "00 of 1 keys found, 1 missing",
      "11 of 1 keys found, 0 missing",
      "+1",
    ]);
    expect(cells("DP-002")[0]).toBe("0.51.5 of 2 keys found, 0.5 missing");
    expect(cells("DP-002")[2]).toBe("+0.5");
    // a question with no key facts has no value
    expect(cells("DP-003")).toEqual(["-no key facts", "-no key facts", "-"]);
  });

  it("finds the questions where the arms disagree, are better or worse", async () => {
    const { user } = await openPair();
    const filter = screen.getByLabelText("Show");
    expect(
      within(filter)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "All questions",
      "The arms disagree",
      "The variant is better",
      "The variant is worse",
    ]);
    await user.selectOptions(filter, "The arms disagree");
    expect(screen.getByTestId("question-count")).toHaveTextContent("2 of 6 questions");
    expect(
      Array.from(questions().querySelectorAll("[data-question]")).map((row) =>
        row.getAttribute("data-question"),
      ),
    ).toEqual(["DP-001-es", "DP-002"]);
    await user.selectOptions(filter, "The variant is better");
    expect(screen.getByTestId("question-count")).toHaveTextContent("2 of 6 questions");
    await user.selectOptions(filter, "The variant is worse");
    expect(screen.getByTestId("question-count")).toHaveTextContent("0 of 6 questions");
    expect(screen.getByText("No question matches.")).toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: /Questions with the mean of/ }),
    ).not.toBeInTheDocument();
  });

  it("agrees with the counts of the paired comparison", async () => {
    const { user } = await openPair();
    const all = recorded("all", "all_keys");
    await user.selectOptions(screen.getByLabelText("Show"), "The variant is better");
    const better = Number(
      /^(\d+) of/.exec(screen.getByTestId("question-count").textContent ?? "")?.[1],
    );
    expect(better).toBe(all.up);
    await user.selectOptions(screen.getByLabelText("Show"), "The variant is worse");
    expect(screen.getByTestId("question-count")).toHaveTextContent(`${all.down} of`);
  });

  it("filters by the metric that is chosen for the question table", async () => {
    const { user } = await openPair();
    await user.selectOptions(screen.getByLabelText("Metric of the question table"), "key_share");
    expect(
      screen.getByRole("table", { name: /Questions with the mean of key_share/ }),
    ).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Show"), "The arms disagree");
    // DP-002 differs in key_share too (0.75 against 1)
    expect(questionRow("DP-002")).toBeInTheDocument();
    const share = questionCells(
      [...(CAPTURE.scoreItems[RUNS.base] ?? []), ...(CAPTURE.scoreItems[RUNS.harness] ?? [])],
      "key_share",
    );
    expect(share.get("DP-002")?.[RUNS.base]?.value).toBe(0.75);
  });

  it("finds a question by its id or its words", async () => {
    const { user } = await openPair();
    await user.type(screen.getByLabelText("Find a question"), "dp-004");
    expect(screen.getByTestId("question-count")).toHaveTextContent("1 of 6 questions");
    await user.clear(screen.getByLabelText("Find a question"));
    await user.type(screen.getByLabelText("Find a question"), "Alpha");
    expect(
      Number(/^(\d+)/.exec(screen.getByTestId("question-count").textContent ?? "")?.[1]),
    ).toBeGreaterThan(0);
    await user.clear(screen.getByLabelText("Find a question"));
    await user.type(screen.getByLabelText("Find a question"), "zzz-nothing");
    expect(screen.getByText("No question matches.")).toBeInTheDocument();
  });
});

describe("one question side by side", () => {
  it("shows both answers, the keys hit and missed, the gold papers cited, what was read and the timings", async () => {
    const { user } = await openPair();
    await openQuestion(user, "DP-002");
    const section = await screen.findByRole("region", { name: "Question DP-002 side by side" });
    await waitFor(() => expect(section.querySelector("h2")).toHaveFocus());
    expect(
      await within(section).findByText(/What does the synthetic paper Beta change/),
    ).toBeInTheDocument();
    expect(within(section).getByText(/Beta replaces the scalar coupling J/)).toBeInTheDocument();

    const bare = column(RUNS.base);
    const harness = column(RUNS.harness);
    expect(
      within(bare).getByRole("heading", { level: 3, name: "bare, fake-model" }),
    ).toBeInTheDocument();
    expect(
      within(harness).getByRole("heading", { level: 3, name: "harness, fake-model" }),
    ).toBeInTheDocument();
    // repeat 1: the closed-book run found the first key and missed the second
    await waitFor(() =>
      expect(within(bare).getByTestId("keys-summary")).toHaveTextContent(
        "1 of 2 keys found, 1 missing",
      ),
    );
    const statuses = (element: HTMLElement) =>
      Array.from(element.querySelectorAll("[data-key-status]")).map((item) =>
        item.getAttribute("data-key-status"),
      );
    expect(statuses(bare)).toEqual(["found", "missed"]);
    expect(within(bare).getByText(/Missed:/).parentElement).toHaveTextContent("three couplings");
    expect(within(bare).getByText(/Found:/).parentElement).toHaveTextContent("J");
    await waitFor(() => expect(statuses(harness)).toEqual(["found", "found"]));
    expect(within(harness).getByTestId("keys-summary")).toHaveTextContent(
      "2 of 2 keys found, 0 missing",
    );
    // gold papers: the closed book cites none, the harness both
    expect(within(bare).getByTestId("gold-summary")).toHaveTextContent("0 of 2 gold papers cited");
    expect(within(bare).getByText(/Not cited: alpha\.pdf, beta\.pdf/)).toBeInTheDocument();
    expect(within(harness).getByTestId("gold-summary")).toHaveTextContent(
      "2 of 2 gold papers cited",
    );
    expect(within(harness).getByText("Citations (2)")).toBeInTheDocument();
    expect(within(harness).getAllByText(/a1a1a1a1a1a1a1a1 pages 1-1/).length).toBeGreaterThan(0);
    // what the model read
    expect(within(bare).getByText("What the model read (0)")).toBeInTheDocument();
    expect(within(harness).getByText("What the model read (2)")).toBeInTheDocument();
    await user.click(within(harness).getByText("What the model read (2)"));
    expect(
      within(harness).getByText(/The exchange coupling between the two toy modes/),
    ).toBeVisible();
    // timings
    for (const label of ["Total time", "Search", "Rerank", "Writing"]) {
      expect(within(harness).getByText(label)).toBeInTheDocument();
    }
  });

  it("changes the repeat for every column", async () => {
    const { user } = await openPair();
    await openQuestion(user, "DP-002");
    const section = await screen.findByRole("region", { name: "Question DP-002 side by side" });
    const bare = column(RUNS.base);
    await waitFor(() =>
      expect(within(bare).getByTestId("keys-summary")).toHaveTextContent("1 of 2"),
    );
    await user.selectOptions(within(section).getByLabelText("Repeat"), "2");
    await waitFor(() =>
      expect(within(bare).getByTestId("keys-summary")).toHaveTextContent(
        "2 of 2 keys found, 0 missing",
      ),
    );
    expect(
      Array.from(bare.querySelectorAll("[data-key-status]")).map((item) =>
        item.getAttribute("data-key-status"),
      ),
    ).toEqual(["found", "found"]);
  });

  it("hides a run, but never the last one", async () => {
    const { user } = await openPair();
    await openQuestion(user, "DP-002");
    const section = await screen.findByRole("region", { name: "Question DP-002 side by side" });
    await user.click(within(section).getByRole("checkbox", { name: "bare, fake-model" }));
    expect(document.querySelector(`[data-run="${RUNS.base}"]`)).toBeNull();
    expect(column(RUNS.harness)).toBeInTheDocument();
    expect(within(section).getByRole("checkbox", { name: "harness, fake-model" })).toBeDisabled();
    await user.click(within(section).getByRole("checkbox", { name: "bare, fake-model" }));
    expect(column(RUNS.base)).toBeInTheDocument();
  });

  it("closes with the button and gives the focus back to the question", async () => {
    const { user } = await openPair();
    await openQuestion(user, "DP-001");
    const section = await screen.findByRole("region", { name: "Question DP-001 side by side" });
    await user.click(within(section).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: /side by side/ })).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Open question DP-001 side by side" }),
      ).toHaveFocus(),
    );
  });

  it("opens one question at a time", async () => {
    const { user } = await openPair();
    await openQuestion(user, "DP-001");
    await screen.findByRole("region", { name: "Question DP-001 side by side" });
    await openQuestion(user, "DP-002");
    expect(
      await screen.findByRole("region", { name: "Question DP-002 side by side" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Question DP-001 side by side" }),
    ).not.toBeInTheDocument();
  });

  it("shows the error of an answer that failed", async () => {
    server.use(
      http.get("*/api/v1/runs/:id/items", ({ params, request }) => {
        const id = new URL(request.url).searchParams.get("id") ?? "";
        const found = CAPTURE.runItems[`${String(params.id)}|${id}`];
        if (!found) return problem(404, "No such run.");
        return HttpResponse.json({
          ...found,
          items: found.items.map((item) => ({
            ...item,
            answer: null,
            error: { kind: "timeout", message: "The model took too long.", exit_code: null },
          })),
        });
      }),
    );
    const { user } = await openPair();
    await openQuestion(user, "DP-002");
    const bare = column(RUNS.base);
    expect(await within(bare).findByText("The question failed (timeout).")).toBeInTheDocument();
    expect(within(bare).getByText("The model took too long.")).toBeInTheDocument();
    expect(within(bare).getByText("The run gave no answer.")).toBeInTheDocument();
  });

  it("says so when a run has no answer for the repeat", async () => {
    server.use(
      http.get("*/api/v1/runs/:id/items", ({ params, request }) => {
        const id = new URL(request.url).searchParams.get("id") ?? "";
        const found = CAPTURE.runItems[`${String(params.id)}|${id}`];
        return found
          ? HttpResponse.json({ ...found, items: found.items.slice(0, 1) })
          : problem(404, "No such run.");
      }),
    );
    const { user } = await openPair();
    await openQuestion(user, "DP-002");
    const section = await screen.findByRole("region", { name: "Question DP-002 side by side" });
    await user.selectOptions(within(section).getByLabelText("Repeat"), "2");
    expect(
      await within(column(RUNS.base)).findByText("This run has no answer for repeat 2."),
    ).toBeInTheDocument();
  });

  it("keeps the answers when the question of the set cannot be read", async () => {
    server.use(
      http.get("*/api/v1/sets/:name/versions/:version/items/:id", () =>
        problem(403, "Not yours to read."),
      ),
    );
    const { user } = await openPair();
    await openQuestion(user, "DP-002");
    const section = await screen.findByRole("region", { name: "Question DP-002 side by side" });
    expect(await within(section).findByText("Not yours to read.")).toBeInTheDocument();
    expect(
      await within(column(RUNS.harness)).findAllByText(/The question's keys could not be read/),
    ).toHaveLength(2);
    expect(within(column(RUNS.harness)).getByText(/The key points are/)).toBeInTheDocument();
  });
});

describe("finding or making the score", () => {
  const pair = [RUNS.harness, RUNS.oracle];

  it("opens the newest score that holds the runs, and puts it in the address", async () => {
    const { app } = await open(address([RUNS.base, RUNS.harness], null));
    await screen.findByRole("table", { name: /Mean and 95% interval of each metric/ });
    expect(app.router.state.location.search).toBe(
      `?runs=${encodeURIComponent(`${RUNS.base},${RUNS.harness}`)}&score=${SCORE}`,
    );
  });

  it("offers to score runs that no score holds, as a pair with base and variant", async () => {
    await open(address(pair, null));
    const section = await screen.findByRole("region", { name: "Score these runs" });
    expect(within(section).getByText("Base").nextSibling).toHaveTextContent(
      `harness, fake-model ${RUNS.harness}`,
    );
    expect(within(section).getByText("Variant").nextSibling).toHaveTextContent(
      `oracle, fake-model ${RUNS.oracle}`,
    );
    expect(within(section).getByRole("button", { name: "Score and compare" })).toBeEnabled();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("scores the pair, waits for the job and opens the results", async () => {
    const bodies: unknown[] = [];
    let polls = 0;
    const scoreJob = (state: Job["state"], done: number): Job => ({
      ...CAPTURE.scoreJob,
      state,
      settled: state === "done",
      progress: { done, total: 1 },
      position: null,
    });
    server.use(
      http.post("*/api/v1/scores", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(scoreJob("queued", 0), { status: 202 });
      }),
      http.get(`*/api/v1/jobs/${CAPTURE.scoreId}`, () => {
        polls += 1;
        return HttpResponse.json(polls < 4 ? scoreJob("running", 0) : scoreJob("done", 1));
      }),
    );
    const { user, app } = await open(address(pair, null));
    await user.click(await screen.findByRole("button", { name: "Score and compare" }));
    expect(bodies).toEqual([
      {
        set: { name: "demo-papers", version: "v1" },
        bootstrap_engine: "python_random",
        number_words: false,
        runs: pair,
        pairs: [pair],
      },
    ]);
    await waitFor(() =>
      expect(app.router.state.location.search).toContain(`score=${CAPTURE.scoreId}`),
    );
    // while it works, the page says so in a live region and shows a bar
    const scoring = await screen.findByRole("region", { name: "Scoring" });
    expect(within(scoring).getByRole("status")).toHaveTextContent(/Running|Queued/);
    expect(
      within(scoring).getByRole("progressbar", { name: "Scoring progress" }),
    ).toBeInTheDocument();
    expect(within(scoring).getByRole("link", { name: /Open the job/ })).toBeInTheDocument();
    // then the results appear, from the score the job wrote
    expect(
      await screen.findByRole("table", { name: /Mean and 95% interval of each metric/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Scoring" })).not.toBeInTheDocument();
  });

  it("swaps base and variant before scoring", async () => {
    const bodies: { pairs: string[][] }[] = [];
    server.use(
      http.post("*/api/v1/scores", async ({ request }) => {
        bodies.push((await request.json()) as (typeof bodies)[number]);
        return HttpResponse.json(
          { ...CAPTURE.scoreJob, state: "queued", settled: false },
          { status: 202 },
        );
      }),
    );
    const { user } = await open(address(pair, null));
    const section = await screen.findByRole("region", { name: "Score these runs" });
    await user.click(within(section).getByRole("button", { name: "Swap base and variant" }));
    expect(within(section).getByText("Base").nextSibling).toHaveTextContent(RUNS.oracle);
    await user.click(within(section).getByRole("button", { name: "Score and compare" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]?.pairs).toEqual([[RUNS.oracle, RUNS.harness]]);
  });

  it("sends the noise pair of the arm of the base, with its runs among those scored", async () => {
    const bodies: { runs: string[]; pairs: string[][]; noise: string[] }[] = [];
    server.use(
      http.post("*/api/v1/scores", async ({ request }) => {
        bodies.push((await request.json()) as (typeof bodies)[number]);
        return HttpResponse.json(
          { ...CAPTURE.scoreJob, state: "queued", settled: false },
          { status: 202 },
        );
      }),
    );
    // no recorded score compares the second closed-book run with the harness
    const { user } = await open(address([RUNS.noise, RUNS.harness], null));
    const section = await screen.findByRole("region", { name: "Score these runs" });
    const first = within(section).getByLabelText("First run");
    // only runs of the arm of the base are offered
    expect(
      within(first)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(
      ["None", RUNS.noise, RUNS.base]
        .sort((a, b) => (a === "None" ? -1 : b === "None" ? 1 : 0))
        .slice(0, 3),
    );
    await user.selectOptions(first, RUNS.base);
    expect(within(section).getByRole("alert")).toHaveTextContent(
      "Pick two different runs, or none.",
    );
    expect(within(section).getByRole("button", { name: "Score and compare" })).toBeDisabled();
    await user.selectOptions(within(section).getByLabelText("Second run"), RUNS.noise);
    expect(within(section).queryByRole("alert")).not.toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Score and compare" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({
      pairs: [[RUNS.noise, RUNS.harness]],
      noise: [RUNS.base, RUNS.noise],
    });
    expect(new Set(bodies[0]?.runs)).toEqual(new Set([RUNS.noise, RUNS.harness, RUNS.base]));
  });

  it("scores one run on its own, with no pair", async () => {
    const bodies: { runs: string[]; pairs: string[][] }[] = [];
    server.use(
      http.get("*/api/v1/scores", () => HttpResponse.json([])),
      http.post("*/api/v1/scores", async ({ request }) => {
        bodies.push((await request.json()) as (typeof bodies)[number]);
        return HttpResponse.json(
          { ...CAPTURE.scoreJob, state: "queued", settled: false },
          { status: 202 },
        );
      }),
    );
    const { user } = await open(address([RUNS.oracle], null), "Results of a run");
    const section = await screen.findByRole("region", { name: "Score these runs" });
    expect(within(section).queryByLabelText("First run")).not.toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Score the run" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ runs: [RUNS.oracle], pairs: [] });
  });

  it("shows the words of the server when it refuses the scoring", async () => {
    server.use(http.post("*/api/v1/scores", () => problem(409, "The run is still going.")));
    const { user } = await open(address(pair, null));
    await user.click(await screen.findByRole("button", { name: "Score and compare" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not possible now");
    expect(alert).toHaveTextContent("The run is still going.");
  });
});

describe("a scoring that does not finish", () => {
  const job = (state: Job["state"], extra: Partial<Job> = {}): Job => ({
    ...CAPTURE.scoreJob,
    state,
    settled: state !== "queued" && state !== "running",
    progress: { done: 0, total: 1 },
    ...extra,
  });
  const serve = (current: () => Job) =>
    server.use(http.get(`*/api/v1/jobs/${CAPTURE.scoreId}`, () => HttpResponse.json(current())));

  it("says why a scoring failed", async () => {
    serve(() =>
      job("failed", { error: { kind: "score", message: "A run does not fit the set." } }),
    );
    await open(address([RUNS.base, RUNS.harness]));
    const scoring = await screen.findByRole("region", { name: "Scoring" });
    expect(within(scoring).getByRole("alert")).toHaveTextContent("A run does not fit the set.");
    expect(within(scoring).getByRole("status")).toHaveTextContent("Failed");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("resumes an interrupted scoring and shows the results when it ends", async () => {
    let current = job("interrupted");
    serve(() => current);
    server.use(
      http.post(`*/api/v1/jobs/${CAPTURE.scoreId}/resume`, () => {
        current = job("done", { progress: { done: 1, total: 1 } });
        return HttpResponse.json(current);
      }),
    );
    const { user } = await open(address([RUNS.base, RUNS.harness]));
    const scoring = await screen.findByRole("region", { name: "Scoring" });
    await user.click(within(scoring).getByRole("button", { name: "Resume" }));
    expect(
      await screen.findByRole("table", { name: /Mean and 95% interval of each metric/ }),
    ).toBeInTheDocument();
  });

  it("does not take another kind of job for a score", async () => {
    server.use(
      http.get(`*/api/v1/jobs/${CAPTURE.scoreId}`, () => HttpResponse.json(CAPTURE.jobs[0])),
    );
    await open(address([RUNS.base, RUNS.harness]));
    expect(await screen.findByRole("alert")).toHaveTextContent("This job is not a scoring.");
  });

  it("reads a score that has no job (a file put in the folder by hand)", async () => {
    server.use(http.get(`*/api/v1/jobs/${CAPTURE.scoreId}`, () => problem(404, "No such job.")));
    await open(address([RUNS.base, RUNS.harness]));
    expect(
      await screen.findByRole("table", { name: /Mean and 95% interval of each metric/ }),
    ).toBeInTheDocument();
  });

  it("says so for a score that does not exist", async () => {
    await open(address([RUNS.base, RUNS.harness], "01m5yz00000000000000000000"));
    expect(await screen.findByRole("alert")).toHaveTextContent("No such score");
  });

  it("refuses runs that the score does not hold", async () => {
    await open(address([RUNS.base, "some-other-run"]));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This score does not hold the runs named in the address.",
    );
  });
});

describe("addresses that cannot be shown", () => {
  it("asks for runs when the address has none", async () => {
    await open("/benchmarks/compare", "Results of a run");
    expect(screen.getByText(/No runs were chosen/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Pick runs on the Benchmarks screen" }),
    ).toHaveAttribute("href", "/benchmarks#runs");
  });

  it("refuses more than two runs and ids that are not run ids", async () => {
    await open("/benchmarks/compare?runs=a,b,c", "Results of a run");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A comparison takes one or two runs.",
    );
  });

  it("refuses a run named twice", async () => {
    await open("/benchmarks/compare?runs=a,a", "Results of a run");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The run ids in the address are not valid",
    );
  });

  it("drops a score that is not a job id and looks for one", async () => {
    const { app } = await open(
      `/benchmarks/compare?runs=${RUNS.base}&score=nope`,
      "Results of a run",
    );
    await waitFor(() => expect(app.router.state.location.search).toContain(`score=${SCORE}`));
  });

  it("says when a run does not exist", async () => {
    await open("/benchmarks/compare?runs=no-such-run", "Results of a run");
    server.use(http.get("*/api/v1/scores", () => HttpResponse.json([])));
    expect(await screen.findByRole("alert")).toHaveTextContent("One of these runs does not exist.");
  });

  it("does not score runs of different sets", async () => {
    server.use(
      http.get("*/api/v1/scores", () => HttpResponse.json([])),
      http.get("*/api/v1/runs", () =>
        HttpResponse.json(
          CAPTURE.runs.map((run) =>
            run.run_id === RUNS.oracle ? { ...run, set_md5: "0".repeat(32) } : run,
          ),
        ),
      ),
    );
    await open(address([RUNS.harness, RUNS.oracle], null));
    expect(await screen.findByRole("alert")).toHaveTextContent("so they cannot be scored together");
  });

  it("does not score a run that is still going", async () => {
    server.use(
      http.get("*/api/v1/scores", () => HttpResponse.json([])),
      http.get("*/api/v1/runs", () =>
        HttpResponse.json(
          CAPTURE.runs.map((run) =>
            run.run_id === RUNS.oracle ? { ...run, state: "running" } : run,
          ),
        ),
      ),
    );
    await open(address([RUNS.harness, RUNS.oracle], null));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      `The run ${RUNS.oracle} is still going.`,
    );
  });

  it("shows a panel with Try again when the scores cannot be read", async () => {
    let fail = true;
    server.use(
      http.get("*/api/v1/scores", () =>
        fail ? problem(403, "Scores are closed to you.") : HttpResponse.json(CAPTURE.scores),
      ),
    );
    const { user } = await open(address([RUNS.base, RUNS.harness], null));
    expect(await screen.findByRole("alert")).toHaveTextContent("Scores are closed to you.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("table", { name: /Mean and 95% interval of each metric/ }),
    ).toBeInTheDocument();
  });
});
