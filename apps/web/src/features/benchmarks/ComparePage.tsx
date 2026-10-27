import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useSearchParams } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { isApiError } from "@/shared/api/errors";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button, buttonVariants } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";
import { useJob, useResumeJob, useRunJobs, useRuns, useScore, useScores } from "./api";
import {
  canResume,
  compareAddress,
  findScore,
  formatMoment,
  parseCompareAddress,
  progressShare,
  runChangedAt,
  scoreIsOlder,
  shortId,
} from "./domain";
import { useJobProgressText } from "./JobsTable";
import { ProblemAlert } from "./ProblemAlert";
import { Results } from "./Results";
import { ScoreStarter } from "./ScoreStarter";
import { Section } from "./Section";
import { StateBadge } from "./StateBadge";
import type { RunSummary } from "./types";

/** A scoring that is waiting, running or has stopped: the same words and bar as a run. */
function Scoring({ scoreId }: { scoreId: string }) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.score" });
  const { t: tState } = useTranslation(undefined, { keyPrefix: "benchmarks.state" });
  const job = useJob(scoreId);
  const resume = useResumeJob();
  const progressText = useJobProgressText();
  if (!job.data) return <Skeleton className="h-24 w-full" />;
  const data = job.data;
  return (
    <Section title={t("scoring")} description={t("scoringHint")} id="scoring">
      <div className="flex flex-wrap items-center gap-3">
        <StateBadge state={data.state} />
        <p role="status" className="text-sm">
          {tState(data.state)}. {progressText(data)}
        </p>
      </div>
      <div
        role="progressbar"
        aria-label={t("progress")}
        aria-valuemin={0}
        aria-valuemax={Math.max(data.progress.total, 1)}
        aria-valuenow={data.progress.done}
        className="h-2 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full bg-primary transition-[width] motion-reduce:transition-none"
          style={{ width: `${Math.round(progressShare(data) * 100)}%` }}
        />
      </div>
      {data.error ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>{t("failed")}</AlertTitle>
          <AlertDescription>{data.error.message}</AlertDescription>
        </Alert>
      ) : null}
      {resume.error ? <ProblemAlert error={resume.error} /> : null}
      <div className="flex flex-wrap items-center gap-2">
        {canResume(data) ? (
          <Button type="button" disabled={resume.isPending} onClick={() => resume.mutate(data.id)}>
            {t("resume")}
          </Button>
        ) : null}
        <Link className="text-sm underline underline-offset-4" to={`/benchmarks/jobs/${data.id}`}>
          {t("jobPage", { id: shortId(data.id) })}
        </Link>
      </div>
    </Section>
  );
}

/**
 * When each run of the address last changed (by run id), read from the run lists and the jobs
 * that made them. A run resumed after it was scored ends again, later than the score.
 */
function useChangedAt(runs: readonly string[]) {
  const list = useRuns(false);
  const mine = list.data ? runs.flatMap((id) => list.data.filter((run) => run.run_id === id)) : [];
  const jobs = useRunJobs(mine);
  const changedAt: Record<string, number | null> = {};
  mine.forEach((run: RunSummary, index) => {
    changedAt[run.run_id] = runChangedAt(run, jobs.jobs[index]);
  });
  return {
    changedAt,
    pending: list.isPending || jobs.pending,
    // a failed background read keeps the list it had: only a list never read is a problem
    error: list.data ? null : list.error,
    retry: () => void list.refetch(),
  };
}

/** A score made before one of its runs last changed: say so, and offer to score the runs again. */
function OlderScoreNotice({ runs, created }: { runs: readonly string[]; created: string }) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.score" });
  return (
    <Alert role="status">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{t("olderTitle")}</AlertTitle>
      <AlertDescription>
        <p>{t("older", { when: formatMoment(created, i18n.language) })}</p>
        <p>
          <Link
            className={buttonVariants({ variant: "outline", size: "sm" })}
            to={compareAddress(runs)}
          >
            {t("olderAgain")}
          </Link>
        </p>
      </AlertDescription>
    </Alert>
  );
}

/** A score named in the address: wait for its job, then read it. A score without a job (a file dropped in) is read at once. */
function ScoreLoader({ scoreId, runs }: { scoreId: string; runs: readonly string[] }) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.score" });
  const job = useJob(scoreId);
  const noJob = job.isError && isApiError(job.error, 404);
  const ready = job.data?.state === "done" || noJob;
  const score = useScore(scoreId, ready);
  const { changedAt, error: checkError } = useChangedAt(runs);

  if (job.isError && !noJob) {
    return <ProblemAlert error={job.error} onRetry={() => void job.refetch()} />;
  }
  if (job.data && job.data.kind !== "score") {
    return (
      <Alert variant="destructive">
        <AlertDescription>{t("notAScore")}</AlertDescription>
      </Alert>
    );
  }
  if (job.data && job.data.state !== "done") return <Scoring scoreId={scoreId} />;
  if (score.isError) {
    return isApiError(score.error, 404) ? (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertTitle>{t("unknownScoreTitle")}</AlertTitle>
        <AlertDescription>{t("unknownScore")}</AlertDescription>
      </Alert>
    ) : (
      <ProblemAlert error={score.error} onRetry={() => void score.refetch()} />
    );
  }
  if (!score.data) return <Skeleton className="h-40 w-full" />;
  const held = new Set(score.data.summary.runs.map((run) => run.run_id));
  if (!runs.every((id) => held.has(id))) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{t("runsNotInScore")}</AlertDescription>
      </Alert>
    );
  }
  const older = runs.some((id) => scoreIsOlder(score.data.summary, changedAt[id] ?? null));
  return (
    <>
      {older ? <OlderScoreNotice runs={runs} created={score.data.summary.created} /> : null}
      {checkError ? (
        <Alert role="status">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{t("cannotCheck")}</AlertDescription>
        </Alert>
      ) : null}
      <Results score={score.data} runIds={runs} />
    </>
  );
}

/**
 * Without a score in the address: use the newest one that holds these runs and was made after
 * they last changed, or offer to make it.
 */
function ScoreFinder({ runs }: { runs: readonly string[] }) {
  const scores = useScores();
  const { changedAt, pending, error, retry } = useChangedAt(runs);
  if (scores.isError && !scores.data) {
    return <ProblemAlert error={scores.error} onRetry={() => void scores.refetch()} />;
  }
  // Without the run list it cannot be told whether a score holds the latest answers of the runs.
  if (error) return <ProblemAlert error={error} onRetry={retry} />;
  if (!scores.data || pending) return <Skeleton className="h-24 w-full" />;
  const { current, stale } = findScore(scores.data, runs, changedAt);
  if (current) return <Navigate replace to={compareAddress(runs, current.score_id)} />;
  return <ScoreStarter runs={runs} older={stale} />;
}

export function ComparePage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const address = parseCompareAddress(params);
  const title =
    address.runs.length > 1 ? t("benchmarks.compare.titlePair") : t("benchmarks.compare.titleOne");

  return (
    <>
      <PageHeader title={title}>
        <p>
          <Link className="text-sm underline underline-offset-4" to="/benchmarks">
            {t("benchmarks.job.back")}
          </Link>
        </p>
        {address.runs.length > 0 ? (
          <p className="font-mono text-xs break-all text-muted-foreground">
            {address.runs.join("  /  ")}
          </p>
        ) : null}
      </PageHeader>
      {address.problem === "none" ? (
        <Alert>
          <AlertDescription>
            {t("benchmarks.compare.none")}{" "}
            <Link className="underline underline-offset-4" to="/benchmarks#runs">
              {t("benchmarks.compare.pick")}
            </Link>
          </AlertDescription>
        </Alert>
      ) : address.problem === "invalid" || address.problem === "tooMany" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>{t("benchmarks.compare.badAddress")}</AlertTitle>
          <AlertDescription>{t(`benchmarks.compare.${address.problem}`)}</AlertDescription>
        </Alert>
      ) : address.problem === "invalidScore" ? (
        <Navigate replace to={compareAddress(address.runs)} />
      ) : address.score ? (
        <ScoreLoader scoreId={address.score} runs={address.runs} />
      ) : (
        <ScoreFinder runs={address.runs} />
      )}
    </>
  );
}

export { ComparePage as Component };
