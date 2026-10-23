import { loginPath, safeNext } from "./guards";

describe("safeNext", () => {
  it("keeps a path inside the app", () => {
    expect(safeNext("/benchmarks")).toBe("/benchmarks");
    expect(safeNext("/sets?x=1")).toBe("/sets?x=1");
  });

  it("drops anything that could leave the app or loop", () => {
    for (const value of [
      null,
      "",
      "ask",
      "//evil.example",
      "https://evil.example",
      "/\\evil",
      "/login",
      "/login?next=/ask",
      "/\t/example.org",
      "/\n/example.org",
      "/\u0000/example.org",
      "/\u2028/example.org",
    ]) {
      expect(safeNext(value), String(value)).toBeNull();
    }
  });
});

describe("loginPath", () => {
  it("carries the path to return to, encoded", () => {
    expect(loginPath("/sets?filter=a b")).toBe("/login?next=%2Fsets%3Ffilter%3Da%20b");
  });

  it("leaves it out for the default screen and for unsafe values", () => {
    expect(loginPath("/ask")).toBe("/login");
    expect(loginPath("//evil.example")).toBe("/login");
    expect(loginPath(null)).toBe("/login");
  });
});
