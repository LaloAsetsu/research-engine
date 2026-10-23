import { useTranslation } from "react-i18next";
import { PlaceholderPage } from "@/shared/layout/PlaceholderPage";

export function QSetsPage() {
  const { t } = useTranslation();
  return (
    <PlaceholderPage title={t("pages.sets.title")} description={t("pages.sets.placeholder")} />
  );
}

export { QSetsPage as Component };
