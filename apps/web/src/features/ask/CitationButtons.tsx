import { FileText } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useDocument } from "./api";
import { shortId } from "./citations";
import { useAskStore } from "./store";
import type { Citation } from "./types";

/** "page 3", "pages 3-4", "section 2": how a cited range reads in the paper's own units. */
export function usePagesLabel() {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  return (kind: "page" | "section", first: number, last: number) =>
    t(`pages.${kind}`, { first, last, count: last > first ? 2 : 1 });
}

/** The name of the element that opens a paper, so focus can return to it when the panel closes. */
export function openerName(entryKey: string, part: string, index: number): string {
  return `${entryKey}:${part}:${index}`;
}

/** What opening a citation does: show the paper at its first cited page. */
export function useOpenCitation() {
  const openSource = useAskStore((state) => state.openSource);
  return (citation: Citation, opener: string) =>
    openSource({
      docId: citation.doc_id,
      page: citation.first_page,
      first: citation.first_page,
      last: citation.last_page,
      opener,
    });
}

interface CitationProps {
  citation: Citation;
  opener: string;
}

/** One cited range as a button: the short id and the pages, with the paper's title in its name. */
export function CitationChip({ citation, opener }: CitationProps) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const pagesLabel = usePagesLabel();
  const open = useOpenCitation();
  const paper = useDocument(citation.doc_id);
  const kind = paper.data?.summary.page_kind ?? "page";
  const title = paper.data?.summary.title;
  return (
    <button
      type="button"
      data-opener={opener}
      title={t("answer.openCitation")}
      onClick={() => open(citation, opener)}
      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border bg-background px-2 py-1 text-sm hover:bg-muted focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      <FileText aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="font-mono text-xs">{shortId(citation.doc_id)}</span>
      <span>{pagesLabel(kind, citation.first_page, citation.last_page)}</span>
      {title ? <span className="sr-only">, {title}</span> : null}
    </button>
  );
}

/** The paper's title beside its chip, once the paper is known. */
export function CitationTitle({ docId }: { docId: string }) {
  const paper = useDocument(docId);
  const title = paper.data?.summary.title;
  return title ? <span className="min-w-0 text-sm text-muted-foreground">{title}</span> : null;
}

/** A citation inside the text of the answer. */
export function InlineCitation({
  citation,
  opener,
  children,
}: CitationProps & { children: ReactNode }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const open = useOpenCitation();
  const title = useDocument(citation.doc_id).data?.summary.title;
  return (
    <button
      type="button"
      data-opener={opener}
      title={t("answer.openCitation")}
      onClick={() => open(citation, opener)}
      className="rounded-sm font-mono text-xs text-primary underline underline-offset-3 hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      {children}
      {title ? <span className="sr-only">, {title}</span> : null}
    </button>
  );
}
