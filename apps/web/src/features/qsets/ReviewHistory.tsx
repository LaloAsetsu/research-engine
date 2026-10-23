import { useTranslation } from "react-i18next";
import { Badge } from "@/shared/ui/badge";
import { describeValue } from "./itemDraft";
import { formatDate } from "./format";
import { decidedOnEarlierDraft } from "./rules";
import type { ReviewLine } from "./types";

/** Every decision on the item, oldest first: who, what, when, why, and what an edit changes. */
export function ReviewHistory({
  reviews,
  currentMd5,
  language,
}: {
  reviews: readonly ReviewLine[];
  currentMd5: string;
  language: string;
}) {
  const { t } = useTranslation();
  if (reviews.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("sets.review.noHistory")}</p>;
  }
  return (
    <ol className="grid gap-3" aria-label={t("sets.review.history")}>
      {reviews.map((line, index) => (
        <li key={`${line.date}-${index}`} className="grid gap-1 rounded-lg border p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <strong>{line.reviewer}</strong>
            <Badge variant="outline">{t(`sets.review.verdict.${line.verdict}`)}</Badge>
            <span className="text-muted-foreground">{formatDate(line.date, language)}</span>
            {decidedOnEarlierDraft(line, currentMd5) ? (
              <Badge variant="secondary">{t("sets.review.earlierDraft")}</Badge>
            ) : null}
          </div>
          {line.note ? <p className="whitespace-pre-wrap">{line.note}</p> : null}
          {line.edit && Object.keys(line.edit).length > 0 ? (
            <div>
              <p className="text-muted-foreground">{t("sets.review.editsFields")}</p>
              <ul className="grid gap-1">
                {Object.entries(line.edit).map(([key, value]) => (
                  <li key={key}>
                    <code className="rounded bg-muted px-1 py-0.5 text-xs">{key}</code>{" "}
                    <span className="break-words">{describeValue(value)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
