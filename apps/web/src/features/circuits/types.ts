/**
 * The shapes of the circuit endpoints. The API passes quantum-sim's `quantum-sim-result/1` and
 * docingest's `circuit/1` through as plain JSON objects (the OpenAPI type is an open object), so
 * the screen types them here and reads every answer through the functions below. An answer that
 * does not have the shape is a `MalformedAnswerError` shown as a problem, never a crash in a
 * component. `fixtures/captured.json` holds real answers; a test reads each of them.
 */

/** A complex number as quantum-sim writes it: `[re, im]`. */
export type Complex = readonly [number, number];

export interface CircuitItem {
  kind: string;
  label: string;
  targets: number[];
  controls: number[];
  open_controls: number[];
  classical: boolean;
  source: string;
}

export type CircuitStep = CircuitItem[];
export type CircuitPart = CircuitStep[];

/** A `circuit/1` document. Only what the screen shows is typed; the rest travels as it is. */
export interface CircuitDocument {
  format: string;
  kind: string;
  status: string;
  label: string;
  wires: number;
  wire_labels: string[];
  parts: CircuitPart[];
}

export type ReadingStatus = "parsed" | "partial" | "unparsed";

/** The answer of `/circuits/parse`. `raw` keeps each circuit as received, for `circuit_json`. */
export interface Reading {
  status: ReadingStatus;
  text: string;
  circuits: CircuitDocument[];
  raw: Record<string, unknown>[];
}

export type SimulatorStatus = "ok" | "symbolic" | "refused";

export interface SimulatedPart {
  part: number;
  wires: number;
  measuredWires: number[];
  state: Complex[] | null;
  probabilities: { wires: number[]; values: number[] } | null;
  unitary: Complex[][] | null;
  sign: string | null;
  operations: number | null;
  parameters: string[];
}

export interface Comparison {
  between: string[];
  equal: boolean;
  exact: boolean;
  phase: number;
  max_deviation: number;
  method: string;
}

export interface SimulatorResult {
  command: "run" | "check";
  status: SimulatorStatus;
  conventions: Record<string, string>;
  input: string | null;
  parameters: string[];
  values: Record<string, number>;
  unbound: string[];
  circuits: SimulatedPart[];
  equalToTarget: boolean | null;
  comparisons: Comparison[];
  notes: string[];
  refused: string[];
}

export interface GateInfo {
  name: string;
  targets: number;
  params: number;
  definition: string;
}

export interface Conventions {
  version: string;
  conventions: Record<string, string>;
  gates: GateInfo[];
  limits: Record<string, number>;
}

export class MalformedAnswerError extends Error {
  constructor(what: string) {
    super(`The server's answer is not a ${what} as expected.`);
    this.name = "MalformedAnswerError";
  }
}

type Record_ = Record<string, unknown>;

function isRecord(value: unknown): value is Record_ {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function numbers(value: unknown): number[] | null {
  return Array.isArray(value) && value.every(isNumber) ? value : null;
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : null;
}

function stringMap(value: unknown): Record<string, string> | null {
  if (!isRecord(value)) return null;
  return Object.values(value).every((item) => typeof item === "string")
    ? (value as Record<string, string>)
    : null;
}

function isComplex(value: unknown): value is Complex {
  return Array.isArray(value) && value.length === 2 && value.every(isNumber);
}

function readItem(value: unknown): CircuitItem | null {
  if (!isRecord(value) || typeof value.kind !== "string") return null;
  const targets = value.targets === undefined ? [] : numbers(value.targets);
  const controls = value.controls === undefined ? [] : numbers(value.controls);
  const open = value.open_controls === undefined ? [] : numbers(value.open_controls);
  if (!targets || !controls || !open) return null;
  return {
    kind: value.kind,
    label: typeof value.label === "string" ? value.label : "",
    targets,
    controls,
    open_controls: open,
    classical: value.classical === true,
    source: typeof value.source === "string" ? value.source : "",
  };
}

/** A `circuit/1` document as the screen needs it, or null when it is not one. */
export function readCircuitDocument(value: unknown): CircuitDocument | null {
  if (!isRecord(value) || !isNumber(value.wires) || !Array.isArray(value.parts)) return null;
  const parts: CircuitPart[] = [];
  for (const part of value.parts as unknown[]) {
    if (!Array.isArray(part)) return null;
    const steps: CircuitPart = [];
    for (const step of part as unknown[]) {
      if (!Array.isArray(step)) return null;
      const items = (step as unknown[]).map(readItem);
      if (items.some((item) => item === null)) return null;
      steps.push(items as CircuitItem[]);
    }
    parts.push(steps);
  }
  const labels = value.wire_labels === undefined ? [] : strings(value.wire_labels);
  if (!labels) return null;
  return {
    format: typeof value.format === "string" ? value.format : "circuit/1",
    kind: typeof value.kind === "string" ? value.kind : "",
    status: typeof value.status === "string" ? value.status : "",
    label: typeof value.label === "string" ? value.label : "",
    wires: value.wires,
    wire_labels: labels,
    parts,
  };
}

export function readReading(value: unknown): Reading {
  if (
    !isRecord(value) ||
    !["parsed", "partial", "unparsed"].includes(value.status as string) ||
    typeof value.text !== "string" ||
    !Array.isArray(value.circuits)
  ) {
    throw new MalformedAnswerError("gate list");
  }
  const circuits: CircuitDocument[] = [];
  const raw: Record<string, unknown>[] = [];
  for (const circuit of value.circuits as unknown[]) {
    const document = readCircuitDocument(circuit);
    if (!document || !isRecord(circuit)) throw new MalformedAnswerError("gate list");
    circuits.push(document);
    raw.push(circuit);
  }
  return { status: value.status as ReadingStatus, text: value.text, circuits, raw };
}

function readPart(value: unknown): SimulatedPart | null {
  if (!isRecord(value) || !isNumber(value.part) || !isNumber(value.wires)) return null;
  let state: Complex[] | null = null;
  if (Array.isArray(value.state)) {
    if (!value.state.every(isComplex)) return null;
    state = value.state;
  }
  let unitary: Complex[][] | null = null;
  if (Array.isArray(value.unitary)) {
    const rows = value.unitary as unknown[];
    if (!rows.every((row) => Array.isArray(row) && (row as unknown[]).every(isComplex))) {
      return null;
    }
    unitary = rows as Complex[][];
  }
  let probabilities: SimulatedPart["probabilities"] = null;
  if (value.probabilities !== undefined && value.probabilities !== null) {
    const p = value.probabilities;
    if (!isRecord(p)) return null;
    const wires = numbers(p.wires);
    const values = numbers(p.values);
    if (!wires || !values) return null;
    probabilities = { wires, values };
  }
  const measured = value.measured_wires === undefined ? [] : numbers(value.measured_wires);
  const parameters = value.parameters === undefined ? [] : strings(value.parameters);
  if (!measured || !parameters) return null;
  return {
    part: value.part,
    wires: value.wires,
    measuredWires: measured,
    state,
    probabilities,
    unitary,
    sign: typeof value.sign === "string" ? value.sign : null,
    operations: isNumber(value.operations) ? value.operations : null,
    parameters,
  };
}

function readComparison(value: unknown): Comparison | null {
  if (!isRecord(value)) return null;
  const between = strings(value.between);
  if (
    !between ||
    typeof value.equal !== "boolean" ||
    typeof value.exact !== "boolean" ||
    !isNumber(value.phase) ||
    !isNumber(value.max_deviation) ||
    typeof value.method !== "string"
  ) {
    return null;
  }
  return {
    between,
    equal: value.equal,
    exact: value.exact,
    phase: value.phase,
    max_deviation: value.max_deviation,
    method: value.method,
  };
}

/** The `quantum-sim-result/1` document of a `run` or a `check`. */
export function readResult(value: unknown): SimulatorResult {
  const fail = () => new MalformedAnswerError("simulator result");
  if (
    !isRecord(value) ||
    value.format !== "quantum-sim-result/1" ||
    (value.command !== "run" && value.command !== "check") ||
    !["ok", "symbolic", "refused"].includes(value.status as string)
  ) {
    throw fail();
  }
  const conventions = stringMap(value.conventions ?? {});
  const parameters = strings(value.parameters ?? []);
  const unbound = strings(value.unbound ?? []);
  const notes = strings(value.notes ?? []);
  const refused = strings(value.refused ?? []);
  if (!conventions || !parameters || !unbound || !notes || !refused) throw fail();
  const values = isRecord(value.values) ? value.values : {};
  if (!Object.values(values).every(isNumber)) throw fail();
  const rawCircuits = value.circuits ?? [];
  const rawComparisons = value.comparisons ?? [];
  if (!Array.isArray(rawCircuits) || !Array.isArray(rawComparisons)) throw fail();
  const circuits = (rawCircuits as unknown[]).map(readPart);
  const comparisons = (rawComparisons as unknown[]).map(readComparison);
  if (circuits.some((part) => part === null) || comparisons.some((item) => item === null)) {
    throw fail();
  }
  const equal = value.equal_to_target;
  if (equal !== null && equal !== undefined && typeof equal !== "boolean") throw fail();
  return {
    command: value.command,
    status: value.status as SimulatorStatus,
    conventions,
    input: typeof value.input === "string" ? value.input : null,
    parameters,
    values: values as Record<string, number>,
    unbound,
    circuits: circuits as SimulatedPart[],
    equalToTarget: equal ?? null,
    comparisons: comparisons as Comparison[],
    notes,
    refused,
  };
}

export function readConventions(value: unknown): Conventions {
  const fail = () => new MalformedAnswerError("list of conventions");
  if (!isRecord(value)) throw fail();
  const conventions = stringMap(value.conventions);
  if (!conventions || !Array.isArray(value.gates) || !isRecord(value.limits)) throw fail();
  const gates: GateInfo[] = [];
  for (const gate of value.gates as unknown[]) {
    if (
      !isRecord(gate) ||
      typeof gate.name !== "string" ||
      !isNumber(gate.targets) ||
      !isNumber(gate.params) ||
      typeof gate.definition !== "string"
    ) {
      throw fail();
    }
    gates.push({
      name: gate.name,
      targets: gate.targets,
      params: gate.params,
      definition: gate.definition,
    });
  }
  const limits: Record<string, number> = {};
  for (const [key, limit] of Object.entries(value.limits)) {
    if (isNumber(limit)) limits[key] = limit;
  }
  return {
    version: typeof value.version === "string" ? value.version : "",
    conventions,
    gates,
    limits,
  };
}
