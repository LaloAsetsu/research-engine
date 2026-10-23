import { useTranslation } from "react-i18next";
import { Badge } from "@/shared/ui/badge";
import type { ReviewState, SetVersion } from "./types";

const VARIANT: Record<ReviewState, "secondary" | "outline" | "default" | "destructive"> = {
  none: "outline",
  needs_second: "secondary",
  accepted: "default",
  edited: "secondary",
  rejected: "outline",
};

/** The review state of an item as a word, never as a color alone. */
export function ReviewBadge({ state }: { state: ReviewState }) {
  const { t } = useTranslation();
  return (
    <Badge variant={VARIANT[state]} data-review={state}>
      {t(`sets.review.state.${state}`)}
    </Badge>
  );
}

export function StatusBadge({ status }: { status: SetVersion["status"] }) {
  const { t } = useTranslation();
  return (
    <Badge variant={status === "frozen" ? "default" : "outline"} data-status={status}>
      {t(`sets.status.${status}`)}
    </Badge>
  );
}
