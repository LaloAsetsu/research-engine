import { clip, formatDate, shortMd5 } from "./format";

describe("formatting for the screen", () => {
  it("writes a date in the language and says it is UTC", () => {
    expect(formatDate("2026-01-01T00:00:00Z", "en")).toMatch(/Jan 1, 2026.*UTC/);
    expect(formatDate("2026-01-01T00:00:00Z", "es")).toMatch(/2026.*UTC/);
  });

  it("shows a value that is not a date as it is, and nothing for nothing", () => {
    expect(formatDate("not a date", "en")).toBe("not a date");
    expect(formatDate(null, "en")).toBe("");
    expect(formatDate(undefined, "en")).toBe("");
  });

  it("shortens an md5", () => {
    expect(shortMd5("cb9cb733168663d4b04e75cdf16e5030")).toBe("cb9cb733");
  });

  it("clips long text with an ellipsis", () => {
    expect(clip("abcdef", 4)).toBe("abc…");
    expect(clip("abc", 4)).toBe("abc");
  });
});
