import { excerpt, formatSeconds } from "./format";

describe("formatting seconds", () => {
  it("keeps one decimal in the reader's language", () => {
    expect(formatSeconds(1.84, "en")).toBe("1.8");
    expect(formatSeconds(1.84, "es")).toBe("1,8");
    expect(formatSeconds(12, "en")).toBe("12");
    expect(formatSeconds(0, "en")).toBe("0");
  });
});

describe("cutting a passage", () => {
  it("keeps a short text whole and joins its lines", () => {
    expect(excerpt("one\n\n two   three")).toBe("one two three");
  });

  it("drops the heading marks of Markdown", () => {
    expect(excerpt("## 2. Loss channels\nThe rate is # not a heading.")).toBe(
      "2. Loss channels The rate is # not a heading.",
    );
  });

  it("cuts a long text at a space", () => {
    const text = "word ".repeat(100);
    const cut = excerpt(text, 50);
    expect(cut.endsWith("...")).toBe(true);
    expect(cut.length).toBeLessThanOrEqual(53);
    expect(cut).not.toMatch(/wor\.\.\.$/);
  });

  it("cuts a text with no spaces where it must", () => {
    expect(excerpt("x".repeat(100), 10)).toBe(`${"x".repeat(10)}...`);
  });
});
