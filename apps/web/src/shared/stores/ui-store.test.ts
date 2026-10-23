import i18n from "@/shared/i18n";
import { useUiStore } from "./ui-store";

describe("useUiStore", () => {
  it("saves the theme and the language in localStorage", () => {
    useUiStore.getState().setTheme("dark");
    useUiStore.getState().setLanguage("es");
    expect(useUiStore.getState()).toMatchObject({ theme: "dark", language: "es" });
    expect(window.localStorage.getItem("re.theme")).toBe("dark");
    expect(window.localStorage.getItem("re.lang")).toBe("es");
    expect(i18n.language).toBe("es");
  });

  it("keeps working when storage refuses writes", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(() => useUiStore.getState().setTheme("light")).not.toThrow();
    expect(useUiStore.getState().theme).toBe("light");
  });
});
