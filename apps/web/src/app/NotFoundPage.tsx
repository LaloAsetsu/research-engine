import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { buttonVariants } from "@/shared/ui/button";

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t("pages.notFound.title")} />
      <p className="mb-4 text-muted-foreground">{t("pages.notFound.body")}</p>
      <Link to="/ask" className={buttonVariants({ variant: "outline" })}>
        {t("pages.notFound.back")}
      </Link>
    </>
  );
}
