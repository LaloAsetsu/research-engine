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
import { formatInterval, formatNumber, type SummaryRow } from "./domain";

/** Mean and 95% interval of each group and metric for each run, as the scorer wrote them. */
export function SummaryTable({
  rows,
  runIds,
  labels,
}: {
  rows: readonly SummaryRow[];
  runIds: readonly string[];
  labels: Record<string, string>;
}) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "benchmarks.results.summary" });
  return (
    <Table scrollLabel={t("scroll")} data-testid="summary-table">
      <TableCaption>{t("caption")}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t("group")}</TableHead>
          <TableHead scope="col">{t("metric")}</TableHead>
          {runIds.map((id) => (
            <TableHead key={id} scope="col">
              {labels[id] ?? id}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={`${row.group}|${row.metric}`}>
            <TableHead scope="row" className="font-mono text-xs font-normal">
              {row.group}
            </TableHead>
            <TableCell className="font-mono text-xs">{row.metric}</TableCell>
            {runIds.map((id) => {
              const cell = row.cells[id];
              const mean = cell?.mean ?? null;
              if (cell === undefined || mean === null) return <TableCell key={id}>-</TableCell>;
              return (
                <TableCell key={id} className="whitespace-nowrap">
                  <span className="font-medium tabular-nums">
                    {formatNumber(mean, i18n.language)}
                  </span>{" "}
                  {cell.ci ? (
                    <span className="text-muted-foreground tabular-nums">
                      {formatInterval(cell.ci, i18n.language)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t("noInterval")}</span>
                  )}{" "}
                  <span className="text-xs text-muted-foreground">{t("n", { n: cell.n })}</span>
                </TableCell>
              );
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
