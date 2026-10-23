import { useTranslation } from "react-i18next";
import { $api } from "@/shared/api/client";
import { Badge } from "@/shared/ui/badge";

const REFRESH_MS = 30_000;

/** Whether the API is up, from GET /status, refreshed every 30 s. State is a word, not only a color. */
export function StatusBadge() {
  const { t } = useTranslation();
  const { data, isError, isPending } = $api.useQuery(
    "get",
    "/api/v1/status",
    {},
    { refetchInterval: REFRESH_MS, retry: false },
  );

  let label = t("status.loading");
  let variant: "secondary" | "outline" | "destructive" = "secondary";
  if (isError) {
    label = t("status.unreachable");
    variant = "destructive";
  } else if (!isPending && data) {
    label = data.ready
      ? t(data.mode === "fake" ? "status.readyFake" : "status.ready")
      : t("status.starting");
    variant = data.ready ? "outline" : "secondary";
  }

  return (
    <Badge variant={variant} role="status" aria-label={`${t("status.label")}: ${label}`}>
      <span
        aria-hidden="true"
        className={`size-1.5 rounded-full ${isError ? "bg-destructive" : data?.ready ? "bg-emerald-600 dark:bg-emerald-400" : "bg-muted-foreground"}`}
      />
      {label}
    </Badge>
  );
}
