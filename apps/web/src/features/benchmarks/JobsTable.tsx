import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TableCaption,
} from "@/shared/ui/table";
import { formatMoment, jobTitleParts, shortId } from "./domain";
import { StateBadge } from "./StateBadge";
import type { Job } from "./types";

/** The text that names a job in a list: the arm and model of a run, or "scoring". */
export function useJobName() {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.jobs" });
  return (job: Job): string => {
    const parts = jobTitleParts(job);
    const name = parts
      ? t("runName", { arm: parts.arm, model: parts.model })
      : t("scoreName", { count: job.score?.runs.length ?? 0 });
    return `${name} (${shortId(job.id)})`;
  };
}

/** Where a job stands in words: the position of a waiting run, the answers of a running one. */
export function useJobProgressText() {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.jobs" });
  return (job: Job): string => {
    const { done, total } = job.progress;
    if (job.state === "queued") {
      if (job.position === null || job.position === undefined) return t("waiting");
      return job.position === 0 ? t("next") : t("ahead", { count: job.position });
    }
    return t("answers", { done, total });
  };
}

/** The jobs, newest first. Each links to its page; the state is a word. */
export function JobsTable({ jobs }: { jobs: readonly Job[] }) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.jobs" });
  const name = useJobName();
  const progress = useJobProgressText();
  return (
    <Table scrollLabel={t("scroll")}>
      <TableCaption>{t("caption")}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t("job")}</TableHead>
          <TableHead scope="col">{t("set")}</TableHead>
          <TableHead scope="col">{t("state")}</TableHead>
          <TableHead scope="col">{t("progress")}</TableHead>
          <TableHead scope="col">{t("owner")}</TableHead>
          <TableHead scope="col">{t("created")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((job) => (
          <TableRow key={job.id}>
            <TableHead scope="row" className="font-normal">
              <Link className="underline underline-offset-4" to={`/benchmarks/jobs/${job.id}`}>
                {name(job)}
              </Link>
            </TableHead>
            <TableCell>
              {job.set.name} {job.set.version}
            </TableCell>
            <TableCell>
              <StateBadge state={job.state} />
            </TableCell>
            <TableCell>{progress(job)}</TableCell>
            <TableCell>{job.owner}</TableCell>
            <TableCell>{formatMoment(job.created_at, i18n.language)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
