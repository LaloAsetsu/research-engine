import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/shared/api/errors";
import { MarkdownMath } from "@/shared/markdown/MarkdownMath";
import { Alert, AlertDescription } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/shared/ui/sheet";
import { Skeleton } from "@/shared/ui/skeleton";
import { useDocument, useDocumentPage } from "./api";
import { usePagesLabel } from "./CitationButtons";
import { useAskStore, type SourceTarget } from "./store";

/** Puts focus back on the element that opened the panel, or on the question box if it is gone. */
function returnFocus(opener: string): void {
  const target = Array.from(document.querySelectorAll<HTMLElement>("[data-opener]")).find(
    (element) => element.dataset.opener === opener,
  );
  (target ?? document.getElementById("ask-question"))?.focus();
}

/**
 * A cited paper at a page, beside the answer. It opens on the first cited page; next and
 * previous move through the paper. Escape closes it and focus returns to the citation.
 */
export function SourcePanel() {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const source = useAskStore((state) => state.source);
  const closeSource = useAskStore((state) => state.closeSource);
  // The panel keeps showing the last paper while it slides out.
  const [shown, setShown] = useState<SourceTarget | null>(source);
  if (source !== null && source !== shown) setShown(source);
  return (
    <Sheet open={source !== null} onOpenChange={(open) => !open && closeSource()}>
      <SheetContent
        side="right"
        closeLabel={t("source.close")}
        className="data-[side=right]:w-full data-[side=right]:sm:max-w-2xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (shown) returnFocus(shown.opener);
        }}
      >
        {shown ? <SourceView target={shown} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function SourceView({ target }: { target: SourceTarget }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const pagesLabel = usePagesLabel();
  const moveSource = useAskStore((state) => state.moveSource);
  const paper = useDocument(target.docId);
  const found = useDocumentPage(target.docId, target.page);
  // While another page loads the previous one stays (no flash), but never another paper's page.
  const page = { ...found, data: found.data?.doc_id === target.docId ? found.data : undefined };
  const body = useRef<HTMLDivElement>(null);

  const summary = paper.data?.summary;
  const kind = page.data?.kind ?? summary?.page_kind ?? "page";
  const total = page.data?.pages ?? summary?.pages ?? null;
  const unit = kind === "section" ? "section" : "page";

  useEffect(() => {
    body.current?.scrollTo?.({ top: 0 });
  }, [target.docId, target.page]);

  const position =
    total === null
      ? null
      : t(unit === "section" ? "source.sectionOf" : "source.pageOf", { page: target.page, total });
  const authors = paper.data?.authors.join(", ");
  const year = paper.data?.year;
  const error = found.error;
  const pageFailed = found.isError;
  const message = isApiError(error, 404)
    ? t("source.notFound")
    : isApiError(error, 409)
      ? t("source.ambiguous")
      : t("source.error");

  return (
    <>
      <SheetHeader className="pr-12">
        <SheetTitle>{summary?.title ?? t("source.fallbackTitle")}</SheetTitle>
        <SheetDescription>
          {[authors, year ? String(year) : null].filter(Boolean).join(" · ")}
        </SheetDescription>
        <p role="status" className="text-sm font-medium">
          {position}
          {page.data?.title ? ` · ${page.data.title}` : ""}
        </p>
        <p className="text-xs text-muted-foreground">
          {t("source.cited", { pages: pagesLabel(kind, target.first, target.last) })}
        </p>
      </SheetHeader>

      <div
        ref={body}
        role="region"
        // A region that scrolls must take focus, or a keyboard cannot reach its text.
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        aria-label={t("source.text")}
        aria-busy={found.isFetching}
        className="min-h-0 flex-1 overflow-y-auto px-4 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        {pageFailed ? (
          <Alert variant="destructive">
            <AlertDescription className="grid gap-2">
              <p>{message}</p>
              {isApiError(error, 404) ? null : (
                <div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void found.refetch()}
                  >
                    {t("source.retry")}
                  </Button>
                </div>
              )}
            </AlertDescription>
          </Alert>
        ) : page.data ? (
          <MarkdownMath>{page.data.markdown}</MarkdownMath>
        ) : (
          <div className="grid gap-3" aria-label={t("source.loading")}>
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 border-t p-4">
        <Button
          type="button"
          variant="outline"
          disabled={target.page <= 1}
          onClick={() => moveSource(target.page - 1)}
        >
          <ChevronLeft aria-hidden="true" />
          {t(unit === "section" ? "source.previousSection" : "source.previousPage")}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={total === null || target.page >= total}
          onClick={() => moveSource(target.page + 1)}
        >
          {t(unit === "section" ? "source.nextSection" : "source.nextPage")}
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </>
  );
}
