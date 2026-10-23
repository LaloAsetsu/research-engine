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
    expect(formatProbability(1e-9, "en")).toBe("<0.01%");
    expect(formatProbability(0.00005, "en")).toBe("0.005%");
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
