import type { ReactNode } from "react";
import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ApiError, type ProblemField } from "@/shared/api/errors";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";

const SHOWN = 12;

/** Lists problems with their place, at most a dozen: a refused file can have a hundred. */
export function ProblemList({ problems }: { problems: readonly ProblemField[] }) {
  const { t } = useTranslation();
  const shown = problems.slice(0, SHOWN);
  return (
    <ul className="mt-2 grid gap-1 text-sm">
      {shown.map((problem, index) => (
        <li key={`${index}-${problem.path}`}>
          <code className="rounded bg-muted px-1 py-0.5 text-xs text-foreground">
            {problem.path || "/"}
          </code>{" "}
          {problem.message}
        </li>
      ))}
      {problems.length > SHOWN ? (
        <li className="text-muted-foreground">
          {t("sets.problem.more", { count: problems.length - SHOWN })}
        </li>
      ) : null}
    </ul>
  );
}

/** What the API refused, in words: the title by status, the server's own detail, and each problem with its place. */
export function ProblemView({
  error,
  showFields = true,
  children,
}: {
  error: unknown;
  showFields?: boolean;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const api = error instanceof ApiError ? error : null;
  const title = api
    ? t(`sets.problem.status.${api.status}`, { defaultValue: t("sets.problem.status.other") })
    : t("sets.problem.status.other");
  const detail = api ? api.detail : error instanceof Error ? error.message : String(error);
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{detail}</p>
        {api?.retryAfter !== undefined ? (
          <p>{t("sets.problem.retryAfter", { count: Math.ceil(api.retryAfter) })}</p>
        ) : null}
        {api && showFields && api.errors.length > 0 ? <ProblemList problems={api.errors} /> : null}
        {api?.errorId ? <p>{t("sets.problem.errorId", { id: api.errorId })}</p> : null}
        {children}
      </AlertDescription>
    </Alert>
  );
}
