import type { CircuitDocument, CircuitItem } from "./types";

/** One drawn item of a circuit, as a row of the gate list. */
export interface GateRow {
  step: number;
  kind: string;
  label: string;
  targets: number[];
  controls: number[];
  openControls: number[];
  classical: boolean;
  /** What the reader saw but did not understand: the circuit cannot be simulated with it. */
  unread: boolean;
  source: string;
}

export interface GatePart {
  /** The sign that opens this part of a drawn equality (`=`), null for the first part. */
  sign: string | null;
  rows: GateRow[];
}

export function isUnread(item: CircuitItem): boolean {
  return item.kind.startsWith("unparsed");
}

/** The parts of a circuit as rows. A `sep` item is the sign of its part, not a row. */
export function gateParts(circuit: CircuitDocument): GatePart[] {
  return circuit.parts.map((steps) => {
    let sign: string | null = null;
    const rows: GateRow[] = [];
    steps.forEach((items, index) => {
      for (const item of items) {
        if (item.kind === "sep") {
          sign = item.label || "=";
          continue;
        }
        rows.push({
          step: index + 1,
          kind: item.kind,
          label: item.label,
          targets: item.targets,
          controls: item.controls,
          openControls: item.open_controls,
          classical: item.classical,
          unread: isUnread(item),
          source: item.source,
        });
      }
    });
    return { sign, rows };
  });
}

/** Every unread item of a circuit, with its part and step, for the list of unread parts. */
export function unreadRows(circuit: CircuitDocument): (GateRow & { part: number })[] {
  return gateParts(circuit).flatMap((part, index) =>
    part.rows.filter((row) => row.unread).map((row) => ({ ...row, part: index + 1 })),
  );
}

/** The name shown in the Gate column for kinds whose label is empty or generic. */
export function gateName(row: Pick<GateRow, "kind" | "label" | "controls" | "openControls">): {
  key: string | null;
  text: string;
} {
  const closed = row.controls.length;
  const controlled = closed + row.openControls.length;
  switch (row.kind) {
    case "gate":
      return { key: null, text: row.label };
    case "x":
      if (closed === 1 && controlled === 1) return { key: "cnot", text: "X" };
      if (closed === 2 && controlled === 2) return { key: "toffoli", text: "X" };
      return { key: null, text: "X" };
    case "swap":
      return controlled === 1 && closed === 1
        ? { key: "fredkin", text: "SWAP" }
        : { key: null, text: "SWAP" };
    case "z_dots":
      return { key: "zDots", text: "Z" };
    case "measure":
      return { key: "measure", text: row.label };
    case "barrier":
      return { key: "barrier", text: row.label };
    case "text":
      return { key: "text", text: row.label };
    default:
      return { key: null, text: row.label || row.kind };
  }
}
