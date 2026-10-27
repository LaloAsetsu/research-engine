import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/shared/api/errors";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";

const TITLED = new Set([404, 409, 413, 422, 429, 503]);

/** What the server said, in its own words, with a title for the kinds of answer that mean something here. */
export function ProblemAlert({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: (() => void) | undefined;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.problem" });
  const api = isApiError(error) ? error : null;
  const title = t(`title.${api && TITLED.has(api.status) ? api.status : "default"}`);
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="grid gap-2">
        <p>{api ? api.detail : t("network")}</p>
        {api?.retryAfter !== undefined ? (
          <p>{t("retryAfter", { seconds: Math.ceil(api.retryAfter) })}</p>
        ) : null}
        {api?.errorId ? (
          <p className="font-mono text-xs">{t("errorId", { id: api.errorId })}</p>
        ) : null}
        {onRetry ? (
          <div>
            <Button type="button" variant="outline" size="sm" onClick={onRetry}>
              {t("retry")}
            </Button>
          </div>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
