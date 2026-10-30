import { useTranslation } from "react-i18next";
import { cn } from "@/shared/lib/utils";
import { Badge } from "@/shared/ui/badge";
import { useAskStore } from "./store";

/** The questions asked in this tab, newest first. They are not on the server (D-197) and not saved here. */
export function HistoryList() {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const entries = useAskStore((state) => state.entries);
  const selectedKey = useAskStore((state) => state.selectedKey);
  const select = useAskStore((state) => state.select);
  return (
    <section aria-labelledby="ask-history-title" className="grid content-start gap-2">
      <h2 id="ask-history-title" className="text-sm font-medium">
        {t("history.title")}
      </h2>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("history.empty")}</p>
      ) : (
        <ul className="grid gap-1.5">
          {entries.map((entry) => (
            <li key={entry.key}>
              <button
                type="button"
                aria-current={entry.key === selectedKey ? "true" : undefined}
                onClick={() => select(entry.key)}
                className={cn(
                  "grid w-full gap-1 rounded-lg border border-border p-2 text-left text-sm hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                  entry.key === selectedKey && "bg-muted",
                )}
              >
                <span className="line-clamp-2">{entry.question}</span>
                <Badge
                  variant={entry.status === "failed" ? "outline" : "secondary"}
                  className="w-fit"
                >
                  {t(`history.status.${entry.status}`)}
                </Badge>
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{t("history.note")}</p>
    </section>
  );
}
