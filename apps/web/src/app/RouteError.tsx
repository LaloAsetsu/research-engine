import { useTranslation } from "react-i18next";
import { useRevalidator, useRouteError } from "react-router";
import { isApiError } from "@/shared/api/errors";
import { PageHeader } from "@/shared/layout/PageHeader";
import { reloadPage } from "@/shared/lib/page";
import { Alert, AlertDescription } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";

/**
 * A plain panel with "Try again", never a stack trace. A failed request asks the loaders again.
 * Anything else (a render error, a lazy chunk that no longer exists after a new release) reloads
 * the page, because revalidating does not import a failed chunk a second time.
 */
function ErrorPanel() {
  const { t } = useTranslation();
  const error = useRouteError();
  const revalidator = useRevalidator();
  const retry = isApiError(error) ? () => void revalidator.revalidate() : reloadPage;
  const detail = isApiError(error) ? error.detail : t("pages.error.body");
  return (
    <>
      <PageHeader title={t("pages.error.title")} />
      <Alert variant="destructive">
        <AlertDescription>{detail}</AlertDescription>
      </Alert>
      <div className="mt-4">
        <Button variant="outline" onClick={retry}>
          {t("app.tryAgain")}
        </Button>
      </div>
    </>
  );
}

/** The error boundary of a route outside the layout (the login screen, the guard of `/`). */
export function RouteError() {
  return (
    <main className="mx-auto max-w-xl p-4">
      <ErrorPanel />
    </main>
  );
}

/** The error boundary of a screen: the top bar and the navigation stay, the screen is replaced. */
export function ScreenError() {
  return <ErrorPanel />;
}
