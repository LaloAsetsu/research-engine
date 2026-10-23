import { Loader2, ServerOff, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import type { AskStatus } from "./types";

export type EngineView =
  | { kind: "unknown" }
  | { kind: "unreachable" }
  | { kind: "ready" }
  | { kind: "warming" }
  | { kind: "unavailable"; reason: string | null };

export function engineView(
  status: { ask: AskStatus } | undefined,
  unreachable: boolean,
): EngineView {
  if (unreachable) return { kind: "unreachable" };
  if (!status) return { kind: "unknown" };
  const { state, reason } = status.ask;
  if (state === "warming_up") return { kind: "warming" };
  if (state === "unavailable") return { kind: "unavailable", reason: reason ?? null };
  // `ready`, and any state a newer API adds: only the two above change what the form does.
  return { kind: "ready" };
}

/**
 * Only a starting engine blocks the form: it refuses questions for a few seconds. An engine that
 * reports `unavailable` still takes them, because the cause (a missing index) may have been fixed
 * since, and the next answer clears the report.
 */
export function blocksAsking(view: EngineView): boolean {
  return view.kind === "warming";
}

export function EngineNotice({ view }: { view: EngineView }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  switch (view.kind) {
    case "warming":
      return (
        <Alert role="status">
          <Loader2 aria-hidden="true" className="animate-spin" />
          <AlertTitle>{t("engine.warming.title")}</AlertTitle>
          <AlertDescription>{t("engine.warming.body")}</AlertDescription>
        </Alert>
      );
    case "unavailable":
      return (
        <Alert variant="destructive" role="status">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>{t("engine.unavailable.title")}</AlertTitle>
          <AlertDescription>
            {view.reason
              ? t("engine.unavailable.body", { reason: view.reason })
              : t("engine.unavailable.noReason")}
          </AlertDescription>
        </Alert>
      );
    case "unreachable":
      return (
        <Alert variant="destructive">
          <ServerOff aria-hidden="true" />
          <AlertTitle>{t("engine.unreachable.title")}</AlertTitle>
          <AlertDescription>{t("engine.unreachable.body")}</AlertDescription>
        </Alert>
      );
    default:
      return null;
  }
}
