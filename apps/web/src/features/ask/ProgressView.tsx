import { Check, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Card, CardContent } from "@/shared/ui/card";
import { cn } from "@/shared/lib/utils";
import type { AskEntry, Progress } from "./store";

const STEPS = ["queued", "retrieving", "answering"] as const;
type Step = (typeof STEPS)[number];

/** The step a question is at; "sending" is before the first one. */
function stepIndex(status: Progress): number {
  return status === "sending" ? -1 : STEPS.indexOf(status);
}

export function useProgressText() {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  return (status: Progress, position: number | null): string => {
    if (status !== "queued") return t(`progress.${status}`);
    if (position === null) return t("progress.queued");
    return position === 0
      ? `${t("progress.queued")}. ${t("progress.queuedNext")}`
      : `${t("progress.queued")}. ${t("progress.queuedAhead", { count: position })}`;
  };
}

/** The steps of the active question: waiting, searching, writing. The current one is marked in words, not only by color. */
export function ProgressView({ entry, onCancel }: { entry: AskEntry; onCancel: () => void }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const text = useProgressText();
  const status = entry.status as Progress;
  const current = stepIndex(status);
  // Without the index there is no search step.
  const steps: readonly Step[] = entry.settings.useIndex
    ? STEPS
    : STEPS.filter((step) => step !== "retrieving");
  return (
    <Card>
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">{t("progress.label")}</h2>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={entry.askId === null}
            onClick={onCancel}
          >
            {t("form.cancel")}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          <span className="font-medium">{t("answer.asked")}:</span> {entry.question}
        </p>
        <ol aria-label={t("progress.label")} className="grid gap-2">
          {steps.map((step) => {
            const index = STEPS.indexOf(step);
            const state = index < current ? "done" : index === current ? "current" : "next";
            return (
              <li
                key={step}
                aria-current={state === "current" ? "step" : undefined}
                className={cn(
                  "flex items-center gap-2 text-sm",
                  state === "next" && "text-muted-foreground",
                  state === "current" && "font-medium",
                )}
              >
                {state === "done" ? (
                  <Check
                    aria-hidden="true"
                    className="size-4 text-emerald-700 dark:text-emerald-400"
                  />
                ) : state === "current" ? (
                  <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                ) : (
                  <span aria-hidden="true" className="size-4 text-center">
                    &middot;
                  </span>
                )}
                <span>
                  {step === "queued" ? text("queued", entry.position) : t(`progress.${step}`)}
                </span>
                <span className="sr-only">({t(`progress.step.${state}`)})</span>
              </li>
            );
          })}
        </ol>
        {status === "sending" ? (
          <p className="text-sm text-muted-foreground">{t("progress.sending")}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
