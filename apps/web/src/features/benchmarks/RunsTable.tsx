import { ArrowLeftRight } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { Badge } from "@/shared/ui/badge";
import { Button, buttonVariants } from "@/shared/ui/button";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import { compareAddress, formatMoment, isPartial, isScorable } from "./domain";
import { CHECKBOX_CLASS } from "./fields";
import { StateBadge } from "./StateBadge";
import type { RunSummary } from "./types";

/**
 * The runs, finished and partial. Pick one to see its results, or two to compare them: the first
 * picked is the base and the second the variant (the difference is variant minus base).
 */
export function RunsTable({ runs }: { runs: readonly RunSummary[] }) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.runs" });
  const [picked, setPicked] = useState<string[]>([]);
  const hintId = useId();
  const byId = new Map(runs.map((run) => [run.run_id, run]));
  const chosen = picked.flatMap((id) => {
    const run = byId.get(id);
    return run ? [run] : [];
  });
  const sameSet =
    chosen.length < 2 ||
    chosen.every(
      (run) => run.set_md5 === chosen[0]?.set_md5 && run.set_name === chosen[0]?.set_name,
    );

  const toggle = (id: string, on: boolean) =>
    setPicked((current) =>
      on ? [...current, id].slice(0, 2) : current.filter((entry) => entry !== id),
    );

  const base = chosen[0];
  const variant = chosen[1];
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-3" aria-live="polite">
        {chosen.length === 0 ? (
          <p id={hintId} className="text-sm text-muted-foreground">
            {t("pickHint")}
          </p>
        ) : (
          <>
            <p id={hintId} className="text-sm">
              {chosen.length === 1 || !variant || !base
                ? t("pickedOne", { run: chosen[0]?.run_id ?? "" })
                : t("pickedTwo", { base: base.run_id, variant: variant.run_id })}
            </p>
            {chosen.length === 2 ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPicked((current) => [...current].reverse())}
              >
                <ArrowLeftRight aria-hidden="true" />
                {t("swap")}
              </Button>
            ) : null}
            {sameSet ? (
              <Link
                className={buttonVariants({ size: "sm" })}
                to={compareAddress(chosen.map((run) => run.run_id))}
              >
                {chosen.length === 1 ? t("results") : t("compare")}
              </Link>
            ) : (
              <p className="text-sm text-destructive">{t("differentSets")}</p>
            )}
            <Button type="button" variant="ghost" size="sm" onClick={() => setPicked([])}>
              {t("clear")}
            </Button>
          </>
        )}
      </div>
      <Table scrollLabel={t("scroll")}>
        <TableCaption>{t("caption")}</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">
              <span className="sr-only">{t("pick")}</span>
            </TableHead>
            <TableHead scope="col">{t("run")}</TableHead>
            <TableHead scope="col">{t("set")}</TableHead>
            <TableHead scope="col">{t("arm")}</TableHead>
            <TableHead scope="col">{t("model")}</TableHead>
            <TableHead scope="col">{t("answers")}</TableHead>
            <TableHead scope="col">{t("state")}</TableHead>
            <TableHead scope="col">{t("started")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => {
            const isChecked = picked.includes(run.run_id);
            const scorable = isScorable(run);
            const full = picked.length >= 2 && !isChecked;
            return (
              <TableRow key={run.run_id} data-run={run.run_id}>
                <TableCell>
                  <input
                    type="checkbox"
                    className={CHECKBOX_CLASS}
                    aria-label={t("pickRun", { run: run.run_id })}
                    checked={isChecked}
                    disabled={!scorable || full}
                    aria-describedby={hintId}
                    onChange={(event) => toggle(run.run_id, event.target.checked)}
                  />
                </TableCell>
                <TableHead scope="row" className="font-mono text-xs font-normal">
                  {run.job_id ? (
                    <Link
                      className="underline underline-offset-4"
                      to={`/benchmarks/jobs/${run.job_id}`}
                    >
                      {run.run_id}
                    </Link>
                  ) : (
                    run.run_id
                  )}
                </TableHead>
                <TableCell>{run.set_name}</TableCell>
                <TableCell>
                  <span className="font-mono">{run.arm}</span>
                  {run.variant ? <span className="font-mono">/{run.variant}</span> : null}
                </TableCell>
                <TableCell>{run.model}</TableCell>
                <TableCell>
                  {run.expected !== null && run.expected !== undefined
                    ? t("answersOf", { done: run.answers, total: run.expected })
                    : run.answers}
                  {run.errors > 0 ? (
                    <span className="text-muted-foreground">
                      {" "}
                      {t("errors", { count: run.errors })}
                    </span>
                  ) : null}
                </TableCell>
                <TableCell>
                  <span className="flex flex-wrap items-center gap-1">
                    {run.state ? <StateBadge state={run.state} /> : null}
                    {isPartial(run) && scorable ? (
                      <Badge variant="outline">{t("partial")}</Badge>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell>{formatMoment(run.started ?? run.created, i18n.language)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
