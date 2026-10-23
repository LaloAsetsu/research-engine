import {
  addMissingNames,
  parseStateInput,
  parseUnitaryInput,
  parseValues,
  symbolKey,
} from "./values";

describe("parseValues", () => {
  it("reads numbers as numbers and angles as text", () => {
    expect(parseValues("theta = \\pi/2\nphi=0.5\n\n k = -3 ")).toEqual({
      values: { theta: "\\pi/2", phi: 0.5, k: -3 },
      errors: [],
    });
  });

  it("keeps an angle that only looks like a number", () => {
    expect(parseValues("a = 1/2").values).toEqual({ a: "1/2" });
    expect(parseValues("a = 1e-3").values).toEqual({ a: 0.001 });
  });

  it("reports a line with no equals sign, no name or no value", () => {
    const { errors } = parseValues("theta\n= 3\nphi =");
    expect(errors).toEqual([
      { line: 1, code: "format", name: "theta" },
      { line: 2, code: "noName", name: "" },
      { line: 3, code: "noValue", name: "phi" },
    ]);
  });

  it("refuses a symbol named twice, also under two spellings", () => {
    const { values, errors } = parseValues("theta = 1\n\\theta = 2\n{theta} = 3");
    expect(values).toEqual({ theta: 1 });
    expect(errors.map((error) => error.code)).toEqual(["duplicate", "duplicate"]);
  });
});

describe("symbolKey", () => {
  it("drops the backslash, braces and spaces", () => {
    expect(symbolKey("\\theta_{1}")).toBe("theta_1");
  });
});

describe("addMissingNames", () => {
  it("adds a line for each name without one", () => {
    expect(addMissingNames("", ["theta", "phi"])).toBe("theta = \nphi = ");
    expect(addMissingNames("theta = 1", ["theta", "phi"])).toBe("theta = 1\nphi = ");
    expect(addMissingNames("theta = 1\n", ["\\theta"])).toBe("theta = 1\n");
  });
});

describe("parseStateInput", () => {
  it("accepts empty, a basis label and a list", () => {
    expect(parseStateInput("")).toEqual({ ok: true, value: undefined });
    expect(parseStateInput(" 010 ")).toEqual({ ok: true, value: "010" });
    expect(parseStateInput("[0, [0.5, 0.5]]")).toEqual({ ok: true, value: [0, [0.5, 0.5]] });
  });

  it("refuses anything else", () => {
    for (const bad of ["012", "ket", "[1, ", "{}", "0 1"]) {
      expect(parseStateInput(bad), bad).toEqual({ ok: false, code: "format" });
    }
  });
});

describe("parseUnitaryInput", () => {
  it("accepts a list of rows", () => {
    expect(parseUnitaryInput("[[0, 1], [1, 0]]")).toEqual({
      ok: true,
      value: [
        [0, 1],
        [1, 0],
      ],
    });
  });

  it("refuses text, an empty list and a flat list", () => {
    for (const bad of ["x", "[]", "[1, 2]", "{}"]) {
      expect(parseUnitaryInput(bad), bad).toEqual({ ok: false, code: "format" });
    }
  });
});
