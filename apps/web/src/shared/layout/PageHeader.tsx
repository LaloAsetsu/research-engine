import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** The page heading: it takes focus on a route change and gives the tab its title. */
export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  const { t } = useTranslation();
  useEffect(() => {
    document.title = `${title} | ${t("app.name")}`;
  }, [title, t]);
  return (
    <header className="mb-6 grid gap-2">
      <h1 id="page-title" tabIndex={-1} className="text-2xl font-semibold outline-none">
        {title}
      </h1>
      {children}
    </header>
  );
}
