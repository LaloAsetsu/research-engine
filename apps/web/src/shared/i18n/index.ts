import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { readStored } from "@/shared/lib/storage";
import { detectLanguage, LANGUAGE_KEY, type Language } from "./detect";
import en from "./en.json";
import es from "./es.json";

export function initialLanguage(): Language {
  const preferred = navigator.languages.length > 0 ? navigator.languages : [navigator.language];
  return detectLanguage(readStored(LANGUAGE_KEY), preferred);
}

void i18n.use(initReactI18next).init({
  resources: { es: { translation: es }, en: { translation: en } },
  lng: initialLanguage(),
  fallbackLng: "es",
  interpolation: { escapeValue: false },
});

export default i18n;
