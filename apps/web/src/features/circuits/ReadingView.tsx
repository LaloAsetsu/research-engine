import { CircleCheck, CircleDashed, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Badge } from "@/shared/ui/badge";
import { Label } from "@/shared/ui/label";
import { RadioGroup, RadioGroupItem } from "@/shared/ui/radio-group";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import { gateName, gateParts, unreadRows, type GateRow } from "./gateList";
import { Section } from "./Section";
import { useCircuitStore } from "./store";
import type { CircuitDocument, ReadingStatus } from "./types";

/** An outlined badge with the destructive color: the tinted destructive variant is under 4.5:1 at this size. */
const FAILED = "border-destructive text-destructive";

const STATUS_ICON = {
  parsed: CircleCheck,
  partial: TriangleAlert,
  unparsed: CircleDashed,
} as const;

export function StatusBadge({ status }: { status: ReadingStatus }) {
  const { t } = useTranslation();
  const Icon = STATUS_ICON[status];
  return (
    <Badge
      variant={status === "parsed" ? "default" : status === "partial" ? "outline" : "destructive"}
    >
      <Icon aria-hidden="true" />
      {t(`circuits.reading.status.${status}`)}
    </Badge>
  );
}

function Wires({ list }: { list: number[] }) {
  const { t } = useTranslation();
  if (list.length > 0) return <>{list.join(", ")}</>;
  return (
    <>
      <span aria-hidden="true">{"\u2013"}</span>
      <span className="sr-only">{t("circuits.reading.none")}</span>
    </>
  );
}

function GateCell({ row }: { row: GateRow }) {
  const { t } = useTranslation();
  if (row.unread) {
    return (
      <span className="grid gap-0.5">
        <Badge variant="outline" className={`w-fit ${FAILED}`}>
          {t("circuits.reading.notRead")}
        </Badge>
        {row.source ? <code className="text-xs break-all">{row.source}</code> : null}
      </span>
    );
  }
  const name = gateName(row);
  return (
    <span>
      <span className="font-mono">{name.text}</span>
      {name.key ? (
        <span className="ml-1.5 text-muted-foreground">{t(`circuits.kinds.${name.key}`)}</span>
      ) : null}
      {row.classical ? (
        <Badge variant="outline" className="ml-1.5">
          {t("circuits.reading.classical")}
        </Badge>
      ) : null}
    </span>
  );
}

function GateTables({ circuit, index }: { circuit: CircuitDocument; index: number }) {
  const { t } = useTranslation();
  const parts = gateParts(circuit);
  return (
    <div className="grid gap-4">
      {parts.map((part, partIndex) => {
        const side =
          parts.length === 1
            ? t("circuits.reading.captionOne", { n: index + 1 })
            : t(
                partIndex === 0 ? "circuits.reading.captionLeft" : "circuits.reading.captionRight",
                {
                  n: index + 1,
                  sign: part.sign ?? "=",
                },
              );
        return (
          <Table key={partIndex} data-testid="gate-table">
            <TableCaption>{side}</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">{t("circuits.reading.step")}</TableHead>
                <TableHead scope="col">{t("circuits.reading.gate")}</TableHead>
                <TableHead scope="col">{t("circuits.reading.targets")}</TableHead>
                <TableHead scope="col">{t("circuits.reading.controls")}</TableHead>
                <TableHead scope="col">{t("circuits.reading.openControls")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {part.rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    {t("circuits.reading.noGates")}
                  </TableCell>
                </TableRow>
              ) : (
                part.rows.map((row, rowIndex) => (
                  <TableRow key={rowIndex}>
                    <TableCell>{row.step}</TableCell>
                    <TableCell>
                      <GateCell row={row} />
                    </TableCell>
                    <TableCell>
                      <Wires list={row.targets} />
                    </TableCell>
                    <TableCell>
                      <Wires list={row.controls} />
                    </TableCell>
                    <TableCell>
                      <Wires list={row.openControls} />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        );
      })}
    </div>
  );
}

function UnreadList({ circuit }: { circuit: CircuitDocument }) {
  const { t } = useTranslation();
  const rows = unreadRows(circuit);
  if (rows.length === 0 && circuit.status !== "partial") return null;
  return (
    <Alert data-testid="unread">
      <TriangleAlert aria-hidden="true" />
      <AlertTitle>{t("circuits.reading.unreadTitle")}</AlertTitle>
      <AlertDescription className="grid gap-1">
        <p>{t("circuits.reading.unreadBody")}</p>
        {rows.length > 0 ? (
          <ul className="list-disc pl-5">
            {rows.map((row, index) => (
              <li key={index}>
                {t("circuits.reading.unreadItem", {
                  step: row.step,
                  wires: row.targets.join(", "),
                })}{" "}
                {row.source ? <code className="break-all">{row.source}</code> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

export function ReadingView() {
  const { t } = useTranslation();
  const reading = useCircuitStore((state) => state.reading);
  const selected = useCircuitStore((state) => state.selected);
  const select = useCircuitStore((state) => state.selectCircuit);
  if (!reading) return null;
  const circuit = reading.circuits[selected] ?? reading.circuits[0];
  return (
    <Section
      title={t("circuits.reading.title")}
      action={<StatusBadge status={reading.status} />}
      description={t(`circuits.reading.explain.${reading.status}`)}
    >
      <div aria-live="polite" className="grid gap-4">
        {reading.circuits.length > 1 ? (
          <RadioGroup
            aria-label={t("circuits.reading.choose")}
            value={String(selected)}
            onValueChange={(value) => select(Number(value))}
          >
            {reading.circuits.map((item, index) => (
              <div key={index} className="flex items-center gap-2">
                <RadioGroupItem value={String(index)} id={`circuit-choice-${index}`} />
                <Label htmlFor={`circuit-choice-${index}`}>
                  {t("circuits.reading.choice", {
                    n: index + 1,
                    kind: item.kind || "circuit/1",
                    wires: item.wires,
                    status: t(
                      `circuits.reading.status.${item.status === "partial" ? "partial" : "parsed"}`,
                    ),
                  })}
                </Label>
              </div>
            ))}
          </RadioGroup>
        ) : null}
        {circuit ? (
          <>
            {circuit.wire_labels.length > 0 ? (
              <div>
                <h3 className="mb-1 text-sm font-medium">
                  {t("circuits.reading.wires", { count: circuit.wires })}
                </h3>
                <ol className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-muted-foreground">
                  {circuit.wire_labels.map((label) => (
                    <li key={label}>{label}</li>
                  ))}
                </ol>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("circuits.reading.wiresPlain", { count: circuit.wires })}
              </p>
            )}
            <UnreadList circuit={circuit} />
            <GateTables circuit={circuit} index={selected} />
          </>
        ) : (
          <p>{t("circuits.reading.nothing")}</p>
        )}
        {reading.text ? (
          <details>
            <summary className="cursor-pointer text-sm font-medium">
              {t("circuits.reading.words")}
            </summary>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-muted p-3 text-xs whitespace-pre-wrap">
              {reading.text}
            </pre>
          </details>
        ) : null}
      </div>
    </Section>
  );
}
