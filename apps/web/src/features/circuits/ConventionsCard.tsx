import { useTranslation } from "react-i18next";
import { Skeleton } from "@/shared/ui/skeleton";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import { useConventions } from "./api";
import { Section } from "./Section";
import { useCircuitStore } from "./store";

/** The keys the simulator documents, in the order the screen lists them; others follow as the server gives them. */
const KNOWN = [
  "wire_order",
  "wire_numbers",
  "rotations",
  "two_qubit_rotations",
  "phase",
  "sqrt_x",
  "qft_rk",
  "controls",
  "equality",
  "input_state",
  "step_order",
  "measurement",
  "source",
];

export function ConventionsCard() {
  const { t, i18n } = useTranslation();
  const query = useConventions();
  const used = useCircuitStore((state) => state.outcome?.result.conventions);
  const conventions = used && Object.keys(used).length > 0 ? used : query.data?.conventions;
  const keys = conventions
    ? [
        ...KNOWN.filter((key) => key in conventions),
        ...Object.keys(conventions).filter((key) => !KNOWN.includes(key)),
      ]
    : [];
  const limits = query.data?.limits;
  return (
    <Section
      id="circuit-conventions"
      title={t("circuits.conventions.title")}
      description={
        used ? t("circuits.conventions.ofResult") : t("circuits.conventions.ofSimulator")
      }
    >
      {conventions ? (
        <dl className="grid gap-x-6 gap-y-2 text-sm md:grid-cols-[14rem_1fr]">
          {keys.map((key) => (
            <div key={key} className="contents">
              <dt className="font-medium">
                {i18n.exists(`circuits.conventions.keys.${key}`)
                  ? t(`circuits.conventions.keys.${key}`)
                  : key}
              </dt>
              <dd className="text-muted-foreground">{conventions[key]}</dd>
            </div>
          ))}
        </dl>
      ) : query.isError ? (
        <p className="text-sm text-muted-foreground">{t("circuits.conventions.unavailable")}</p>
      ) : (
        <Skeleton className="h-24 w-full" />
      )}
      {limits ? (
        <p className="text-sm text-muted-foreground">
          {t("circuits.conventions.limits", {
            statevector: limits.statevector_wires ?? "?",
            unitary: limits.unitary_wires ?? "?",
            ops:
              limits.ops === undefined
                ? "?"
                : new Intl.NumberFormat(i18n.language).format(limits.ops),
          })}
        </p>
      ) : null}
      <p className="text-sm text-muted-foreground">{t("circuits.conventions.apiCaps")}</p>
      <details id="circuit-gate-table">
        <summary className="cursor-pointer text-sm font-medium">
          {t("circuits.conventions.gateTable", { count: query.data?.gates.length ?? 0 })}
        </summary>
        {query.data ? (
          <Table
            data-testid="gate-table-reference"
            scrollLabel={t("circuits.conventions.gateTableCaption")}
          >
            <TableCaption>{t("circuits.conventions.gateTableCaption")}</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">{t("circuits.conventions.gate")}</TableHead>
                <TableHead scope="col">{t("circuits.conventions.targets")}</TableHead>
                <TableHead scope="col">{t("circuits.conventions.params")}</TableHead>
                <TableHead scope="col">{t("circuits.conventions.definition")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.gates.map((gate) => (
                <TableRow key={gate.name}>
                  <TableCell className="font-mono">{gate.name}</TableCell>
                  <TableCell>{gate.targets}</TableCell>
                  <TableCell>{gate.params}</TableCell>
                  <TableCell className="font-mono text-xs">{gate.definition}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </details>
    </Section>
  );
}
