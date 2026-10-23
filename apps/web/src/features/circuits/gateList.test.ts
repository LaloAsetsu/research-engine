import { gateName, gateParts, unreadRows } from "./gateList";
import { readCircuitDocument, type CircuitDocument } from "./types";
import captured from "./fixtures/captured.json";

function circuitOf(name: string): CircuitDocument {
  const entry = captured.find((item) => item.name === name);
  const response = entry?.response as { circuits: unknown[] };
  const document = readCircuitDocument(response.circuits[0]);
  if (!document) throw new Error(`fixture ${name} has no circuit`);
  return document;
}

describe("gateParts", () => {
  it("lists a Bell pair step by step", () => {
    const [part] = gateParts(circuitOf("parse bell"));
    expect(part?.sign).toBeNull();
    expect(
      part?.rows.map((row) => [row.step, row.kind, row.label, row.targets, row.controls]),
    ).toEqual([
      [1, "gate", "H", [1], []],
      [2, "x", "X", [2], [1]],
    ]);
  });

  it("makes the sign of a drawn equality the sign of its second part, not a row", () => {
    const parts = gateParts(circuitOf("parse hzh equals x"));
    expect(parts).toHaveLength(2);
    expect(parts[1]?.sign).toBe("=");
    expect(parts[1]?.rows.map((row) => row.label)).toEqual(["X"]);
  });

  it("marks an unread item and keeps what the reader saw", () => {
    const circuit = circuitOf("parse partial");
    const unread = unreadRows(circuit);
    expect(unread).toHaveLength(1);
    expect(unread[0]).toMatchObject({ part: 1, step: 2, targets: [2], unread: true });
    expect(unread[0]?.source).toContain("iddots");
  });
});

describe("gateName", () => {
  const base = { label: "", controls: [], openControls: [] };
  it("names a controlled X by its usual name", () => {
    expect(gateName({ ...base, kind: "x", controls: [1] }).key).toBe("cnot");
    expect(gateName({ ...base, kind: "x", controls: [1, 2] }).key).toBe("toffoli");
    expect(gateName({ ...base, kind: "x", controls: [1], openControls: [2] }).key).toBeNull();
    expect(gateName({ ...base, kind: "x" })).toEqual({ key: null, text: "X" });
  });

  it("names a controlled swap and the kinds without a label", () => {
    expect(gateName({ ...base, kind: "swap", controls: [1] }).key).toBe("fredkin");
    expect(gateName({ ...base, kind: "swap" })).toEqual({ key: null, text: "SWAP" });
    expect(gateName({ ...base, kind: "z_dots" }).key).toBe("zDots");
    expect(gateName({ ...base, kind: "measure" }).key).toBe("measure");
    expect(gateName({ ...base, kind: "gate", label: "R_y(\\pi/2)" })).toEqual({
      key: null,
      text: "R_y(\\pi/2)",
    });
    expect(gateName({ ...base, kind: "dots" })).toEqual({ key: null, text: "dots" });
  });
});
