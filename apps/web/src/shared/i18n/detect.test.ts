import { detectLanguage, parseLanguage } from "./detect";

describe("detectLanguage", () => {
  it("prefers the saved choice", () => {
    expect(detectLanguage("en", ["es-MX"])).toBe("en");
    expect(detectLanguage("es", ["en-US"])).toBe("es");
  });

  it("ignores a saved value that is not a language of the app", () => {
    expect(detectLanguage("fr", ["en-US"])).toBe("en");
  });

  it("follows the browser's first Spanish or English language", () => {
    expect(detectLanguage(null, ["en-GB", "es"])).toBe("en");
    expect(detectLanguage(null, ["es-MX", "en"])).toBe("es");
    expect(detectLanguage(null, ["fr-FR", "en-US"])).toBe("en");
    expect(detectLanguage(null, ["EN"])).toBe("en");
  });

  it("falls back to Spanish", () => {
    expect(detectLanguage(null, ["fr-FR", "de"])).toBe("es");
    expect(detectLanguage(null, [])).toBe("es");
  });
});

describe("parseLanguage", () => {
  it("accepts only the two languages", () => {
    expect(parseLanguage("es")).toBe("es");
    expect(parseLanguage("pt")).toBeUndefined();
    expect(parseLanguage(null)).toBeUndefined();
  });
});
