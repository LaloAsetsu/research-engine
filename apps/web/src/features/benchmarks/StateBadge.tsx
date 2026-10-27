import { useTranslation } from "react-i18next";
import { Badge } from "@/shared/ui/badge";
import type { JobState } from "./types";

const TONE = {
  queued: "secondary",
  running: "default",
  cancelling: "secondary",
  cancelled: "secondary",
  done: "outline",
  failed: "problem",
  interrupted: "problem",
} as const satisfies Record<JobState, "default" | "secondary" | "outline" | "problem">;

/**
 * The state of a job as a word: the color only repeats it. A job that stopped by itself has an
 * outline in the error color; the destructive badge variant has too little contrast for its text.
 */
export function StateBadge({ state }: { state: JobState }) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.state" });
  const tone = TONE[state];
  return tone === "problem" ? (
    <Badge variant="outline" className="border-destructive text-destructive">
      {t(state)}
    </Badge>
  ) : (
    <Badge variant={tone}>{t(state)}</Badge>
  );
}
