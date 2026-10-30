import type {
  GoldPaper,
  Job,
  KeyFact,
  KeyResult,
  RunSummary,
  ScoreCompare,
  ScoreGroup,
  ScoreItem,
  ScoreSummary,
  SetEntry,
  SetVersion,
} from "./types";

// Plain functions, no React: what the screens decide from the API's answers.

const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const JOB_ID = /^[0-9a-hjkmnp-tv-z]{26}$/;

export function isRunId(value: string): boolean {
  return RUN_ID.test(value);
}

export function isJobId(value: string): boolean {
  return JOB_ID.test(value);
}

// ---- jobs ------------------------------------------------------------------------------

/** A queued or running job can be cancelled; the runner stops before its next question. */
export function canCancel(job: Pick<Job, "state">): boolean {
  return job.state === "queued" || job.state === "running";
}

/** A cancelled or interrupted job continues from the first answer it lacks. */
export function canResume(job: Pick<Job, "state">): boolean {
  return job.state === "cancelled" || job.state === "interrupted";
}

/** Whole seconds a job has taken, up to `nowMs` while it runs. Null before it starts and while it waits. */
export function elapsedSeconds(
  job: Pick<Job, "started_at" | "ended_at" | "state">,
  nowMs: number,
): number | null {
  if (job.started_at === null) return null;
  const start = Date.parse(job.started_at);
  if (Number.isNaN(start)) return null;
  const live = job.state === "running" || job.state === "cancelling";
  if (!live && (job.ended_at === null || job.state === "queued")) return null;
  const stop = live ? nowMs : Date.parse(job.ended_at ?? "");
  if (Number.isNaN(stop)) return null;
  return Math.max(0, Math.floor((stop - start) / 1000));
}

/** `done / total` as a share of 1, or 0 when nothing is expected. */
export function progressShare(job: Pick<Job, "progress">): number {
  const { done, total } = job.progress;
  return total > 0 ? Math.min(1, Math.max(0, done / total)) : 0;
}

/** The last characters of a job id: the front of a sortable id is its time, the same for jobs made together. */
export function shortId(id: string): string {
  return id.slice(-8);
}

/** What a job is, in a few words: `harness, fake-model` for a run; `scoring` is the caller's word. */
export function jobTitleParts(job: Job): { arm: string; model: string } | null {
  return job.run ? { arm: job.run.arm.name, model: job.run.model } : null;
}

// ---- the start form --------------------------------------------------------------------

function versionNumber(version: string): number {
  return Number(version.slice(1));
}

/** Frozen versions only (a run needs a set that cannot change), by name and then version. */
export function frozenVersions(sets: readonly SetEntry[]): SetVersion[] {
  return sets
    .flatMap((entry) => entry.versions)
    .filter((version) => version.status === "frozen")
    .sort(
      (a, b) =>
        a.name.localeCompare(b.name, "en") || versionNumber(a.version) - versionNumber(b.version),
    );
}

export function versionKey(version: Pick<SetVersion, "name" | "version">): string {
  return `${version.name}/${version.version}`;
}

/** The first characters of a hash, enough to tell two versions apart in a list. */
export function shortHash(md5: string): string {
  return md5.slice(0, 8);
}

// ---- the compare address ---------------------------------------------------------------

export interface CompareAddress {
  runs: string[];
  /** The id of a scoring job (also the id of its file), when the address has one. */
  score: string | null;
  problem: "none" | "invalid" | "tooMany" | "invalidScore" | null;
}

/** `?runs=a,b&score=s`: one or two run ids and, once scored, the score. Nothing else is read. */
export function parseCompareAddress(params: URLSearchParams): CompareAddress {
  const raw = params.get("runs") ?? "";
  const runs = raw === "" ? [] : raw.split(",");
  const scoreRaw = params.get("score");
  const score = scoreRaw === null || scoreRaw === "" ? null : scoreRaw;
  if (runs.length === 0) return { runs, score: null, problem: "none" };
  if (runs.length > 2) return { runs: [], score: null, problem: "tooMany" };
  if (!runs.every(isRunId) || new Set(runs).size !== runs.length) {
    return { runs: [], score: null, problem: "invalid" };
  }
  if (score !== null && !isJobId(score)) return { runs, score: null, problem: "invalidScore" };
  return { runs, score, problem: null };
}

export function compareAddress(runs: readonly string[], score?: string | null): string {
  const query = new URLSearchParams({ runs: runs.join(",") });
  if (score) query.set("score", score);
  return `/benchmarks/compare?${query.toString()}`;
}

/** The moment of a time written by the API, or null when it is missing or does not read. */
function moment(text: string | null | undefined): number | null {
  if (text === null || text === undefined || text === "") return null;
  const parsed = Date.parse(text);
  return Number.isNaN(parsed) ? null : parsed;
}

/** A score writes its time to the second, so a run that ended within that second is not newer. */
const SCORE_CLOCK_MS = 1000;

/**
 * When a run last changed: the later of the end the run file gives and the end of the job that
 * made it. A run resumed after it was scored ends again, later than the score. While the job
 * behind the run is waiting, running or being cancelled the run is changing now, so no score
 * made so far holds its latest answers: the result is `Infinity`.
 */
export function runChangedAt(
  run: Pick<RunSummary, "ended" | "state">,
  job: Pick<Job, "ended_at" | "settled"> | null | undefined,
): number | null {
  if (!isScorable(run) || (job != null && !job.settled)) return Number.POSITIVE_INFINITY;
  const times = [moment(run.ended), moment(job?.ended_at)].filter(
    (time): time is number => time !== null,
  );
  return times.length === 0 ? null : Math.max(...times);
}

/** What the screen knows of a run as it is now, to tell whether a score still holds it. */
export interface RunNow {
  /** The md5 of the run file as the API reads it; null when the summary has none. */
  md5: string | null;
  /** When the run last changed (see `runChangedAt`); `Infinity` while it is going. */
  changedAt: number | null;
}

export function runNow(
  run: Pick<RunSummary, "ended" | "state" | "md5">,
  job: Pick<Job, "ended_at" | "settled"> | null | undefined,
): RunNow {
  return { md5: run.md5 ?? null, changedAt: runChangedAt(run, job) };
}

/**
 * True when the score does not hold the run as it is now. A run that is still going is newer than
 * any score. Otherwise the md5 the score recorded for the run is compared with the md5 of the
 * run file now: any difference is an older score, and equal md5 is a current one whatever the
 * clocks say. Only when one of the two md5 is missing does the time rule decide: the score was
 * made more than a second before the run last changed. A run the list does not have is not older.
 */
export function scoreIsOlder(
  score: Pick<ScoreSummary, "created" | "runs">,
  runId: string,
  now: RunNow | undefined,
): boolean {
  if (now === undefined) return false;
  if (now.changedAt === Number.POSITIVE_INFINITY) return true;
  const scored = score.runs.find((run) => run.run_id === runId)?.md5;
  if (scored && now.md5) return scored !== now.md5;
  const made = moment(score.created);
  return made !== null && now.changedAt !== null && made + SCORE_CLOCK_MS < now.changedAt;
}

/**
 * The newest score that holds these runs, and for two runs the pair as (base, variant). With
 * `now` (what each run is now, by run id) a score that does not hold one of its runs as it is now
 * is left out: `stale` is the newest of those, so the screen can say that it exists.
 */
export function findScore(
  scores: readonly ScoreSummary[],
  runs: readonly string[],
  now: Readonly<Record<string, RunNow | undefined>> = {},
): { current: ScoreSummary | null; stale: ScoreSummary | null } {
  const wanted = runs;
  const matches = scores.filter((score) => {
    const held = new Set(score.runs.map((run) => run.run_id));
    if (!wanted.every((id) => held.has(id))) return false;
    if (wanted.length < 2) return true;
    const [base, variant] = wanted;
    return score.pairs.some((pair) => pair.base_run === base && pair.var_run === variant);
  });
  matches.sort((a, b) => b.created.localeCompare(a.created));
  const older = (score: ScoreSummary) => wanted.some((id) => scoreIsOlder(score, id, now[id]));
  return {
    current: matches.find((score) => !older(score)) ?? null,
    stale: matches.find(older) ?? null,
  };
}

/** Runs that are still being written cannot be scored. */
export function isScorable(run: Pick<RunSummary, "state">): boolean {
  return run.state !== "queued" && run.state !== "running" && run.state !== "cancelling";
}

/** A run that stopped before its last answer. */
export function isPartial(run: Pick<RunSummary, "complete">): boolean {
  return !run.complete;
}

export function runLabel(run: Pick<RunSummary, "arm" | "variant" | "model">): string {
  return `${run.arm}${run.variant ? `/${run.variant}` : ""}, ${run.model}`;
}

/**
 * A short name for each run of a comparison: the arm and model, and the end of the run id when
 * two runs would otherwise read the same (two repeats of one arm).
 */
export function runLabels(
  ids: readonly string[],
  summaries: readonly RunSummary[],
  scored: readonly { run_id: string; label?: string | null | undefined }[],
): Record<string, string> {
  const plain = new Map<string, string>();
  for (const id of ids) {
    const summary = summaries.find((run) => run.run_id === id);
    const label = scored.find((run) => run.run_id === id)?.label;
    plain.set(id, summary ? runLabel(summary) : (label ?? id));
  }
  const counts = new Map<string, number>();
  for (const label of plain.values()) counts.set(label, (counts.get(label) ?? 0) + 1);
  const result: Record<string, string> = {};
  for (const [id, label] of plain) {
    result[id] = (counts.get(label) ?? 0) > 1 ? `${label} (${id.slice(-8)})` : label;
  }
  return result;
}

// ---- metrics, groups and tables --------------------------------------------------------

/** The metrics a first look needs, in this order, when the score has them. */
const FIRST_METRICS = ["all_keys", "all_keys_lenient", "key_share", "wall_s"] as const;

/** Metric names present in the groups: the usual ones first, then the rest in alphabetical order. */
export function metricNames(groups: readonly Pick<ScoreGroup, "metric">[]): string[] {
  const present = new Set(groups.map((group) => group.metric));
  const first = FIRST_METRICS.filter((name) => present.has(name));
  const rest = [...present].filter((name) => !first.includes(name as never)).sort();
  return [...first, ...rest];
}

export function defaultMetrics(names: readonly string[]): string[] {
  const chosen = FIRST_METRICS.filter((name) => names.includes(name)).slice(0, 3);
  return chosen.length > 0 ? chosen : names.slice(0, 3);
}

/** `all` first, then `field=value` names in alphabetical order. */
export function groupNames(groups: readonly Pick<ScoreGroup, "group">[]): string[] {
  const names = [...new Set(groups.map((group) => group.group))];
  return names.sort((a, b) => (a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b, "en")));
}

/**
 * The groups the compare screen starts with: `all` and the `content=` and `lang=` groups. A score
 * with none of those (other fields only) starts with every group. The person can tick any group.
 */
export function defaultGroups(names: readonly string[]): string[] {
  const short = names.filter(
    (name) => name === "all" || name.startsWith("content=") || name.startsWith("lang="),
  );
  return short.some((name) => name !== "all") ? short : [...names];
}

export interface SummaryCell {
  mean: number | null;
  ci: [number, number] | null;
  n: number;
}

export interface SummaryRow {
  group: string;
  metric: string;
  cells: Record<string, SummaryCell | undefined>;
}

function interval(ci: ScoreGroup["ci"]): [number, number] | null {
  if (!Array.isArray(ci) || ci.length !== 2) return null;
  const [low, high] = ci;
  return typeof low === "number" && typeof high === "number" ? [low, high] : null;
}

/** One row per group and metric with a cell per run: the mean and its interval, as the scorer wrote them. */
export function summaryRows(
  groups: readonly ScoreGroup[],
  runIds: readonly string[],
  metrics: readonly string[],
  only?: readonly string[],
): SummaryRow[] {
  const wanted = new Set(metrics);
  const keep = only && only.length > 0 ? new Set(only) : null;
  const rows = new Map<string, SummaryRow>();
  for (const record of groups) {
    if (!wanted.has(record.metric) || !runIds.includes(record.run_id)) continue;
    if (keep && !keep.has(record.group)) continue;
    const key = `${record.group}\u0000${record.metric}`;
    const row = rows.get(key) ?? { group: record.group, metric: record.metric, cells: {} };
    row.cells[record.run_id] = { mean: record.mean, ci: interval(record.ci), n: record.n };
    rows.set(key, row);
  }
  const order = groupNames(groups);
  return [...rows.values()].sort(
    (a, b) =>
      order.indexOf(a.group) - order.indexOf(b.group) ||
      metrics.indexOf(a.metric) - metrics.indexOf(b.metric),
  );
}

/** The paired comparisons of (base, variant) for the chosen metrics, in the order of the summary. */
export function compareRows(
  compares: readonly ScoreCompare[],
  base: string,
  variant: string,
  metrics: readonly string[],
  groupOrder: readonly string[],
  only?: readonly string[],
): ScoreCompare[] {
  const wanted = new Set(metrics);
  const keep = only && only.length > 0 ? new Set(only) : null;
  return compares
    .filter(
      (row) =>
        row.base_run === base &&
        row.var_run === variant &&
        wanted.has(row.metric) &&
        (!keep || keep.has(row.group)),
    )
    .sort(
      (a, b) =>
        groupOrder.indexOf(a.group) - groupOrder.indexOf(b.group) ||
        metrics.indexOf(a.metric) - metrics.indexOf(b.metric),
    );
}

// ---- questions -------------------------------------------------------------------------

export interface QuestionCell {
  /** The metric's mean over the repeats that have it, or null when no repeat does. */
  value: number | null;
  repeats: number;
  keysTotal: number;
  /** Mean number of keys found by the exact rule over the repeats. */
  keysFound: number;
  keysLenientOnly: number;
}

export type QuestionCells = Record<string, QuestionCell | undefined>;

function numeric(value: unknown): number | null {
  if (typeof value === "boolean") return value ? 1 : 0;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Per question and run: the chosen metric averaged over the repeats (as the scorer does) and the keys found. */
export function questionCells(
  items: readonly ScoreItem[],
  metric: string,
): Map<string, QuestionCells> {
  interface Sums {
    sum: number;
    count: number;
    repeats: number;
    total: number;
    exact: number;
    lenient: number;
  }
  const sums = new Map<string, Map<string, Sums>>();
  for (const item of items) {
    const byRun = sums.get(item.id) ?? new Map<string, Sums>();
    const cell: Sums = byRun.get(item.run_id) ?? {
      sum: 0,
      count: 0,
      repeats: 0,
      total: 0,
      exact: 0,
      lenient: 0,
    };
    const value = numeric(item.metrics[metric]);
    if (value !== null) {
      cell.sum += value;
      cell.count += 1;
    }
    const keys = Array.isArray(item.keys) ? item.keys : [];
    cell.repeats += 1;
    cell.total += keys.length;
    for (const key of keys) {
      if (key.exact === true) cell.exact += 1;
      else if (key.lenient === true) cell.lenient += 1;
    }
    byRun.set(item.run_id, cell);
    sums.set(item.id, byRun);
  }
  const result = new Map<string, QuestionCells>();
  for (const [id, byRun] of sums) {
    const cells: QuestionCells = {};
    for (const [runId, cell] of byRun) {
      cells[runId] = {
        value: cell.count > 0 ? cell.sum / cell.count : null,
        repeats: cell.repeats,
        keysTotal: cell.repeats > 0 ? cell.total / cell.repeats : 0,
        keysFound: cell.repeats > 0 ? cell.exact / cell.repeats : 0,
        keysLenientOnly: cell.repeats > 0 ? cell.lenient / cell.repeats : 0,
      };
    }
    result.set(id, cells);
  }
  return result;
}

export type QuestionFilter = "all" | "disagree" | "up" | "down";

const EPSILON = 1e-9;

/** Whether a question passes the filter for the pair: `up` the variant scores higher, `down` lower, `disagree` either. */
export function passesFilter(
  cells: QuestionCells | undefined,
  base: string,
  variant: string,
  filter: QuestionFilter,
): boolean {
  if (filter === "all") return true;
  const a = cells?.[base]?.value;
  const b = cells?.[variant]?.value;
  if (a === null || a === undefined || b === null || b === undefined) return false;
  const diff = b - a;
  if (filter === "up") return diff > EPSILON;
  if (filter === "down") return diff < -EPSILON;
  return Math.abs(diff) > EPSILON;
}

export type KeyStatus = "found" | "lenient" | "missed" | "unscored";

export interface KeyOutcome {
  fact: KeyFact;
  status: KeyStatus;
}

/** Each key fact of the question with what the scorer found: exact, only by the lenient rule, missed. */
export function keyOutcomes(
  facts: readonly KeyFact[],
  results: readonly KeyResult[],
): KeyOutcome[] {
  const byIndex = new Map(results.map((result) => [result.index, result]));
  return facts.map((fact, index) => {
    const result = byIndex.get(index);
    if (!result) return { fact, status: "unscored" };
    if (result.exact) return { fact, status: "found" };
    return { fact, status: result.lenient ? "lenient" : "missed" };
  });
}

/** The shortest citation id that stands for a paper: the 16 hex of the short form. */
const MIN_CITED_ID = 16;

/** Which gold papers an answer cites. A short (16 hex) id of a citation matches the long one. */
export function goldCited(
  gold: readonly GoldPaper[],
  cited: readonly { doc_id: string }[],
): { cited: GoldPaper[]; missing: GoldPaper[] } {
  const ids = cited.map((citation) => citation.doc_id);
  const hit = (paper: GoldPaper) =>
    ids.some(
      (id) =>
        id === paper.docId ||
        (Math.min(id.length, paper.docId.length) >= MIN_CITED_ID &&
          (paper.docId.startsWith(id) || id.startsWith(paper.docId))),
    );
  return { cited: gold.filter(hit), missing: gold.filter((paper) => !hit(paper)) };
}

/** `name/v1` of a score's set into its two parts. */
export function splitSetName(setName: string): { name: string; version: string } | null {
  const at = setName.lastIndexOf("/");
  if (at <= 0 || at === setName.length - 1) return null;
  return { name: setName.slice(0, at), version: setName.slice(at + 1) };
}

// ---- numbers and times -----------------------------------------------------------------

/** Up to four decimals, the precision of the iteration records, with no trailing zeros. */
export function formatNumber(value: number, language: string): string {
  return new Intl.NumberFormat(language, { maximumFractionDigits: 4, useGrouping: false }).format(
    value,
  );
}

/** The same with an explicit sign, for a difference: `+0.0198`, `-0.0297`. */
export function formatSigned(value: number, language: string): string {
  const text = formatNumber(Math.abs(value), language);
  if (value === 0 || Number(text.replace(",", ".")) === 0) return text;
  return `${value < 0 ? "-" : "+"}${text}`;
}

export function formatInterval(
  ci: readonly [number, number] | readonly number[] | null | undefined,
  language: string,
  signed = false,
): string {
  if (ci?.length !== 2) return "";
  const [low, high] = ci as [number, number];
  const show = signed ? formatSigned : formatNumber;
  return `[${show(low, language)}, ${show(high, language)}]`;
}

/** `72` as `1 min 12 s`; under a minute `12 s`. */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

/** A moment of the API (ISO with an offset) in the reader's language and time zone. */
export function formatMoment(iso: string | null | undefined, language: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(language, { dateStyle: "short", timeStyle: "medium" }).format(
    date,
  );
}
