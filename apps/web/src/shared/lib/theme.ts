import { readStored } from "./storage";

export const THEME_KEY = "re.theme";
export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

const DARK_QUERY = "(prefers-color-scheme: dark)";

export function parseTheme(value: string | null): Theme {
  return THEMES.find((theme) => theme === value) ?? "system";
}

export function storedTheme(): Theme {
  return parseTheme(readStored(THEME_KEY));
}

export function systemPrefersDark(): boolean {
  return window.matchMedia(DARK_QUERY).matches;
}

export function isDark(theme: Theme): boolean {
  return theme === "dark" || (theme === "system" && systemPrefersDark());
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", isDark(theme));
}

/** Calls back when the operating system switches between light and dark. Returns the unsubscribe. */
export function watchSystemTheme(onChange: () => void): () => void {
  const query = window.matchMedia(DARK_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
