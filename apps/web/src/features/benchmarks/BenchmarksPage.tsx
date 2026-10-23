import { useTranslation } from "react-i18next";
import { PlaceholderPage } from "@/shared/layout/PlaceholderPage";

export function BenchmarksPage() {
  const { t } = useTranslation();
  return (
    <PlaceholderPage
      title={t("pages.benchmarks.title")}
      description={t("pages.benchmarks.placeholder")}
    />
  );
}

export { BenchmarksPage as Component };
