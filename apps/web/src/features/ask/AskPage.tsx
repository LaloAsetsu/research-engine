import { useTranslation } from "react-i18next";
import { PlaceholderPage } from "@/shared/layout/PlaceholderPage";

export function AskPage() {
  const { t } = useTranslation();
  return <PlaceholderPage title={t("pages.ask.title")} description={t("pages.ask.placeholder")} />;
}

export { AskPage as Component };
