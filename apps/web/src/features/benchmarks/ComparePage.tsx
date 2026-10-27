import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useSearchParams } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { isApiError } from "@/shared/api/errors";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";
import { useJob, useResumeJob, useScore, useScores } from "./api";
import {
  canResume,
  compareAddress,
  findScore,
  parseCompareAddress,
  progressShare,
  shortId,
} from "./domain";
import { useJobProgressText } from "./JobsTable";
import { ProblemAlert } from "./ProblemAlert";
import { Results } from "./Results";
import { ScoreStarter } from "./ScoreStarter";
import { Section } from "./Section";
import { StateBadge } from "./StateBadge";

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

/** A score named in the address: wait for its job, then read it. A score without a job (a file dropped in) is read at once. */
function ScoreLoader({ scoreId, runs }: { scoreId: string; runs: readonly string[] }) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.score" });
  const job = useJob(scoreId);
  const noJob = job.isError && isApiError(job.error, 404);
  const ready = job.data?.state === "done" || noJob;
  const score = useScore(scoreId, ready);

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
  return <Results score={score.data} runIds={runs} />;
}

/** Without a score in the address: use the newest one that holds these runs, or offer to make it. */
function ScoreFinder({ runs }: { runs: readonly string[] }) {
  const scores = useScores();
  if (scores.isError) {
    return <ProblemAlert error={scores.error} onRetry={() => void scores.refetch()} />;
  }
  if (!scores.data) return <Skeleton className="h-24 w-full" />;
  const found = findScore(scores.data, runs);
  if (found) return <Navigate replace to={compareAddress(runs, found.score_id)} />;
  return <ScoreStarter runs={runs} />;
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
