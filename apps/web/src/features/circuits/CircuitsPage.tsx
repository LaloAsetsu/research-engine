import { useTranslation } from "react-i18next";
import { PlaceholderPage } from "@/shared/layout/PlaceholderPage";

export function CircuitsPage() {
  const { t } = useTranslation();
  return (
    <PlaceholderPage
      title={t("pages.circuits.title")}
      description={t("pages.circuits.placeholder")}
    />
  );
}

export { CircuitsPage as Component };
