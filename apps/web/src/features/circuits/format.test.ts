import { basisLabel, bitsFor, formatComplex, formatProbability, squaredMagnitude } from "./format";

const MINUS = "\u2212";

describe("formatComplex", () => {
  it("writes a real, an imaginary and a mixed amplitude", () => {
    expect(formatComplex([0.707106781187, 0], "en")).toBe("0.7071");
    expect(formatComplex([0, 1], "en")).toBe("i");
    expect(formatComplex([0, -1], "en")).toBe(`${MINUS}i`);
    expect(formatComplex([0, 0.5], "en")).toBe("0.5i");
    expect(formatComplex([0.5, 0.5], "en")).toBe("0.5 + 0.5i");
    expect(formatComplex([0.5, -0.25], "en")).toBe(`0.5 ${MINUS} 0.25i`);
    expect(formatComplex([-0.5, 0], "en")).toBe(`${MINUS}0.5`);
  });

  it("writes zero as 0 and never as a signed zero", () => {
    expect(formatComplex([0, 0], "en")).toBe("0");
    expect(formatComplex([-0.00001, 0], "en")).toBe("0");
    expect(formatComplex([0, -0.00001], "en")).toBe("0");
  });

  it("uses the decimal mark of the language", () => {
    expect(formatComplex([0.5, 0], "es")).toBe("0,5");
  });
});

describe("formatProbability", () => {
  it("writes percentages with the digits that matter", () => {
    expect(formatProbability(0.5, "en")).toBe("50%");
    expect(formatProbability(0.125, "en")).toBe("12.5%");
    expect(formatProbability(1, "en")).toBe("100%");
    expect(formatProbability(0, "en")).toBe("0%");
  });

  it("never shows a nonzero probability as zero", () => {
    expect(formatProbability(1e-9, "en")).toBe("<0.0001%");
    expect(formatProbability(0.00005, "en")).toBe("0.005%");
  });

  it("reads a smaller probability as a smaller figure", () => {
    const written = [1e-9, 4e-7, 5e-6, 1e-5, 0.00005].map((p) => formatProbability(p, "en"));
    expect(written).toEqual(["<0.0001%", "<0.0001%", "0.0005%", "0.001%", "0.005%"]);
  });

  it("never shows a probability below 1 as 100%", () => {
    expect(formatProbability(0.9996, "en")).toBe("99.96%");
    expect(formatProbability(0.9994, "en")).toBe("99.9%");
    expect(formatProbability(0.999999, "en")).toBe("99.9999%");
    expect(formatProbability(1 - 1e-9, "en")).toBe(">99.9999%");
    expect(formatProbability(1, "en")).toBe("100%");
    expect(formatProbability(1 + 1e-12, "en")).toBe("100%");
  });

  it("reads a larger probability near 1 as a larger figure", () => {
    const written = [0.9994, 0.9996, 0.99999, 0.9999999, 1 - 1e-9].map((p) =>
      formatProbability(p, "en"),
    );
    expect(written).toEqual(["99.9%", "99.96%", "99.999%", ">99.9999%", ">99.9999%"]);
    expect(written).not.toContain("100%");
  });

  it("builds the bound with the digits of the language", () => {
    expect(formatProbability(1e-9, "es")).toMatch(/^<0,0001\s?%$/);
    expect(formatProbability(5e-6, "es")).toMatch(/^0,0005\s?%$/);
    expect(formatProbability(1 - 1e-9, "es")).toMatch(/^>99,9999\s?%$/);
  });
});

describe("basis labels", () => {
  it("puts the first wire first, as the most significant bit", () => {
    expect(basisLabel(1, 3)).toBe("|001\u27E9");
    expect(basisLabel(6, 3)).toBe("|110\u27E9");
    expect(basisLabel(0, 1)).toBe("|0\u27E9");
  });

  it("counts the bits of a list", () => {
    expect(bitsFor(8)).toBe(3);
    expect(bitsFor(1)).toBe(0);
  });

  it("squares a magnitude", () => {
    expect(squaredMagnitude([3, 4])).toBe(25);
  });
});
