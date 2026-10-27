import { useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import {
  formatNumber,
  formatSigned,
  passesFilter,
  type QuestionCell,
  type QuestionCells,
  type QuestionFilter,
} from "./domain";
import { SELECT_CLASS } from "./fields";
import { useCompareStore } from "./store";
import type { ItemPage } from "./types";

const PAGE_SIZE = 20;

const FILTERS: readonly QuestionFilter[] = ["all", "disagree", "up", "down"];

function oneDecimal(value: number, language: string): string {
  return new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(value);
}

/** The keys of one answer in words: `1 of 2 found, 1 missing`. Averaged over repeats, so a share may be fractional. */
function KeysText({ cell }: { cell: QuestionCell | undefined }) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.results.questions" });
  if (!cell) return <span className="text-muted-foreground">-</span>;
  if (cell.keysTotal === 0) return <span className="text-muted-foreground">{t("noKeys")}</span>;
  const found = cell.keysFound;
  const missing = Math.max(0, cell.keysTotal - cell.keysFound - cell.keysLenientOnly);
  return (
    <span>
      {t("keys", {
        found: oneDecimal(found, i18n.language),
        total: oneDecimal(cell.keysTotal, i18n.language),
        missing: oneDecimal(missing, i18n.language),
      })}
      {cell.keysLenientOnly > 0 ? (
        <span className="text-muted-foreground">
          {" "}
          {t("lenientOnly", { n: oneDecimal(cell.keysLenientOnly, i18n.language) })}
        </span>
      ) : null}
    </span>
  );
}

/**
 * One row per question of the set: the chosen metric and the keys found for each run, the change
 * from base to variant for two runs, a filter for the questions where the runs disagree.
 */
export function QuestionsTable({
  questions,
  cells,
  runIds,
  labels,
  metric,
}: {
  questions: ItemPage["items"];
  cells: ReadonlyMap<string, QuestionCells>;
  runIds: readonly string[];
  labels: Record<string, string>;
  metric: string;
}) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.results.questions" });
  const filter = useCompareStore((state) => state.filter);
  const text = useCompareStore((state) => state.text);
  const page = useCompareStore((state) => state.page);
  const open = useCompareStore((state) => state.open);
  const setFilter = useCompareStore((state) => state.setFilter);
  const setText = useCompareStore((state) => state.setText);
  const setPage = useCompareStore((state) => state.setPage);
  const openQuestion = useCompareStore((state) => state.openQuestion);
  const ids = useId();
  const base = runIds[0];
  const variant = runIds[1];
  const paired = base !== undefined && variant !== undefined;

  // Only questions this score has an answer for (a partial run answers fewer).
  const listed = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return questions.filter((question) => {
      const row = cells.get(question.id);
      if (!row) return false;
      if (needle && !`${question.id} ${question.question}`.toLowerCase().includes(needle))
        return false;
      return !paired || passesFilter(row, base, variant, filter);
    });
  }, [questions, cells, text, paired, base, variant, filter]);

  const pages = Math.max(1, Math.ceil(listed.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const shown = listed.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor={`${ids}-text`}>{t("search")}</Label>
          <Input
            id={`${ids}-text`}
            type="search"
            className="w-64"
            autoComplete="off"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        {paired ? (
          <div className="grid gap-1.5">
            <Label htmlFor={`${ids}-filter`}>{t("filter")}</Label>
            <select
              id={`${ids}-filter`}
              className={SELECT_CLASS}
              value={filter}
              onChange={(event) =>
                setFilter(FILTERS.find((entry) => entry === event.target.value) ?? "all")
              }
            >
              {FILTERS.map((entry) => (
                <option key={entry} value={entry}>
                  {t(`filters.${entry}`)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <p role="status" className="text-sm text-muted-foreground" data-testid="question-count">
          {t("count", { shown: listed.length, total: cells.size })}
        </p>
      </div>
      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("none")}</p>
      ) : (
        <Table scrollLabel={t("scroll")} data-testid="questions-table">
          <TableCaption>{t("caption", { metric })}</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t("question")}</TableHead>
              {runIds.map((id) => (
                <TableHead key={id} scope="col">
                  {labels[id] ?? id}
                </TableHead>
              ))}
              {paired ? <TableHead scope="col">{t("change")}</TableHead> : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((question) => {
              const row = cells.get(question.id);
              const a = base ? row?.[base]?.value : null;
              const b = variant ? row?.[variant]?.value : null;
              return (
                <TableRow key={question.id} data-question={question.id}>
                  <TableHead scope="row" className="max-w-md font-normal whitespace-normal">
                    <Button
                      type="button"
                      variant="link"
                      size="sm"
                      className="h-auto p-0 font-mono"
                      aria-label={t("open", { id: question.id })}
                      aria-expanded={open === question.id}
                      onClick={() => openQuestion(open === question.id ? null : question.id)}
                    >
                      {question.id}
                    </Button>
                    <span className="block text-muted-foreground">{question.question}</span>
                  </TableHead>
                  {runIds.map((id) => {
                    const cell = row?.[id];
                    return (
                      <TableCell key={id} className="align-top">
                        <span className="block font-medium tabular-nums">
                          {cell && cell.value !== null
                            ? formatNumber(cell.value, i18n.language)
                            : "-"}
                        </span>
                        <span className="block text-xs">
                          <KeysText cell={cell} />
                        </span>
                      </TableCell>
                    );
                  })}
                  {paired ? (
                    <TableCell className="align-top tabular-nums">
                      {a !== null && a !== undefined && b !== null && b !== undefined
                        ? formatSigned(b - a, i18n.language)
                        : "-"}
                    </TableCell>
                  ) : null}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {pages > 1 ? (
        <nav aria-label={t("pagesLabel")} className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={current === 0}
            onClick={() => setPage(current - 1)}
          >
            {t("previous")}
          </Button>
          <span className="text-sm">{t("pageOf", { page: current + 1, pages })}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={current >= pages - 1}
            onClick={() => setPage(current + 1)}
          >
            {t("next")}
          </Button>
        </nav>
      ) : null}
    </div>
  );
}
