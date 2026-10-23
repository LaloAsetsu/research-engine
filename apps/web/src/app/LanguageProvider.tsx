import { useEffect, type ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/shared/i18n";
import { useUiStore } from "@/shared/stores/ui-store";

export function LanguageProvider({ children }: { children: ReactNode }) {
  const language = useUiStore((state) => state.language);
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}
