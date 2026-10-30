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
 * A starting engine refuses questions for a few seconds, and one that reports `unavailable` has
 * its models or its index down: in both the button is disabled (A5). Only the first clears by
 * itself. `unavailable` clears when a question works, and `/status` does not probe the model
 * servers yet, so the form offers "Ask anyway" for that state, or nobody could ever ask again.
 */
export function blocksAsking(view: EngineView): boolean {
  return view.kind === "warming" || view.kind === "unavailable";
}

/** Only an engine that is starting must also stop a retry: it would be refused. */
export function isStarting(view: EngineView): boolean {
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
