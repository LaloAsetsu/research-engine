import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { CitationTitle, openerName, usePagesLabel, useOpenCitation } from "./CitationButtons";
import { excerpt } from "./format";
import { shortId } from "./citations";
import { useDocument } from "./api";
import type { AskContext } from "./types";

/** What the answering model was given, in order. Closed until asked for: it is long. */
export function ContextsPanel({
  entryKey,
  contexts,
}: {
  entryKey: string;
  contexts: readonly AskContext[];
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const [open, setOpen] = useState(false);
  const listId = useId();
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <section aria-labelledby={`${listId}-title`} className="grid gap-2">
      <h3 id={`${listId}-title`} className="text-sm font-medium">
        {t("contexts.title")}{" "}
        <span className="font-normal text-muted-foreground">
          ({t("contexts.count", { count: contexts.length })})
        </span>
      </h3>
      {contexts.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("contexts.none")}</p>
      ) : (
        <>
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-expanded={open}
              aria-controls={listId}
              onClick={() => setOpen((value) => !value)}
            >
              <Icon aria-hidden="true" />
              {open ? t("contexts.hide") : t("contexts.show")}
            </Button>
          </div>
          <ol id={listId} aria-labelledby={`${listId}-title`} hidden={!open} className="grid gap-2">
            {open
              ? contexts.map((context, index) => (
                  <ContextCard
                    key={`${context.doc_id}:${context.start}:${index}`}
                    entryKey={entryKey}
                    context={context}
                    index={index}
                  />
                ))
              : null}
          </ol>
        </>
      )}
    </section>
  );
}

function ContextCard({
  entryKey,
  context,
  index,
}: {
  entryKey: string;
  context: AskContext;
  index: number;
}) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "ask" });
  const pagesLabel = usePagesLabel();
  const open = useOpenCitation();
  const paper = useDocument(context.doc_id);
  const kind = paper.data?.summary.page_kind ?? "page";
  const pages = pagesLabel(kind, context.first_page, context.last_page);
  const opener = openerName(entryKey, "context", index);
  const score =
    context.score === null || context.score === undefined
      ? null
      : new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }).format(context.score);
  return (
    <li className="grid gap-1.5 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-sm font-medium">{t("contexts.item", { n: index + 1 })}</span>
        <span className="font-mono text-xs text-muted-foreground">{shortId(context.doc_id)}</span>
        <span className="text-sm">{pages}</span>
        {context.is_reference ? <Badge variant="secondary">{t("contexts.reference")}</Badge> : null}
        {score ? (
          <span className="text-xs text-muted-foreground">{t("contexts.score", { score })}</span>
        ) : null}
      </div>
      <CitationTitle docId={context.doc_id} />
      <p className="text-sm text-muted-foreground">{excerpt(context.text)}</p>
      <div>
        <Button
          type="button"
          variant="outline"
          size="xs"
          data-opener={opener}
          onClick={() =>
            open(
              {
                key: `${shortId(context.doc_id)} pages ${context.first_page}-${context.last_page}`,
                doc_id: context.doc_id,
                first_page: context.first_page,
                last_page: context.last_page,
              },
              opener,
            )
          }
        >
          {t("contexts.open", { pages })}
        </Button>
      </div>
    </li>
  );
}
