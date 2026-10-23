import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import type { Failure } from "./store";

interface Described {
  status: number | null;
  message: string | null;
  retryAfter: number | null;
  errorId: string | null;
}

/** What a failure says: the server's own sentence when there is one, else the screen's. */
function describe(failure: Failure, t: (key: string) => string): Described {
  switch (failure.kind) {
    case "server":
      return {
        status: failure.failure.status,
        message: failure.failure.message,
        retryAfter: failure.failure.retry_after_s ?? null,
        errorId: null,
      };
    case "api":
      return {
        status: failure.error.status,
        message: failure.error.detail,
        retryAfter: failure.error.retryAfter ?? null,
        errorId: failure.error.errorId ?? null,
      };
    case "network":
      return { status: null, message: t("failure.network"), retryAfter: null, errorId: null };
    case "lost":
      return { status: null, message: t("failure.lost"), retryAfter: null, errorId: null };
    case "malformed":
      return { status: null, message: t("failure.malformed"), retryAfter: null, errorId: null };
  }
}

const TITLED = new Set([503, 502, 429, 422]);

/** A question that got no answer: why, and a way to try again. */
export function FailureView({
  failure,
  question,
  disabled,
  onRetry,
}: {
  failure: Failure;
  question: string;
  disabled: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const { status, message, retryAfter, errorId } = describe(failure, t);
  const title = t(`failure.title.${status !== null && TITLED.has(status) ? status : "default"}`);
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="grid gap-2">
        <p>{message}</p>
        {retryAfter !== null ? <p>{t("failure.retryAfter", { seconds: retryAfter })}</p> : null}
        {errorId ? (
          <p className="font-mono text-xs">{t("failure.errorId", { id: errorId })}</p>
        ) : null}
        <p className="text-muted-foreground">
          <span className="font-medium">{t("answer.asked")}:</span> {question}
        </p>
        <div>
          <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={onRetry}>
            {t("failure.retry")}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
