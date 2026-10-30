import { CAPTURE, RUNS } from "@/test/benchmarks-api";
import {
  canCancel,
  canResume,
  compareAddress,
  compareRows,
  defaultGroups,
  defaultMetrics,
  elapsedSeconds,
  findScore,
  runChangedAt,
  runNow,
  scoreIsOlder,
  formatDuration,
  formatInterval,
  formatNumber,
  formatSigned,
  frozenVersions,
  goldCited,
  groupNames,
  isPartial,
  isScorable,
  keyOutcomes,
  metricNames,
  parseCompareAddress,
  passesFilter,
  progressShare,
  questionCells,
  runLabels,
  shortHash,
  shortId,
  splitSetName,
  summaryRows,
  versionKey,
} from "./domain";
import type { RunNow } from "./domain";
import type { KeyFact, RunSummary, ScoreGroup, ScoreItem, ScoreSummary, SetEntry } from "./types";

const JOB = CAPTURE.jobs[0]!;

describe("jobs", () => {
  it("lets a queued or running job be cancelled and a stopped one be resumed", () => {
    expect(canCancel({ state: "queued" })).toBe(true);
    expect(canCancel({ state: "running" })).toBe(true);
    for (const state of ["cancelling", "cancelled", "done", "failed", "interrupted"] as const) {
      expect(canCancel({ state }), state).toBe(false);
    }
    expect(canResume({ state: "cancelled" })).toBe(true);
    expect(canResume({ state: "interrupted" })).toBe(true);
    for (const state of ["queued", "running", "cancelling", "done", "failed"] as const) {
      expect(canResume({ state }), state).toBe(false);
    }
  });

  it("counts the time of a job up to now while it runs and up to its end after", () => {
    const start = "2026-10-27T10:00:00Z";
    const now = Date.parse("2026-10-27T10:01:12Z");
    expect(elapsedSeconds({ state: "queued", started_at: null, ended_at: null }, now)).toBeNull();
    expect(elapsedSeconds({ state: "running", started_at: start, ended_at: null }, now)).toBe(72);
    expect(
      elapsedSeconds({ state: "done", started_at: start, ended_at: "2026-10-27T10:00:30Z" }, now),
    ).toBe(30);
    // a job that waits again after a resume keeps its old end: it is still going
    expect(
      elapsedSeconds(
        { state: "running", started_at: start, ended_at: "2026-10-27T10:00:30Z" },
        now,
      ),
    ).toBe(72);
    // a job that waits, or has no end, does not count
    expect(
      elapsedSeconds({ state: "queued", started_at: start, ended_at: "2026-10-27T10:00:30Z" }, now),
    ).toBeNull();
    expect(
      elapsedSeconds({ state: "cancelled", started_at: start, ended_at: null }, now),
    ).toBeNull();
    // a clock that is behind never gives a negative time
    expect(elapsedSeconds({ state: "running", started_at: start, ended_at: null }, 0)).toBe(0);
    expect(
      elapsedSeconds({ state: "running", started_at: "nonsense", ended_at: null }, now),
    ).toBeNull();
  });

  it("gives the share of the answers done", () => {
    expect(progressShare({ progress: { done: 3, total: 12 } })).toBe(0.25);
    expect(progressShare({ progress: { done: 0, total: 0 } })).toBe(0);
    expect(progressShare({ progress: { done: 15, total: 12 } })).toBe(1);
  });

  it("shortens an id to its last characters", () => {
    expect(shortId(JOB.id)).toHaveLength(8);
    expect(JOB.id.endsWith(shortId(JOB.id))).toBe(true);
  });
});

describe("the start form", () => {
  const sets: SetEntry[] = CAPTURE.sets;

  it("lists frozen versions only, by name and version number", () => {
    const frozen = frozenVersions(sets);
    expect(frozen.map(versionKey)).toEqual(["demo-papers/v1"]);
    expect(frozen[0]?.status).toBe("frozen");
    const many = frozenVersions([
      {
        name: "b",
        corpus: null,
        versions: [
          { ...sets[1]!.versions[0]!, name: "b", version: "v10", status: "frozen" },
          { ...sets[1]!.versions[0]!, name: "b", version: "v2", status: "frozen" },
        ],
      },
      {
        name: "a",
        corpus: null,
        versions: [{ ...sets[1]!.versions[0]!, name: "a", status: "frozen" }],
      },
    ]);
    expect(many.map(versionKey)).toEqual(["a/v1", "b/v2", "b/v10"]);
  });

  it("shortens a hash", () => {
    expect(shortHash("cb9cb733168663d4b04e75cdf16e5030")).toBe("cb9cb733");
  });
});

describe("the compare address", () => {
  const parse = (query: string) => parseCompareAddress(new URLSearchParams(query));
  const JOB_ID = "01m5yz8v932fnzdf4nepwwddzb";

  it("reads one or two run ids and an optional score", () => {
    expect(parse("")).toEqual({ runs: [], score: null, problem: "none" });
    expect(parse("runs=a")).toEqual({ runs: ["a"], score: null, problem: null });
    expect(parse(`runs=a,b&score=${JOB_ID}`)).toEqual({
      runs: ["a", "b"],
      score: JOB_ID,
      problem: null,
    });
  });

  it("refuses ids that are not run ids, repeated ids and too many runs", () => {
    expect(parse("runs=a,b,c").problem).toBe("tooMany");
    expect(parse("runs=a,a").problem).toBe("invalid");
    expect(parse("runs=a,../b").problem).toBe("invalid");
    expect(parse("runs=,a").problem).toBe("invalid");
    expect(parse("runs=a b").problem).toBe("invalid");
  });

  it("keeps the runs and drops a score that is not a job id", () => {
    expect(parse("runs=a&score=nope")).toEqual({
      runs: ["a"],
      score: null,
      problem: "invalidScore",
    });
  });

  it("writes an address that parses back", () => {
    const address = compareAddress(["a", "b"], JOB_ID);
    expect(address).toBe(`/benchmarks/compare?runs=a%2Cb&score=${JOB_ID}`);
    expect(parse(address.split("?")[1] ?? "")).toEqual({
      runs: ["a", "b"],
      score: JOB_ID,
      problem: null,
    });
    expect(compareAddress(["a"])).toBe("/benchmarks/compare?runs=a");
  });
});

describe("finding a score", () => {
  const score = (
    id: string,
    created: string,
    runs: string[],
    pairs: [string, string][],
  ): ScoreSummary => ({
    ...CAPTURE.scores[0]!,
    score_id: id,
    created,
    runs: runs.map((run_id) => ({ run_id, file: `${run_id}.jsonl`, md5: "m" })),
    pairs: pairs.map(([base_run, var_run]) => ({ base_run, var_run })),
  });
  const scores = [
    score("old", "2026-01-01T00:00:00+00:00", ["a", "b"], [["a", "b"]]),
    score("new", "2026-02-01T00:00:00+00:00", ["a", "b", "c"], [["a", "b"]]),
    score("swapped", "2026-03-01T00:00:00+00:00", ["a", "b"], [["b", "a"]]),
  ];

  it("takes the newest score that holds the runs, and the pair in that order", () => {
    expect(findScore(scores, ["a", "b"]).current?.score_id).toBe("new");
    expect(findScore(scores, ["b", "a"]).current?.score_id).toBe("swapped");
    expect(findScore(scores, ["a"]).current?.score_id).toBe("swapped");
    expect(findScore(scores, ["a", "z"])).toEqual({ current: null, stale: null });
    expect(findScore([], ["a"])).toEqual({ current: null, stale: null });
  });

  // a run list without md5: only the times can tell
  const timed = (changedAt: number | null): RunNow => ({ md5: null, changedAt });

  it("leaves out a score made before one of its runs changed, and says it exists", () => {
    const changed = { a: timed(Date.parse("2026-02-15T00:00:00Z")), b: timed(null) };
    // "new" was made before a changed: the older "old" is older still, so no score is current
    const found = findScore(scores, ["a", "b"], changed);
    expect(found.current).toBeNull();
    expect(found.stale?.score_id).toBe("new");
    // a score made after the change is current again, and the stale one is still reported
    const after = [
      ...scores,
      score("fresh", "2026-04-01T00:00:00+00:00", ["a", "b"], [["a", "b"]]),
    ];
    expect(findScore(after, ["a", "b"], changed).current?.score_id).toBe("fresh");
  });

  it("dates a run by the later of its end and the end of its job", () => {
    const at = (text: string) => Date.parse(text);
    const ended = { ended: "2026-03-01T10:00:00Z", state: "done" } as const;
    expect(runChangedAt(ended, { ended_at: "2026-03-01T10:05:00Z", settled: true })).toBe(
      at("2026-03-01T10:05:00Z"),
    );
    expect(runChangedAt(ended, null)).toBe(at("2026-03-01T10:00:00Z"));
    expect(
      runChangedAt({ ended: null, state: "cancelled" }, { ended_at: null, settled: true }),
    ).toBeNull();
    expect(runChangedAt({ ended: "nonsense", state: null }, undefined)).toBeNull();
  });

  it("treats a run whose job is not settled as changing now", () => {
    // a resumed job keeps its id and has no end; the cancelled run it made has none either
    const cancelled = { ended: null, state: "cancelled" } as const;
    for (const state of ["queued", "running", "cancelling"] as const) {
      expect(runChangedAt({ ended: null, state }, null)).toBe(Number.POSITIVE_INFINITY);
      expect(runChangedAt(cancelled, { ended_at: null, settled: false })).toBe(
        Number.POSITIVE_INFINITY,
      );
    }
    const partial = score("partial", "2026-03-01T10:00:00+00:00", ["a"], []);
    const changing = timed(runChangedAt(cancelled, { ended_at: null, settled: false }));
    expect(scoreIsOlder(partial, "a", changing)).toBe(true);
    // the file has not grown yet, so its md5 is still the scored one: still going is newer
    expect(scoreIsOlder(partial, "a", { ...changing, md5: "m" })).toBe(true);
    const found = findScore([partial], ["a"], { a: changing });
    expect(found.current).toBeNull();
    expect(found.stale?.score_id).toBe("partial");
    // once it settles again its own end decides
    expect(runChangedAt(cancelled, { ended_at: "2026-03-01T09:00:00Z", settled: true })).toBe(
      Date.parse("2026-03-01T09:00:00Z"),
    );
  });

  it("allows for the second a score is written to when the times decide", () => {
    const made = score("s", "2026-03-01T10:00:00+00:00", ["a"], []);
    const at = (text: string) => scoreIsOlder(made, "a", timed(Date.parse(text)));
    expect(at("2026-03-01T10:00:00.659Z")).toBe(false);
    expect(at("2026-03-01T10:00:02Z")).toBe(true);
    expect(scoreIsOlder(made, "a", timed(null))).toBe(false);
    expect(
      scoreIsOlder({ ...made, created: "" }, "a", timed(Date.parse("2026-03-01T10:00:02Z"))),
    ).toBe(false);
    expect(scoreIsOlder(made, "a", undefined)).toBe(false);
  });

  describe("by the md5 of the runs", () => {
    const made = score("s", "2026-03-01T10:00:00+00:00", ["a", "b"], [["a", "b"]]);
    const scoredA = {
      ...made,
      runs: made.runs.map((run) => ({ ...run, md5: `md5-${run.run_id}` })),
    };
    const same = (id: string, changedAt: number | null = null): RunNow => ({
      md5: `md5-${id}`,
      changedAt,
    });

    it("is older exactly when an md5 differs", () => {
      const now = { a: same("a"), b: same("b") };
      expect(scoreIsOlder(scoredA, "a", now.a)).toBe(false);
      expect(findScore([scoredA], ["a", "b"], now).current?.score_id).toBe("s");
      const grown = { ...now, b: { ...now.b, md5: "another" } };
      expect(scoreIsOlder(scoredA, "a", grown.a)).toBe(false);
      expect(scoreIsOlder(scoredA, "b", grown.b)).toBe(true);
      expect(findScore([scoredA], ["a", "b"], grown)).toMatchObject({
        current: null,
        stale: { score_id: "s" },
      });
    });

    it("sees a change the times miss: the file changed within the second of the score", () => {
      // the run ended 0.4 s after the score was made: the time rule lets that pass
      const changedAt = Date.parse("2026-03-01T10:00:00.400Z");
      expect(scoreIsOlder(made, "a", timed(changedAt))).toBe(false);
      expect(scoreIsOlder(scoredA, "a", { md5: "another", changedAt })).toBe(true);
      // and a run whose times were lost (no end, no job) is still caught
      expect(scoreIsOlder(scoredA, "a", { md5: "another", changedAt: null })).toBe(true);
    });

    it("keeps a score whose md5 are equal though the times say the run is newer", () => {
      // a job row touched after the score, or a clock that was set back, changes no answer
      const changedAt = Date.parse("2026-03-02T00:00:00Z");
      expect(scoreIsOlder(made, "a", timed(changedAt))).toBe(true);
      expect(scoreIsOlder(scoredA, "a", same("a", changedAt))).toBe(false);
    });

    it("falls back to the times when the summary or the score has no md5", () => {
      const late = Date.parse("2026-03-02T00:00:00Z");
      expect(scoreIsOlder(scoredA, "a", { md5: null, changedAt: late })).toBe(true);
      expect(scoreIsOlder(scoredA, "a", { md5: null, changedAt: null })).toBe(false);
      const unscored = { ...scoredA, runs: scoredA.runs.map((run) => ({ ...run, md5: "" })) };
      expect(scoreIsOlder(unscored, "a", { md5: "x", changedAt: late })).toBe(true);
      expect(scoreIsOlder(unscored, "a", { md5: "x", changedAt: null })).toBe(false);
    });

    it("takes the md5 of the run list into what it knows of a run", () => {
      const run = { ended: "2026-03-01T10:00:00Z", state: "done", md5: "m1" } as const;
      expect(runNow(run, { ended_at: "2026-03-01T10:05:00Z", settled: true })).toEqual({
        md5: "m1",
        changedAt: Date.parse("2026-03-01T10:05:00Z"),
      });
      expect(runNow({ ...run, md5: undefined }, null).md5).toBeNull();
      expect(runNow({ ...run, md5: null }, null).md5).toBeNull();
    });
  });

  it("scores a run that has stopped and flags one that did not reach its end", () => {
    expect(isScorable({ state: "done" })).toBe(true);
    expect(isScorable({ state: "cancelled" })).toBe(true);
    expect(isScorable({ state: null })).toBe(true);
    expect(isScorable({ state: "running" })).toBe(false);
    expect(isScorable({ state: "queued" })).toBe(false);
    expect(isScorable({ state: "cancelling" })).toBe(false);
    expect(isPartial({ complete: false })).toBe(true);
    expect(isPartial({ complete: true })).toBe(false);
  });
});

describe("naming runs", () => {
  const run = (run_id: string, arm: string, model: string): RunSummary => ({
    ...CAPTURE.runs[0]!,
    run_id,
    arm,
    model,
    variant: null,
  });

  it("uses the arm and model, and the end of the id when two would read the same", () => {
    const summaries = [
      run("r-1", "bare", "m"),
      run("r-2", "bare", "m"),
      run("r-3", "harness", "m"),
    ];
    expect(runLabels(["r-1", "r-3"], summaries, [])).toEqual({
      "r-1": "bare, m",
      "r-3": "harness, m",
    });
    expect(runLabels(["r-1", "r-2"], summaries, [])).toEqual({
      "r-1": "bare, m (r-1)",
      "r-2": "bare, m (r-2)",
    });
  });

  it("falls back to the score's label and then to the id", () => {
    expect(runLabels(["x", "y"], [], [{ run_id: "x", label: "bare" }])).toEqual({
      x: "bare",
      y: "y",
    });
  });
});

describe("the tables of a score", () => {
  const { groups, compares } = CAPTURE.score;
  const runs = [RUNS.base, RUNS.harness];

  it("puts the usual metrics first and groups all first", () => {
    const names = metricNames(groups);
    expect(names.slice(0, 4)).toEqual(["all_keys", "all_keys_lenient", "key_share", "wall_s"]);
    expect(new Set(names).size).toBe(names.length);
    expect(groupNames(groups)[0]).toBe("all");
    expect(groupNames(groups)).toContain("lang=es");
    expect(defaultMetrics(names)).toEqual(["all_keys", "all_keys_lenient", "key_share"]);
    expect(defaultMetrics(["b", "a", "c", "d"])).toEqual(["b", "a", "c"]);
    expect(defaultMetrics([])).toEqual([]);
  });

  it("starts with all, the content and the lang groups, or with every group when there are none", () => {
    expect(defaultGroups(groupNames(groups))).toEqual([
      "all",
      "content=math",
      "content=other",
      "lang=en",
      "lang=es",
    ]);
    expect(defaultGroups(["all", "category=a", "form=single"])).toEqual([
      "all",
      "category=a",
      "form=single",
    ]);
    expect(defaultGroups(["all"])).toEqual(["all"]);
    expect(defaultGroups(["all", "form=single", "lang=es"])).toEqual(["all", "lang=es"]);
    expect(defaultGroups([])).toEqual([]);
  });

  it("gives a row per group and metric with the numbers of the score and a cell per run", () => {
    const rows = summaryRows(groups, runs, ["all_keys"], ["all", "lang=es"]);
    expect(rows.map((row) => row.group)).toEqual(["all", "lang=es"]);
    const first = rows[0]!;
    const record = groups.find(
      (g) => g.run_id === RUNS.harness && g.group === "all" && g.metric === "all_keys",
    )!;
    expect(first.cells[RUNS.harness]).toEqual({ mean: record.mean, ci: record.ci, n: record.n });
    expect(first.cells[RUNS.oracle]).toBeUndefined();
  });

  it("leaves a group without an interval as null and reads all groups when none is chosen", () => {
    const lone: ScoreGroup[] = [
      { run_id: "r", group: "all", metric: "m", n: 1, mean: 0.5, ci: null },
      { run_id: "r", group: "g=a", metric: "m", n: 1, mean: null },
    ];
    const rows = summaryRows(lone, ["r"], ["m"]);
    expect(rows).toHaveLength(2);
    expect(rows[0]?.cells.r?.ci).toBeNull();
    expect(rows[1]?.cells.r?.mean).toBeNull();
  });

  it("takes the pair in order, and only the chosen metrics and groups", () => {
    const order = groupNames(groups);
    const rows = compareRows(compares, RUNS.base, RUNS.harness, ["all_keys", "key_share"], order, [
      "all",
    ]);
    expect(rows.map((row) => row.metric)).toEqual(["all_keys", "key_share"]);
    expect(rows.every((row) => row.var_run === RUNS.harness && row.group === "all")).toBe(true);
    expect(compareRows(compares, RUNS.harness, RUNS.base, ["all_keys"], order)).toEqual([]);
  });
});

describe("questions", () => {
  const item = (
    id: string,
    run_id: string,
    repeat: number,
    all_keys: number | boolean | string | null,
    keys: Record<string, unknown>[] | null,
  ): ScoreItem => ({ id, run_id, repeat, metrics: { all_keys: all_keys as number }, keys });

  const items: ScoreItem[] = [
    item("q1", "a", 1, true, [
      { i: 0, exact: true, lenient: true },
      { i: 1, exact: false, lenient: true },
    ]),
    item("q1", "a", 2, false, [
      { i: 0, exact: true, lenient: true },
      { i: 1, exact: false, lenient: false },
    ]),
    item("q1", "b", 1, true, [
      { i: 0, exact: true, lenient: true },
      { i: 1, exact: true, lenient: true },
    ]),
    item("q2", "a", 1, 0.5, null),
    item("q2", "b", 1, null, []),
    item("q3", "a", 1, "not a number", []),
  ];

  it("averages the metric over the repeats and the keys found, as the scorer does", () => {
    const cells = questionCells(items, "all_keys");
    expect(cells.get("q1")?.a).toEqual({
      value: 0.5,
      repeats: 2,
      keysTotal: 2,
      keysFound: 1,
      keysLenientOnly: 0.5,
    });
    expect(cells.get("q1")?.b).toMatchObject({ value: 1, keysFound: 2, keysLenientOnly: 0 });
    expect(cells.get("q2")?.a).toMatchObject({ value: 0.5, keysTotal: 0 });
    expect(cells.get("q2")?.b?.value).toBeNull();
    expect(cells.get("q3")?.a?.value).toBeNull();
  });

  it("filters the questions where the variant is better, worse or different", () => {
    const cells = questionCells(items, "all_keys");
    const q1 = cells.get("q1");
    const q2 = cells.get("q2");
    expect(passesFilter(q1, "a", "b", "all")).toBe(true);
    expect(passesFilter(q1, "a", "b", "up")).toBe(true);
    expect(passesFilter(q1, "a", "b", "down")).toBe(false);
    expect(passesFilter(q1, "a", "b", "disagree")).toBe(true);
    expect(passesFilter(q1, "b", "a", "down")).toBe(true);
    // a question one run did not answer, or has no value for, is neither better nor worse
    expect(passesFilter(q2, "a", "b", "disagree")).toBe(false);
    expect(passesFilter(undefined, "a", "b", "disagree")).toBe(false);
    expect(passesFilter(undefined, "a", "b", "all")).toBe(true);
    const same = questionCells(
      [item("s", "a", 1, 1, null), item("s", "b", 1, 1, null)],
      "all_keys",
    );
    expect(passesFilter(same.get("s"), "a", "b", "disagree")).toBe(false);
  });

  it("tells which keys were found, found only by the lenient rule, missed or not scored", () => {
    const facts: KeyFact[] = ["J", "three", "x", "y"].map((value) => ({
      type: "text",
      value,
      unit: null,
      paper: null,
    }));
    const outcomes = keyOutcomes(facts, [
      { index: 0, exact: true, lenient: true },
      { index: 1, exact: false, lenient: true },
      { index: 2, exact: false, lenient: false },
    ]);
    expect(outcomes.map((outcome) => outcome.status)).toEqual([
      "found",
      "lenient",
      "missed",
      "unscored",
    ]);
    expect(outcomes[1]?.fact.value).toBe("three");
  });

  it("tells which gold papers an answer cites, by the long id or its start", () => {
    const gold = [
      { docId: "a".repeat(64), doc: "alpha.pdf" },
      { docId: "b".repeat(64), doc: null },
    ];
    expect(goldCited(gold, [{ doc_id: "a".repeat(64) }])).toEqual({
      cited: [gold[0]],
      missing: [gold[1]],
    });
    expect(goldCited(gold, [{ doc_id: "b".repeat(16) }]).cited).toEqual([gold[1]]);
    expect(goldCited(gold, []).missing).toHaveLength(2);
    // an empty or short id is no citation: it would otherwise match every paper
    expect(goldCited(gold, [{ doc_id: "" }]).cited).toEqual([]);
    expect(goldCited(gold, [{ doc_id: "a".repeat(8) }]).cited).toEqual([]);
    expect(goldCited([], [{ doc_id: "x" }])).toEqual({ cited: [], missing: [] });
  });

  it("splits the name of a set version", () => {
    expect(splitSetName("demo-papers/v1")).toEqual({ name: "demo-papers", version: "v1" });
    expect(splitSetName("a/b/v2")).toEqual({ name: "a/b", version: "v2" });
    expect(splitSetName("nothing")).toBeNull();
    expect(splitSetName("/v1")).toBeNull();
    expect(splitSetName("x/")).toBeNull();
  });
});

describe("numbers and times", () => {
  it("shows up to four decimals as the iteration records do", () => {
    expect(formatNumber(0.0198, "en")).toBe("0.0198");
    expect(formatNumber(0.375, "en")).toBe("0.375");
    expect(formatNumber(12, "en")).toBe("12");
    expect(formatNumber(0.123456, "en")).toBe("0.1235");
    expect(formatNumber(0.375, "es")).toBe("0,375");
    expect(formatNumber(1234.5, "en")).toBe("1234.5");
  });

  it("signs a difference and never shows a signed zero", () => {
    expect(formatSigned(0.0198, "en")).toBe("+0.0198");
    expect(formatSigned(-0.0297, "en")).toBe("-0.0297");
    expect(formatSigned(0, "en")).toBe("0");
    expect(formatSigned(-0.00001, "en")).toBe("0");
    expect(formatSigned(0.5, "es")).toBe("+0,5");
  });

  it("writes an interval and nothing for a missing one", () => {
    expect(formatInterval([-0.0297, 0.0693], "en", true)).toBe("[-0.0297, +0.0693]");
    expect(formatInterval([0, 0.75], "en")).toBe("[0, 0.75]");
    expect(formatInterval(null, "en")).toBe("");
    expect(formatInterval([1], "en")).toBe("");
  });

  it("writes a duration in seconds, minutes and hours", () => {
    expect(formatDuration(12)).toBe("12 s");
    expect(formatDuration(72)).toBe("1 min 12 s");
    expect(formatDuration(3660)).toBe("1 h 1 min");
  });
});
