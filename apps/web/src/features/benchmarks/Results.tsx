import { useEffect, useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Skeleton } from "@/shared/ui/skeleton";
import { Label } from "@/shared/ui/label";
import { useRuns, useScoreItems, useSetQuestions } from "./api";
import { CompareTable } from "./CompareTable";
import {
  compareRows,
  defaultMetrics,
  groupNames,
  metricNames,
  questionCells,
  runLabels,
  splitSetName,
  summaryRows,
} from "./domain";
import { CHECKBOX_CLASS, SELECT_CLASS } from "./fields";
import { ProblemAlert } from "./ProblemAlert";
import { QuestionsTable } from "./QuestionsTable";
import { Section } from "./Section";
import { SideBySide } from "./SideBySide";
import { useCompareStore } from "./store";
import { SummaryTable } from "./SummaryTable";
import type { Score } from "./types";

function Choice({
  legend,
  options,
  chosen,
  onChange,
  summary,
}: {
  legend: string;
  options: readonly string[];
  chosen: readonly string[];
  onChange: (next: string[]) => void;
  summary: string;
}) {
  return (
    <details className="rounded-lg border px-3 py-2">
      <summary className="cursor-pointer text-sm font-medium">{summary}</summary>
      <fieldset className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
        <legend className="sr-only">{legend}</legend>
        {options.map((option) => (
          <label key={option} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className={CHECKBOX_CLASS}
              checked={chosen.includes(option)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? options.filter((name) => name === option || chosen.includes(name))
                    : chosen.filter((name) => name !== option),
                )
              }
            />
            <span className="font-mono text-xs">{option}</span>
          </label>
        ))}
      </fieldset>
    </details>
  );
}

/**
 * A score: the mean and interval of each group for each run, the paired comparison of two runs,
 * a row per question and, for the question that is open, the runs side by side. The numbers are
 * the scorer's, as the API returns them; nothing is computed again here.
 */
export function Results({ score, runIds }: { score: Score; runIds: readonly string[] }) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.results" });
  const scoreId = score.summary.score_id;
  const names = useMemo(() => metricNames(score.groups), [score.groups]);
  const order = useMemo(() => groupNames(score.groups), [score.groups]);
  const metricsChosen = useCompareStore((state) => state.metrics);
  const focusChosen = useCompareStore((state) => state.focus);
  const groups = useCompareStore((state) => state.groups);
  const open = useCompareStore((state) => state.open);
  const setMetrics = useCompareStore((state) => state.setMetrics);
  const setFocus = useCompareStore((state) => state.setFocus);
  const setGroups = useCompareStore((state) => state.setGroups);
  const metrics = metricsChosen ?? defaultMetrics(names);
  const focus =
    focusChosen && names.includes(focusChosen)
      ? focusChosen
      : (metrics[0] ?? names[0] ?? "all_keys");
  const ids = useId();

  const runsQuery = useRuns(false);
  const labels = useMemo(
    () => runLabels(runIds, runsQuery.data ?? [], score.summary.runs),
    [runIds, runsQuery.data, score.summary.runs],
  );
  const [base, variant] = runIds;
  const paired = base !== undefined && variant !== undefined;
  const hasPair =
    paired &&
    score.summary.pairs.some((pair) => pair.base_run === base && pair.var_run === variant);

  const set = splitSetName(score.summary.set_name);
  const questions = useSetQuestions(set?.name, set?.version);
  const firstItems = useScoreItems(scoreId, base);
  const secondItems = useScoreItems(scoreId, variant);
  const items = useMemo(
    () => [...(firstItems.data ?? []), ...(secondItems.data ?? [])],
    [firstItems.data, secondItems.data],
  );
  const cells = useMemo(() => questionCells(items, focus), [items, focus]);
  const repeats = useMemo(() => Math.max(1, ...items.map((item) => item.repeat)), [items]);

  // A new score starts from the default choices.
  const reset = useCompareStore((state) => state.reset);
  useEffect(() => {
    reset();
    return reset;
  }, [scoreId, reset]);

  const summary = useMemo(
    () => summaryRows(score.groups, runIds, metrics, groups),
    [score.groups, runIds, metrics, groups],
  );
  const compared = useMemo(
    () => (hasPair ? compareRows(score.compares, base, variant, metrics, order, groups) : []),
    [hasPair, score.compares, base, variant, metrics, order, groups],
  );

  const itemsError = firstItems.error ?? secondItems.error ?? questions.error;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <Section title={t("choose.title")} description={t("choose.description")} id="choose">
        <div className="flex flex-wrap items-start gap-3">
          <Choice
            legend={t("choose.metrics")}
            summary={t("choose.metricsSummary", { chosen: metrics.length, total: names.length })}
            options={names}
            chosen={metrics}
            onChange={setMetrics}
          />
          <Choice
            legend={t("choose.groups")}
            summary={
              groups.length === 0
                ? t("choose.groupsAll", { total: order.length })
                : t("choose.groupsSome", { chosen: groups.length, total: order.length })
            }
            options={order}
            chosen={groups.length === 0 ? order : groups}
            onChange={(next) => setGroups(next.length === order.length ? [] : next)}
          />
          <div className="grid gap-1.5">
            <Label htmlFor={`${ids}-focus`}>{t("choose.focus")}</Label>
            <select
              id={`${ids}-focus`}
              className={SELECT_CLASS}
              value={focus}
              onChange={(event) => setFocus(event.target.value)}
            >
              {names.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          {t("choose.scorer", {
            set: score.summary.set_name,
            md5: score.summary.set_md5.slice(0, 8),
            records: score.summary.records,
          })}
        </p>
        {typeof score.header.notes === "string" && score.header.notes !== "" ? (
          <details className="text-sm">
            <summary className="cursor-pointer font-medium">{t("choose.notes")}</summary>
            <p className="mt-2 max-w-prose whitespace-pre-wrap text-muted-foreground">
              {score.header.notes}
            </p>
          </details>
        ) : null}
      </Section>

      <Section title={t("summary.title")} description={t("summary.description")} id="summary">
        {summary.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("summary.empty")}</p>
        ) : (
          <SummaryTable rows={summary} runIds={runIds} labels={labels} />
        )}
      </Section>

      {paired ? (
        <Section title={t("compare.title")} description={t("compare.description")} id="compare">
          {!hasPair ? (
            <p className="text-sm text-destructive">{t("compare.noPair")}</p>
          ) : compared.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("compare.empty")}</p>
          ) : (
            <CompareTable
              rows={compared}
              baseLabel={labels[base] ?? base}
              variantLabel={labels[variant] ?? variant}
            />
          )}
        </Section>
      ) : null}

      <Section title={t("questions.title")} description={t("questions.description")} id="questions">
        {itemsError ? (
          <ProblemAlert error={itemsError} />
        ) : questions.isPending ||
          firstItems.isPending ||
          (variant !== undefined && secondItems.isPending) ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <QuestionsTable
            questions={questions.data ?? []}
            cells={cells}
            runIds={runIds}
            labels={labels}
            metric={focus}
          />
        )}
      </Section>

      {open ? (
        <SideBySide
          scoreId={scoreId}
          setName={score.summary.set_name}
          questionId={open}
          runIds={runIds}
          labels={labels}
          repeats={repeats}
        />
      ) : null}
    </div>
  );
}
