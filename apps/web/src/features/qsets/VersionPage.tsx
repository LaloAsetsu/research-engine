import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";
import { downloadExport, useCreateSet, useMe, useVersionPage } from "./api";
import { formatDate } from "./format";
import {
  PAGE_SIZE,
  readFilters,
  readOffset,
  withFilters,
  withOffset,
  withoutFilters,
  type FilterKey,
} from "./filters";
import { BUSY } from "./fields";
import { ItemFilters } from "./ItemFilters";
import { ItemsTable } from "./ItemsTable";
import { ProblemView } from "./ProblemView";
import { ReviewBadge, StatusBadge } from "./ReviewBadge";
import { canEditDraft } from "./rules";
import { Section } from "./Section";
import { REVIEW_STATES, versionPath, type ItemPage } from "./types";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-sm break-words">{children}</dd>
    </div>
  );
}

function Summary({ page }: { page: ItemPage }) {
  const { t, i18n } = useTranslation();
  const { version, header, review } = page;
  const source = header.source;
  return (
    <div className="grid gap-4">
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label={t("sets.version.owner")}>{version.owner}</Fact>
        <Fact label={t("sets.version.items")}>{version.items}</Fact>
        <Fact label={t("sets.version.corpus")}>{version.corpus ?? ""}</Fact>
        <Fact label={t("sets.version.updated")}>
          {formatDate(version.updated_at, i18n.language)}
        </Fact>
        <Fact label={t("sets.version.md5")}>
          <code data-testid="version-md5" className="text-xs break-all">
            {version.md5}
          </code>
        </Fact>
        {version.frozen_at ? (
          <Fact label={t("sets.version.frozenAt")}>
            {formatDate(version.frozen_at, i18n.language)}
          </Fact>
        ) : null}
        {header.notes ? <Fact label={t("sets.version.notes")}>{header.notes}</Fact> : null}
        {source ? (
          <Fact label={t("sets.version.source")}>
            {source.file} <code className="text-xs">{source.md5}</code>
          </Fact>
        ) : null}
      </dl>
      <div>
        <h3 className="mb-1 text-sm font-medium">{t("sets.version.progress")}</h3>
        <ul className="flex flex-wrap gap-3">
          {REVIEW_STATES.map((state) => (
            <li key={state} className="flex items-center gap-1.5 text-sm">
              <ReviewBadge state={state} />
              <span>{review[state]}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** The offset of the last page: the address can name a page beyond the end. */
function lastOffset(total: number): number {
  return total === 0 ? 0 : Math.floor((total - 1) / PAGE_SIZE) * PAGE_SIZE;
}

/** Both buttons stay focusable while the next page loads (`aria-disabled`) and ignore clicks until it is there. */
function Pager({
  page,
  loading,
  onOffset,
}: {
  page: ItemPage;
  loading: boolean;
  onOffset: (offset: number) => void;
}) {
  const { t } = useTranslation();
  const hasPrevious = page.offset > 0;
  const hasNext = page.offset + page.items.length < page.total;
  if (!hasPrevious && !hasNext) return null;
  return (
    <nav aria-label={t("sets.items.pages")} className="flex items-center gap-2">
      <Button
        variant="outline"
        disabled={!hasPrevious}
        aria-disabled={loading || undefined}
        className={BUSY}
        onClick={() => {
          if (!loading) {
            onOffset(Math.max(0, Math.min(page.offset - PAGE_SIZE, lastOffset(page.total))));
          }
        }}
      >
        {t("sets.items.previous")}
      </Button>
      <Button
        variant="outline"
        disabled={!hasNext}
        aria-disabled={loading || undefined}
        className={BUSY}
        onClick={() => {
          if (!loading) onOffset(page.offset + PAGE_SIZE);
        }}
      >
        {t("sets.items.next")}
      </Button>
    </nav>
  );
}

export function VersionPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { name = "", version = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const offset = readOffset(params);
  const page = useVersionPage(name, version, filters, offset);
  const me = useMe();
  const next = useCreateSet();
  const tableBox = useRef<HTMLDivElement>(null);
  const paged = useRef(false);
  const [exportState, setExportState] = useState<
    | { kind: "idle" }
    | { kind: "busy" }
    | { kind: "done"; filename: string }
    | { kind: "error"; error: unknown }
  >({ kind: "idle" });

  const title = `${name} ${version}`;

  // After a click on Previous or Next, once the new page is on screen, focus goes to the table
  // (a named, scrollable region) so a screen reader says where the person is.
  const loadingPage = page.isPlaceholderData;
  const shownOffset = page.data?.offset;
  useEffect(() => {
    if (!paged.current || loadingPage) return;
    paged.current = false;
    tableBox.current?.querySelector<HTMLElement>('[role="region"]')?.focus();
  }, [loadingPage, shownOffset]);

  if (page.isPending) {
    return (
      <>
        <PageHeader title={title} />
        <div role="status" aria-label={t("sets.loading")} className="grid gap-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </>
    );
  }
  if (page.isError) {
    return (
      <>
        <PageHeader title={title} />
        <div className="grid gap-3">
          <ProblemView error={page.error} />
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => void page.refetch()}>
              {t("sets.tryAgain")}
            </Button>
            <Button variant="outline" asChild>
              <Link to="/sets">{t("sets.back")}</Link>
            </Button>
          </div>
        </div>
      </>
    );
  }

  const data = page.data;
  const frozen = data.version.status === "frozen";
  const mayEdit = canEditDraft(me.data, data.version);

  const setFilters = (updates: Partial<Record<FilterKey, string | undefined>>) => {
    setParams((current) => withFilters(current, updates));
  };

  async function exportFile() {
    setExportState({ kind: "busy" });
    try {
      const { filename } = await downloadExport({ name, version });
      setExportState({ kind: "done", filename });
    } catch (error) {
      setExportState({ kind: "error", error });
    }
  }

  return (
    <>
      <PageHeader title={title}>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={data.version.status} />
          {frozen ? <Badge variant="outline">{t("sets.version.fixed")}</Badge> : null}
          <Link
            to="/sets"
            className="text-sm underline underline-offset-4 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            {t("sets.back")}
          </Link>
        </div>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        <Section
          title={t("sets.version.title")}
          description={frozen ? t("sets.version.frozenNote") : t("sets.version.draftNote")}
          action={
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => void exportFile()}
                disabled={exportState.kind === "busy"}
              >
                {t("sets.version.export")}
              </Button>
              {mayEdit ? (
                <Button asChild variant="outline">
                  <Link to={`${versionPath(name, version)}/new-item`}>
                    {t("sets.version.addItem")}
                  </Link>
                </Button>
              ) : null}
              {!frozen ? (
                <Button asChild variant={mayEdit ? "default" : "outline"}>
                  <Link to={`${versionPath(name, version)}/freeze`}>
                    {mayEdit ? t("sets.version.freeze") : t("sets.version.seePlan")}
                  </Link>
                </Button>
              ) : null}
              <Button
                variant="outline"
                disabled={next.isPending}
                onClick={() =>
                  next.mutate(
                    { name, copy_from: version },
                    {
                      onSuccess: (created) => {
                        if (created) void navigate(versionPath(created.name, created.version));
                      },
                    },
                  )
                }
              >
                {t("sets.version.next")}
              </Button>
            </div>
          }
        >
          <Summary page={data} />
          {exportState.kind === "done" ? (
            <p role="status" className="text-sm">
              {t("sets.version.exported", {
                filename: exportState.filename,
                md5: data.version.md5,
              })}
            </p>
          ) : null}
          {exportState.kind === "error" ? <ProblemView error={exportState.error} /> : null}
          {next.isError ? <ProblemView error={next.error} /> : null}
        </Section>

        <Section title={t("sets.items.title")} description={t("sets.items.description")}>
          <ItemFilters
            filters={filters}
            onChange={setFilters}
            onClear={() => setParams((current) => withoutFilters(current))}
          />
          {data.items.length === 0 && data.total > 0 && data.offset > 0 ? (
            <div className="grid justify-items-start gap-2">
              <p className="text-muted-foreground">
                {t("sets.items.pastEnd", { offset: data.offset, total: data.total })}
              </p>
              <Button
                variant="outline"
                onClick={() => setParams((current) => withOffset(current, lastOffset(data.total)))}
              >
                {t("sets.items.lastPage")}
              </Button>
            </div>
          ) : data.items.length === 0 ? (
            <p className="text-muted-foreground">
              {data.total === 0 && Object.keys(filters).length === 0
                ? t("sets.items.none")
                : t("sets.items.noMatch")}
            </p>
          ) : (
            <div ref={tableBox}>
              <ItemsTable page={data} />
            </div>
          )}
          <Pager
            page={data}
            loading={page.isPlaceholderData}
            onOffset={(value) => {
              paged.current = true;
              setParams((current) => withOffset(current, value));
            }}
          />
        </Section>
      </div>
    </>
  );
}

export { VersionPage as Component };
