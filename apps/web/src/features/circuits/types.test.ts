import captured from "./fixtures/captured.json";
import { EXAMPLES } from "./examples";
import {
  MalformedAnswerError,
  readCircuitDocument,
  readConventions,
  readReading,
  readResult,
} from "./types";

interface Recorded {
  name: string;
  path: string;
  request: Record<string, unknown> | null;
  status: number;
  response: unknown;
}
const recorded = captured as Recorded[];
const ok = (path: string) =>
  recorded.filter((entry) => entry.path === path && entry.status === 200);

describe("the recorded answers of the API", () => {
  it.each(ok("parse").map((entry) => [entry.name, entry.response]))(
    "read as a gate list: %s",
    (_, body) => {
      const reading = readReading(body);
      expect(reading.circuits).toHaveLength(reading.raw.length);
      for (const circuit of reading.circuits) expect(circuit.format).toBe("circuit/1");
    },
  );

  it.each(
    ok("run")
      .concat(ok("check"))
      .map((entry) => [entry.name, entry.response]),
  )("read as a simulator result: %s", (_, body) => {
    const result = readResult(body);
    expect(["ok", "symbolic", "refused"]).toContain(result.status);
    expect(Object.keys(result.conventions).length).toBeGreaterThan(5);
    if (result.status === "refused") {
      expect(result.refused.length).toBeGreaterThan(0);
      expect(result.circuits).toEqual([]);
    }
  });

  it("read the conventions with the gate table", () => {
    const entry = recorded.find((item) => item.path === "conventions");
    const conventions = readConventions(entry?.response);
    expect(conventions.gates.length).toBeGreaterThan(15);
    expect(conventions.gates.find((gate) => gate.name === "rzz")).toMatchObject({
      targets: 2,
      params: 1,
    });
    expect(conventions.limits.statevector_wires).toBe(20);
  });

  it("keep the amplitudes, the probabilities and the unitary of a simulation", () => {
    const entry = recorded.find((item) => item.name === "run bell unitary");
    const [part] = readResult(entry?.response).circuits;
    expect(part?.state).toHaveLength(4);
    expect(part?.probabilities).toEqual({ wires: [1, 2], values: [0.5, 0, 0, 0.5] });
    expect(part?.unitary).toHaveLength(4);
  });

  it("keep a symbolic result's unbound names, and a check's comparison", () => {
    const symbolic = readResult(recorded.find((item) => item.name === "run symbolic")?.response);
    expect(symbolic).toMatchObject({
      status: "symbolic",
      unbound: ["theta"],
      parameters: ["theta"],
    });
    expect(symbolic.circuits[0]?.state).toBeNull();
    const check = readResult(
      recorded.find((item) => item.name === "check bell state not equal")?.response,
    );
    expect(check.equalToTarget).toBe(false);
    expect(check.comparisons[0]).toMatchObject({ equal: false, method: "arrays" });
  });
});

describe("the examples of the screen", () => {
  it("are the circuits that were recorded, so a test of the screen is a test of the examples", () => {
    const requests = new Map(
      recorded
        .filter((entry) => entry.path === "parse")
        .map((entry) => [entry.name, (entry.request as { latex: string }).latex]),
    );
    const names: Record<string, string> = {
      bell: "parse bell",
      ghz: "parse ghz qcircuit",
      rotation: "parse rotation",
      hzhEqualsX: "parse hzh equals x",
      cnotFromCz: "parse cnot from cz",
      qft3: "parse qft3",
      measured: "parse measured",
      partial: "parse partial",
      bundle: "parse bundle",
    };
    for (const example of EXAMPLES.filter((item) => item.kind === "latex")) {
      expect(example.text, example.id).toBe(requests.get(names[example.id] ?? ""));
    }
  });

  it("includes a circuit/1 example that is the recorded one", () => {
    const json = EXAMPLES.find((example) => example.id === "bellJson");
    const entry = recorded.find((item) => item.name === "run bad input");
    expect(json?.text).toBe((entry?.request as { circuit_json: string }).circuit_json);
  });
});

describe("answers that are not what the contract says", () => {
  it("refuses a gate list with a missing or wrong field", () => {
    for (const bad of [
      null,
      "x",
      { status: "weird", text: "", circuits: [] },
      { status: "parsed", circuits: [] },
      { status: "parsed", text: "", circuits: "no" },
      { status: "parsed", text: "", circuits: [{ wires: 1 }] },
      { status: "parsed", text: "", circuits: [{ wires: 1, parts: [[["x"]]] }] },
      {
        status: "parsed",
        text: "",
        circuits: [{ wires: 1, parts: [[[{ kind: "gate", targets: ["a"] }]]] }],
      },
    ]) {
      expect(() => readReading(bad), JSON.stringify(bad)).toThrow(MalformedAnswerError);
    }
  });

  it("refuses a result with a missing or wrong field", () => {
    const base = { format: "quantum-sim-result/1", command: "run", status: "ok" };
    for (const bad of [
      null,
      { ...base, format: "other" },
      { ...base, command: "walk" },
      { ...base, status: "maybe" },
      { ...base, conventions: { a: 1 } },
      { ...base, refused: [1] },
      { ...base, values: { theta: "x" } },
      { ...base, circuits: "no" },
      { ...base, circuits: [{ part: 1 }] },
      { ...base, circuits: [{ part: 1, wires: 1, state: [1] }] },
      { ...base, circuits: [{ part: 1, wires: 1, unitary: [[1]] }] },
      { ...base, circuits: [{ part: 1, wires: 1, probabilities: { wires: [1], values: "no" } }] },
      { ...base, comparisons: [{ between: ["a"], equal: "yes" }] },
      { ...base, equal_to_target: "yes" },
    ]) {
      expect(() => readResult(bad), JSON.stringify(bad)).toThrow(MalformedAnswerError);
    }
  });

  it("accepts the smallest result", () => {
    const result = readResult({
      format: "quantum-sim-result/1",
      command: "check",
      status: "refused",
      refused: ["why"],
    });
    expect(result).toMatchObject({
      command: "check",
      refused: ["why"],
      circuits: [],
      equalToTarget: null,
    });
  });

  it("refuses conventions with a missing or wrong field", () => {
    for (const bad of [
      null,
      {},
      { conventions: { a: "b" }, gates: "no", limits: {} },
      { conventions: { a: "b" }, gates: [{ name: 1 }], limits: {} },
    ]) {
      expect(() => readConventions(bad), JSON.stringify(bad)).toThrow(MalformedAnswerError);
    }
  });

  it("reads a document with no kind, label or legends, and refuses a bad one", () => {
    expect(readCircuitDocument({ wires: 1, parts: [] })).toMatchObject({
      format: "circuit/1",
      wire_labels: [],
    });
    expect(readCircuitDocument({ wires: "1", parts: [] })).toBeNull();
    expect(readCircuitDocument({ wires: 1, parts: [], wire_labels: [1] })).toBeNull();
    expect(readCircuitDocument({ wires: 1, parts: ["x"] })).toBeNull();
  });

  it("says in the error which answer it was", () => {
    expect(() => readReading(null)).toThrow("gate list");
  });
});
