import { CircleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { isApiError } from "@/shared/api/errors";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button, buttonVariants } from "@/shared/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/shared/ui/popover";
import { Skeleton } from "@/shared/ui/skeleton";
import { useCancelJob, useJob, useMe, useResumeJob } from "./api";
import {
  canCancel,
  canResume,
  compareAddress,
  elapsedSeconds,
  formatDuration,
  formatMoment,
  isJobId,
  progressShare,
  shortId,
} from "./domain";
import { useJobName, useJobProgressText } from "./JobsTable";
import { LogTail } from "./LogTail";
import { ProblemAlert } from "./ProblemAlert";
import { Section } from "./Section";
import { StateBadge } from "./StateBadge";
import type { Job } from "./types";

/** The clock, once a second while `running`. Not announced: a ticking number would never stop talking. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  return now;
}

function CancelButton({
  job,
  pending,
  onConfirm,
}: {
  job: Job;
  pending: boolean;
  onConfirm: () => void;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.job" });
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" disabled={pending}>
          {t("cancel")}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" aria-labelledby="cancel-title" className="w-80 gap-3">
        <h2 id="cancel-title" className="font-medium">
          {t("cancelTitle")}
        </h2>
        <p className="text-muted-foreground">
          {job.state === "queued" ? t("cancelQueued") : t("cancelRunning")}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setOpen(false);
              onConfirm();
            }}
          >
            {t("cancelConfirm")}
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => setOpen(false)}>
            {t("cancelKeep")}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function JobView({ job }: { job: Job }) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.job" });
  const { t: tState } = useTranslation(undefined, { keyPrefix: "benchmarks.state" });
  const me = useMe();
  const cancelState = useCancelJob();
  const resume = useResumeJob();
  const progressText = useJobProgressText();
  const now = useNow(job.state === "running" || job.state === "cancelling");
  const elapsed = elapsedSeconds(job, now);
  const share = progressShare(job);
  const mayControl =
    me.data !== undefined && (me.data.username === job.owner || me.data.role === "admin");
  const run = job.run;
  const results =
    run &&
    (job.state === "done" ||
      ((job.state === "cancelled" || job.state === "interrupted") && job.progress.done > 0))
      ? compareAddress([run.run_id])
      : job.score && job.state === "done"
        ? compareAddress(
            job.score.pairs[0] ? [...job.score.pairs[0]] : job.score.runs.slice(0, 1),
            job.score.score_id,
          )
        : null;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <Section title={t("progress")} id="progress">
        <div className="grid gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <StateBadge state={job.state} />
            <p role="status" className="text-sm" data-testid="job-status">
              {tState(job.state)}. {progressText(job)}
              {job.state === "cancelling" ? ` ${t("cancellingNote")}` : ""}
            </p>
          </div>
          <div
            role="progressbar"
            aria-label={t("progressLabel")}
            aria-valuemin={0}
            aria-valuemax={Math.max(job.progress.total, 1)}
            aria-valuenow={job.progress.done}
            className="h-2 w-full overflow-hidden rounded-full bg-muted"
          >
            <div
              className="h-full bg-primary transition-[width] motion-reduce:transition-none"
              style={{ width: `${Math.round(share * 100)}%` }}
            />
          </div>
          {job.state === "queued" && job.waiting_for ? (
            <p className="text-sm text-muted-foreground">
              {t("waitingFor")} {job.waiting_for}
            </p>
          ) : null}
          {job.error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertTitle>{t("failedTitle")}</AlertTitle>
              <AlertDescription>
                <p>{job.error.message}</p>
                <p className="font-mono text-xs">{job.error.kind}</p>
              </AlertDescription>
            </Alert>
          ) : null}
          {cancelState.error ? <ProblemAlert error={cancelState.error} /> : null}
          {resume.error ? <ProblemAlert error={resume.error} /> : null}
          <div className="flex flex-wrap items-center gap-2">
            {mayControl && canCancel(job) ? (
              <CancelButton
                job={job}
                pending={cancelState.isPending}
                onConfirm={() => cancelState.mutate(job.id)}
              />
            ) : null}
            {mayControl && canResume(job) ? (
              <Button
                type="button"
                disabled={resume.isPending}
                onClick={() => resume.mutate(job.id)}
              >
                {t("resume")}
              </Button>
            ) : null}
            {results ? (
              <Link className={buttonVariants({ variant: "outline" })} to={results}>
                {job.score ? t("openScore") : t("seeResults")}
              </Link>
            ) : null}
            {!mayControl && (canCancel(job) || canResume(job)) && me.data ? (
              <p className="text-sm text-muted-foreground">{t("notYours", { owner: job.owner })}</p>
            ) : null}
          </div>
        </div>
      </Section>

      <Section title={t("details")} id="details">
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Fact label={t("kind")}>{t(`kinds.${job.kind}`)}</Fact>
          <Fact label={t("set")}>
            {job.set.name} {job.set.version}
          </Fact>
          {run ? (
            <>
              <Fact label={t("arm")}>
                <span className="font-mono">{run.arm.name}</span>
                {run.arm.variant ? <span className="font-mono">/{run.arm.variant}</span> : null}
              </Fact>
              <Fact label={t("model")}>{run.model}</Fact>
              <Fact label={t("repeats")}>{run.repeats}</Fact>
              <Fact label={t("runId")}>
                <span className="font-mono text-xs">{run.run_id}</span>
              </Fact>
              {run.notes ? <Fact label={t("notes")}>{run.notes}</Fact> : null}
            </>
          ) : null}
          {job.score ? (
            <Fact label={t("runsScored")}>
              <span className="font-mono text-xs">{job.score.runs.join(", ")}</span>
            </Fact>
          ) : null}
          <Fact label={t("owner")}>{job.owner}</Fact>
          <Fact label={t("created")}>{formatMoment(job.created_at, i18n.language)}</Fact>
          <Fact label={t("started")}>{formatMoment(job.started_at, i18n.language) || "-"}</Fact>
          <Fact label={t("ended")}>{formatMoment(job.ended_at, i18n.language) || "-"}</Fact>
          <Fact label={t("elapsed")}>{elapsed === null ? "-" : formatDuration(elapsed)}</Fact>
        </dl>
      </Section>

      <LogTail jobId={job.id} settled={job.settled} />
    </div>
  );
}

export function JobPage() {
  const { t } = useTranslation();
  const { jobId = "" } = useParams();
  const valid = isJobId(jobId);
  const job = useJob(valid ? jobId : undefined);
  const name = useJobName();
  const notFound = !valid || (job.isError && isApiError(job.error, 404));
  const title = job.data
    ? name(job.data)
    : t("benchmarks.job.titleLoading", { id: valid ? shortId(jobId) : "" });
  return (
    <>
      <PageHeader title={title}>
        <p>
          <Link className="text-sm underline underline-offset-4" to="/benchmarks">
            {t("benchmarks.job.back")}
          </Link>
        </p>
      </PageHeader>
      {notFound ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>{t("benchmarks.job.notFoundTitle")}</AlertTitle>
          <AlertDescription>{t("benchmarks.job.notFound")}</AlertDescription>
        </Alert>
      ) : job.isError ? (
        <ProblemAlert error={job.error} onRetry={() => void job.refetch()} />
      ) : job.data ? (
        <JobView job={job.data} />
      ) : (
        <Skeleton className="h-40 w-full" />
      )}
    </>
  );
}

export { JobPage as Component };
