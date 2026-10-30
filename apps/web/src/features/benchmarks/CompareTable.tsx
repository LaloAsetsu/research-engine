import { useTranslation } from "react-i18next";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import { formatInterval, formatNumber, formatSigned } from "./domain";
import type { ScoreCompare } from "./types";

/**
 * The paired bootstrap of variant minus base, one row per group and metric, with the numbers the
 * scorer wrote: the difference, its 95% interval, how many questions went up and down and, when
 * a noise pair was scored, the floor and whether the difference counts.
 */
export function CompareTable({
  rows,
  baseLabel,
  variantLabel,
}: {
  rows: readonly ScoreCompare[];
  baseLabel: string;
  variantLabel: string;
}) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.results.compare" });
  const hasFloor = rows.some((row) => row.floor !== null && row.floor !== undefined);
  return (
    <Table scrollLabel={t("scroll")} data-testid="compare-table">
      <TableCaption>{t("caption", { base: baseLabel, variant: variantLabel })}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t("group")}</TableHead>
          <TableHead scope="col">{t("metric")}</TableHead>
          <TableHead scope="col">{t("n")}</TableHead>
          <TableHead scope="col">{t("base")}</TableHead>
          <TableHead scope="col">{t("variant")}</TableHead>
          <TableHead scope="col">{t("diff")}</TableHead>
          <TableHead scope="col">{t("interval")}</TableHead>
          <TableHead scope="col">{t("up")}</TableHead>
          <TableHead scope="col">{t("down")}</TableHead>
          {hasFloor ? <TableHead scope="col">{t("floor")}</TableHead> : null}
          {hasFloor ? <TableHead scope="col">{t("counts")}</TableHead> : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={`${row.group}|${row.metric}`}>
            <TableHead scope="row" className="font-mono text-xs font-normal">
              {row.group}
            </TableHead>
            <TableCell className="font-mono text-xs">{row.metric}</TableCell>
            <TableCell className="tabular-nums">{row.n}</TableCell>
            <TableCell className="tabular-nums">{formatNumber(row.base, i18n.language)}</TableCell>
            <TableCell className="tabular-nums">{formatNumber(row.var, i18n.language)}</TableCell>
            <TableCell className="font-medium whitespace-nowrap tabular-nums">
              {formatSigned(row.diff, i18n.language)}
            </TableCell>
            <TableCell className="whitespace-nowrap tabular-nums">
              {formatInterval(row.ci, i18n.language, true)}
            </TableCell>
            <TableCell className="tabular-nums">{row.up}</TableCell>
            <TableCell className="tabular-nums">{row.down}</TableCell>
            {hasFloor ? (
              <TableCell className="tabular-nums">
                {row.floor === null || row.floor === undefined
                  ? "-"
                  : formatNumber(row.floor, i18n.language)}
              </TableCell>
            ) : null}
            {hasFloor ? (
              <TableCell>
                {row.counts === null || row.counts === undefined
                  ? "-"
                  : row.counts
                    ? t("countsYes")
                    : t("countsNo")}
              </TableCell>
            ) : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
