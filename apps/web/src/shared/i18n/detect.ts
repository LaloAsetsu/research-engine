export const LANGUAGE_KEY = "re.lang";
export const LANGUAGES = ["es", "en"] as const;
export type Language = (typeof LANGUAGES)[number];
export const FALLBACK_LANGUAGE: Language = "es";

export function parseLanguage(value: string | null | undefined): Language | undefined {
  return LANGUAGES.find((language) => language === value);
}

/**
 * The saved choice if there is one; else the first browser language that is Spanish or English
 * (`en-GB` counts as English); else Spanish.
 */
export function detectLanguage(saved: string | null, preferred: readonly string[]): Language {
  const chosen = parseLanguage(saved);
  if (chosen) return chosen;
  for (const tag of preferred) {
    const primary = parseLanguage(tag.toLowerCase().split("-")[0]);
    if (primary) return primary;
  }
  return FALLBACK_LANGUAGE;
}
