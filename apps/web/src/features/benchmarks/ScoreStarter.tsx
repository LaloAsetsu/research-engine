import { ArrowLeftRight } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { Alert, AlertDescription } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { Skeleton } from "@/shared/ui/skeleton";
import { useRuns, useStartScore } from "./api";
import { compareAddress, formatMoment, isScorable, runLabel, splitSetName } from "./domain";
import { SELECT_CLASS } from "./fields";
import { ProblemAlert } from "./ProblemAlert";
import { Section } from "./Section";
import type { ScoreSummary } from "./types";

/**
 * Runs that have not been scored together yet. Scoring is a job of the CPU lane (a few seconds);
 * its result is kept, so the next visit opens at once.
 */
export function ScoreStarter({
  runs,
  older = null,
}: {
  runs: readonly string[];
  /** A score of these runs made before a run last changed: left aside, but it can still be opened. */
  older?: ScoreSummary | null;
}) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.score" });
  const navigate = useNavigate();
  const runsQuery = useRuns(false);
  const start = useStartScore();
  const ids = useId();
  const [swapped, setSwapped] = useState(false);
  const [noiseA, setNoiseA] = useState("");
  const [noiseB, setNoiseB] = useState("");

  if (runsQuery.isError) {
    return <ProblemAlert error={runsQuery.error} onRetry={() => void runsQuery.refetch()} />;
  }
  if (!runsQuery.data) return <Skeleton className="h-32 w-full" />;

  const summaries = runs.flatMap((id) => runsQuery.data.filter((run) => run.run_id === id));
  if (summaries.length !== runs.length) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{t("unknownRun")}</AlertDescription>
      </Alert>
    );
  }
  const first = summaries[0];
  const same = summaries.every(
    (run) => run.set_md5 === first?.set_md5 && run.set_name === first?.set_name,
  );
  const set = first ? splitSetName(first.set_name) : null;
  if (!first || !set || !same) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{t("differentSets")}</AlertDescription>
      </Alert>
    );
  }
  const open = summaries.filter((run) => !isScorable(run));
  if (open.length > 0) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{t("stillRunning", { run: open[0]?.run_id ?? "" })}</AlertDescription>
      </Alert>
    );
  }

  const ordered = swapped ? [...summaries].reverse() : summaries;
  const base = ordered[0];
  const variant = ordered[1];
  const candidates = runsQuery.data.filter(
    (run) =>
      run.set_md5 === first.set_md5 &&
      run.set_name === first.set_name &&
      isScorable(run) &&
      run.arm === base?.arm,
  );
  const noiseInvalid = (noiseA === "") !== (noiseB === "") || (noiseA !== "" && noiseA === noiseB);

  const submit = () => {
    if (!base || noiseInvalid) return;
    const noise = noiseA !== "" ? ([noiseA, noiseB] as [string, string]) : null;
    const scored = [...new Set([...ordered.map((run) => run.run_id), ...(noise ?? [])])];
    start.mutate(
      {
        set,
        bootstrap_engine: "python_random",
        number_words: false,
        runs: scored,
        pairs: variant ? [[base.run_id, variant.run_id]] : [],
        ...(noise ? { noise } : {}),
      },
      {
        onSuccess: (job) =>
          void navigate(
            compareAddress(
              ordered.map((run) => run.run_id),
              job.id,
            ),
            { replace: true },
          ),
      },
    );
  };

  return (
    <Section
      title={t("title")}
      description={variant ? t("descriptionPair") : t("descriptionOne")}
      id="score"
    >
      <div className="grid gap-4">
        {start.error ? <ProblemAlert error={start.error} /> : null}
        {older ? (
          <Alert role="status">
            <AlertDescription>
              <p>{t("olderExists", { when: formatMoment(older.created, i18n.language) })}</p>
              <p>
                <Link
                  className="underline underline-offset-4"
                  to={compareAddress(runs, older.score_id)}
                >
                  {t("olderOpen")}
                </Link>
              </p>
            </AlertDescription>
          </Alert>
        ) : null}
        <dl className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-0.5">
            <dt className="text-xs text-muted-foreground">{variant ? t("base") : t("run")}</dt>
            <dd>
              {base ? runLabel(base) : ""} <span className="font-mono text-xs">{base?.run_id}</span>
            </dd>
          </div>
          {variant ? (
            <div className="grid gap-0.5">
              <dt className="text-xs text-muted-foreground">{t("variant")}</dt>
              <dd>
                {runLabel(variant)} <span className="font-mono text-xs">{variant.run_id}</span>
              </dd>
            </div>
          ) : null}
        </dl>
        {variant ? (
          <div>
            <Button type="button" variant="outline" size="sm" onClick={() => setSwapped(!swapped)}>
              <ArrowLeftRight aria-hidden="true" />
              {t("swap")}
            </Button>
          </div>
        ) : null}
        {variant ? (
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">{t("noise")}</legend>
            <p className="max-w-prose text-sm text-muted-foreground">{t("noiseHint")}</p>
            <div className="flex flex-wrap items-end gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor={`${ids}-a`}>{t("noiseFirst")}</Label>
                <select
                  id={`${ids}-a`}
                  className={SELECT_CLASS}
                  value={noiseA}
                  onChange={(event) => setNoiseA(event.target.value)}
                >
                  <option value="">{t("noNoise")}</option>
                  {candidates.map((run) => (
                    <option key={run.run_id} value={run.run_id}>
                      {run.run_id}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`${ids}-b`}>{t("noiseSecond")}</Label>
                <select
                  id={`${ids}-b`}
                  className={SELECT_CLASS}
                  value={noiseB}
                  onChange={(event) => setNoiseB(event.target.value)}
                >
                  <option value="">{t("noNoise")}</option>
                  {candidates.map((run) => (
                    <option key={run.run_id} value={run.run_id}>
                      {run.run_id}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {noiseInvalid ? (
              <p role="alert" className="text-sm text-destructive">
                {t("noiseInvalid")}
              </p>
            ) : null}
          </fieldset>
        ) : null}
        <div>
          <Button type="button" disabled={start.isPending || noiseInvalid} onClick={submit}>
            {start.isPending ? t("starting") : variant ? t("startPair") : t("startOne")}
          </Button>
        </div>
      </div>
    </Section>
  );
}
