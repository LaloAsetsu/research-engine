import { setSystemDark } from "@/test/media";
import { applyTheme, isDark, parseTheme, storedTheme, watchSystemTheme } from "./theme";

describe("theme", () => {
  it("parses a saved value, defaulting to the system", () => {
    expect(parseTheme("dark")).toBe("dark");
    expect(parseTheme("light")).toBe("light");
    expect(parseTheme("blue")).toBe("system");
    expect(parseTheme(null)).toBe("system");
  });

  it("follows the system only while the theme is system", () => {
    setSystemDark(true);
    expect(isDark("system")).toBe(true);
    expect(isDark("light")).toBe(false);
    setSystemDark(false);
    expect(isDark("system")).toBe(false);
    expect(isDark("dark")).toBe(true);
  });

  it("puts the dark class on the root element", () => {
    applyTheme("dark");
    expect(document.documentElement).toHaveClass("dark");
    applyTheme("light");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("reads the saved theme, and survives storage that throws", () => {
    window.localStorage.setItem("re.theme", "dark");
    expect(storedTheme()).toBe("dark");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(storedTheme()).toBe("system");
  });

  it("tells a watcher when the system changes, until it is stopped", () => {
    const seen = vi.fn();
    const stop = watchSystemTheme(seen);
    setSystemDark(true);
    expect(seen).toHaveBeenCalledTimes(1);
    stop();
    setSystemDark(false);
    expect(seen).toHaveBeenCalledTimes(1);
  });
});
