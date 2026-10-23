import { create } from "zustand";
import i18n from "@/shared/i18n";
import { LANGUAGE_KEY, parseLanguage, type Language } from "@/shared/i18n/detect";
import { writeStored } from "@/shared/lib/storage";
import { storedTheme, THEME_KEY, type Theme } from "@/shared/lib/theme";

interface UiState {
  theme: Theme;
  language: Language;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
}

export const useUiStore = create<UiState>()((set) => ({
  theme: storedTheme(),
  language: parseLanguage(i18n.language) ?? "es",
  setTheme: (theme) => {
    writeStored(THEME_KEY, theme);
    set({ theme });
  },
  setLanguage: (language) => {
    writeStored(LANGUAGE_KEY, language);
    void i18n.changeLanguage(language);
    set({ language });
  },
}));
