import { Check, CircleAlert, CircleHelp, Minus, X } from "lucide-react";
import { useEffect, useId } from "react";
import { useTranslation } from "react-i18next";
import { MarkdownMath } from "@/shared/markdown/MarkdownMath";
import { Alert, AlertDescription } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { Skeleton } from "@/shared/ui/skeleton";
import { useQuestionDetail, useRunQuestion, useScoreItems } from "./api";
import { formatNumber, goldCited, keyOutcomes, splitSetName, type KeyStatus } from "./domain";
import { CHECKBOX_CLASS, SELECT_CLASS } from "./fields";
import { MathExpr, MathText } from "./MathText";
import { ProblemAlert } from "./ProblemAlert";
import { Section } from "./Section";
import { useCompareStore } from "./store";
import { readKeyResults, type QuestionDetail, type RunItem } from "./types";

const KEY_ICON = { found: Check, lenient: Minus, missed: X, unscored: CircleHelp } as const;
const KEY_TONE: Record<KeyStatus, string> = {
  found: "text-emerald-700 dark:text-emerald-400",
  lenient: "text-amber-700 dark:text-amber-400",
  missed: "text-destructive",
  unscored: "text-muted-foreground",
};

function seconds(value: number | null | undefined, language: string): string {
  return value === null || value === undefined
    ? "-"
    : `${new Intl.NumberFormat(language, { maximumFractionDigits: 2 }).format(value)} s`;
}

/** Keys the scorer looked for in one answer: found, found only by the lenient rule, missed. */
function KeysList({
  detail,
  keys,
}: {
  detail: QuestionDetail | null | undefined;
  keys: ReturnType<typeof readKeyResults>;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.results.side" });
  if (!detail) return <p className="text-sm text-muted-foreground">{t("keysUnknown")}</p>;
  if (detail.keyFacts.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("noKeys")}</p>;
  }
  const outcomes = keyOutcomes(detail.keyFacts, keys);
  const found = outcomes.filter((outcome) => outcome.status === "found").length;
  const missing = outcomes.filter((outcome) => outcome.status === "missed").length;
  return (
    <div className="grid gap-1">
      <p className="text-sm" data-testid="keys-summary">
        {t("keysSummary", { found, total: outcomes.length, missing })}
      </p>
      <ul className="grid gap-1">
        {outcomes.map((outcome, index) => {
          const Icon = KEY_ICON[outcome.status];
          return (
            <li
              key={index}
              className="flex items-start gap-2 text-sm"
              data-key-status={outcome.status}
            >
              <Icon
                aria-hidden="true"
                className={`mt-0.5 size-4 shrink-0 ${KEY_TONE[outcome.status]}`}
              />
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-1">
                <span className="font-medium">{t(`keyStatus.${outcome.status}`)}:</span>
                {outcome.fact.type === "expr" ? (
                  <MathExpr className="max-w-full min-w-0">{outcome.fact.value}</MathExpr>
                ) : outcome.fact.value.includes("$") ? (
                  <MathText className="max-w-full min-w-0">{outcome.fact.value}</MathText>
                ) : (
                  <span className="font-mono">{outcome.fact.value}</span>
                )}
                {outcome.fact.unit ? <span>{outcome.fact.unit}</span> : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ArmColumn({
  label,
  runId,
  questionId,
  repeat,
  scoreId,
  detail,
}: {
  label: string;
  runId: string;
  questionId: string;
  repeat: number;
  scoreId: string;
  detail: QuestionDetail | null | undefined;
}) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.results.side" });
  const answers = useRunQuestion(runId, questionId);
  const scored = useScoreItems(scoreId, runId);
  const headingId = useId();
  const item: RunItem | undefined = answers.data?.items.find((entry) => entry.repeat === repeat);
  const keys = readKeyResults(
    scored.data?.find((entry) => entry.id === questionId && entry.repeat === repeat)?.keys,
  );
  const cited = item && detail ? goldCited(detail.goldPapers, item.citations) : null;

  return (
    <section
      aria-labelledby={headingId}
      className="grid min-w-0 content-start gap-4 rounded-lg border p-4"
      data-run={runId}
    >
      <h3 id={headingId} className="font-medium">
        {label}
      </h3>
      {answers.isError ? (
        <ProblemAlert error={answers.error} onRetry={() => void answers.refetch()} />
      ) : answers.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : !item ? (
        <p className="text-sm text-muted-foreground">{t("noAnswer", { repeat })}</p>
      ) : (
        <>
          {item.error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("errorKind", { kind: item.error.kind })}</p>
                {item.error.message ? <p>{item.error.message}</p> : null}
              </AlertDescription>
            </Alert>
          ) : null}
          <div className="grid gap-1">
            <h4 className="text-sm font-medium">{t("answer")}</h4>
            {item.answer ? (
              <MarkdownMath className="text-sm">{item.answer}</MarkdownMath>
            ) : (
              <p className="text-sm text-muted-foreground">{t("emptyAnswer")}</p>
            )}
          </div>
          <div className="grid gap-1">
            <h4 className="text-sm font-medium">{t("keys")}</h4>
            <KeysList detail={detail} keys={keys} />
          </div>
          <div className="grid gap-1">
            <h4 className="text-sm font-medium">{t("gold")}</h4>
            {cited && detail ? (
              <>
                <p className="text-sm" data-testid="gold-summary">
                  {detail.goldPapers.length > 0
                    ? t("goldSummary", {
                        cited: cited.cited.length,
                        total: detail.goldPapers.length,
                      })
                    : t("noGold")}
                </p>
                {cited.missing.length > 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("goldMissing")}{" "}
                    {cited.missing.map((paper) => paper.doc ?? paper.docId.slice(0, 16)).join(", ")}
                  </p>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">{t("keysUnknown")}</p>
            )}
            <h5 className="mt-1 text-sm text-muted-foreground">
              {t("citations", { count: item.citations.length })}
            </h5>
            {item.citations.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {item.citations.map((citation, index) => (
                  <li key={index} className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs">
                    {citation.key}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer font-medium">
              {t("contexts", { count: item.contexts.length })}
            </summary>
            {item.contexts.length === 0 ? (
              <p className="mt-2 text-muted-foreground">{t("noContexts")}</p>
            ) : (
              <ol className="mt-2 grid gap-2">
                {item.contexts.map((context, index) => (
                  <li key={index} className="rounded-md border p-2">
                    <p className="font-mono text-xs text-muted-foreground">
                      {context.name ?? context.doc_id.slice(0, 16)}
                      {context.score !== null && context.score !== undefined
                        ? `, ${t("score")} ${formatNumber(context.score, i18n.language)}`
                        : ""}
                      {context.is_reference ? `, ${t("referenceList")}` : ""}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap">{context.text}</p>
                  </li>
                ))}
              </ol>
            )}
          </details>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-muted-foreground">{t("wall")}</dt>
              <dd className="tabular-nums">{seconds(item.timings?.wall_s, i18n.language)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">{t("retrieve")}</dt>
              <dd className="tabular-nums">{seconds(item.timings?.retrieve_s, i18n.language)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">{t("rerank")}</dt>
              <dd className="tabular-nums">{seconds(item.timings?.rerank_s, i18n.language)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">{t("write")}</dt>
              <dd className="tabular-nums">{seconds(item.timings?.answer_s, i18n.language)}</dd>
            </div>
          </dl>
        </>
      )}
    </section>
  );
}

/**
 * One question, the runs side by side: what each answered, which keys it hit and missed, which
 * gold papers it cited, what the model read and how long it took. The repeat is the same for
 * every column.
 */
export function SideBySide({
  scoreId,
  setName,
  questionId,
  runIds,
  labels,
  repeats,
}: {
  scoreId: string;
  setName: string;
  questionId: string;
  runIds: readonly string[];
  labels: Record<string, string>;
  repeats: number;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.results.side" });
  const hidden = useCompareStore((state) => state.hidden);
  const repeat = useCompareStore((state) => state.repeat);
  const toggleHidden = useCompareStore((state) => state.toggleHidden);
  const setRepeat = useCompareStore((state) => state.setRepeat);
  const openQuestion = useCompareStore((state) => state.openQuestion);
  const ids = useId();
  const set = splitSetName(setName);
  const detail = useQuestionDetail(set?.name, set?.version, questionId);

  // A question opened from the table takes the reader here; closing it gives the table back.
  useEffect(() => {
    document.querySelector<HTMLElement>("#side-by-side h2")?.focus();
  }, [questionId]);
  const close = () => {
    openQuestion(null);
    window.setTimeout(() => {
      Array.from(document.querySelectorAll<HTMLElement>("[data-question]"))
        .find((row) => row.dataset.question === questionId)
        ?.querySelector("button")
        ?.focus();
    }, 0);
  };

  const shown = runIds.filter((id) => !hidden.includes(id));
  return (
    <Section
      title={t("title", { id: questionId })}
      id="side-by-side"
      action={
        <Button type="button" variant="outline" size="sm" onClick={close}>
          {t("close")}
        </Button>
      }
    >
      {detail.isError ? <ProblemAlert error={detail.error} /> : null}
      {detail.data ? (
        <div className="grid gap-1">
          <MathText className="max-w-prose">{detail.data.question}</MathText>
          {detail.data.answer ? (
            <div className="flex max-w-prose items-baseline gap-1 text-sm text-muted-foreground">
              <span className="font-medium">{t("reference")}:</span>
              <MathText className="min-w-0">{detail.data.answer}</MathText>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-end gap-4">
        {repeats > 1 ? (
          <div className="grid gap-1.5">
            <Label htmlFor={`${ids}-repeat`}>{t("repeat")}</Label>
            <select
              id={`${ids}-repeat`}
              className={SELECT_CLASS}
              value={repeat}
              onChange={(event) => setRepeat(Number(event.target.value))}
            >
              {Array.from({ length: repeats }, (_, index) => index + 1).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {runIds.length > 1 ? (
          <fieldset className="flex flex-wrap items-center gap-3">
            <legend className="mb-1 text-sm font-medium">{t("arms")}</legend>
            {runIds.map((id) => (
              <label key={id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className={CHECKBOX_CLASS}
                  checked={!hidden.includes(id)}
                  disabled={!hidden.includes(id) && shown.length === 1}
                  onChange={() => toggleHidden(id)}
                />
                {labels[id] ?? id}
              </label>
            ))}
          </fieldset>
        ) : null}
      </div>
      <div className={`grid gap-4 ${shown.length > 1 ? "lg:grid-cols-2" : ""}`}>
        {shown.map((id) => (
          <ArmColumn
            key={id}
            label={labels[id] ?? id}
            runId={id}
            questionId={questionId}
            repeat={repeat}
            scoreId={scoreId}
            detail={detail.data}
          />
        ))}
      </div>
    </Section>
  );
}
