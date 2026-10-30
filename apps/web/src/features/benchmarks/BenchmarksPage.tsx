import { useTranslation } from "react-i18next";
import { PageHeader } from "@/shared/layout/PageHeader";
import { Skeleton } from "@/shared/ui/skeleton";
import { useJobs, useRuns, useServiceStatus } from "./api";
import { JobsTable } from "./JobsTable";
import { ProblemAlert } from "./ProblemAlert";
import { RunForm } from "./RunForm";
import { RunsTable } from "./RunsTable";
import { Section } from "./Section";

export function BenchmarksPage() {
  const { t } = useTranslation();
  const jobs = useJobs();
  const open = jobs.data?.filter((job) => !job.settled).length ?? 0;
  const runs = useRuns(open > 0);
  const status = useServiceStatus();
  return (
    <>
      <PageHeader title={t("pages.benchmarks.title")}>
        <p className="max-w-prose text-muted-foreground">{t("benchmarks.intro")}</p>
        {status.data?.mode === "fake" ? (
          <p className="max-w-prose text-sm text-muted-foreground">{t("benchmarks.fakeNote")}</p>
        ) : null}
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        <RunForm />
        <Section
          title={t("benchmarks.jobs.title")}
          description={t("benchmarks.jobs.description")}
          id="jobs"
        >
          <p role="status" className="text-sm text-muted-foreground">
            {jobs.data ? t("benchmarks.jobs.open", { count: open }) : ""}
          </p>
          {jobs.isError ? (
            <ProblemAlert error={jobs.error} onRetry={() => void jobs.refetch()} />
          ) : jobs.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : jobs.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("benchmarks.jobs.empty")}</p>
          ) : (
            <JobsTable jobs={jobs.data} />
          )}
        </Section>
        <Section
          title={t("benchmarks.runs.title")}
          description={t("benchmarks.runs.description")}
          id="runs"
        >
          {runs.isError ? (
            <ProblemAlert error={runs.error} onRetry={() => void runs.refetch()} />
          ) : runs.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : runs.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("benchmarks.runs.empty")}</p>
          ) : (
            <RunsTable runs={runs.data} />
          )}
        </Section>
      </div>
    </>
  );
}

export { BenchmarksPage as Component };
