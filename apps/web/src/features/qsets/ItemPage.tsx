import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";
import { useItemDetail, useMe, useVersionPage } from "./api";
import { newItemTemplate, text } from "./itemDraft";
import { ItemWorkspace } from "./ItemWorkspace";
import { ProblemView } from "./ProblemView";
import { ReviewBadge, StatusBadge } from "./ReviewBadge";
import { ReviewHistory } from "./ReviewHistory";
import { canEditDraft } from "./rules";
import { Section } from "./Section";
import { versionPath, type ItemDetail } from "./types";

function BackLink({ name, version }: { name: string; version: string }) {
  const { t } = useTranslation();
  return (
    <Link
      to={versionPath(name, version)}
      className="text-sm underline underline-offset-4 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      {t("sets.item.back", { name, version })}
    </Link>
  );
}

function Loading() {
  const { t } = useTranslation();
  return (
    <div role="status" aria-label={t("sets.loading")} className="grid gap-2">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

/** A frozen item: nothing to edit, so its fields are read and its decisions are listed. */
function FrozenItem({ detail, language }: { detail: ItemDetail; language: string }) {
  const { t } = useTranslation();
  const { item } = detail;
  const rows: [string, string][] = [
    ["lang", text(item, "lang")],
    ["form", text(item, "form")],
    ["content", text(item, "content")],
    ["corpus", text(item, "corpus")],
    ["split", text(item, "split")],
    ["question", text(item, "question")],
    ["answer", text(item, "answer")],
    ["rationale", text(item, "rationale")],
    ["notes", text(item, "notes")],
  ];
  return (
    <>
      <Section title={t("sets.item.frozenTitle")} description={t("sets.item.frozenNote")}>
        <dl className="grid gap-3 sm:grid-cols-2">
          {rows
            .filter(([, value]) => value !== "")
            .map(([key, value]) => (
              <div key={key} className="grid gap-0.5">
                <dt className="text-xs text-muted-foreground">{key}</dt>
                <dd className="text-sm break-words whitespace-pre-wrap">{value}</dd>
              </div>
            ))}
        </dl>
        <details>
          <summary className="cursor-pointer text-sm">{t("sets.item.fullJson")}</summary>
          <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-muted p-3 text-xs">
            {JSON.stringify(item, null, 2)}
          </pre>
        </details>
      </Section>
      <Section title={t("sets.review.history")}>
        <ReviewHistory
          reviews={detail.reviews}
          currentMd5={detail.version.md5}
          language={language}
        />
      </Section>
    </>
  );
}

export function ItemPage() {
  const { t, i18n } = useTranslation();
  const { name = "", version = "", id = "" } = useParams();
  const detail = useItemDetail(name, version, id);
  const me = useMe();
  const [resetKey, setResetKey] = useState(0);

  if (detail.isPending) {
    return (
      <>
        <PageHeader title={id} />
        <Loading />
      </>
    );
  }
  if (detail.isError) {
    return (
      <>
        <PageHeader title={id} />
        <div className="grid gap-3">
          <ProblemView error={detail.error} />
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => void detail.refetch()}>
              {t("sets.tryAgain")}
            </Button>
            <Button asChild variant="outline">
              <Link to={versionPath(name, version)}>{t("sets.item.back", { name, version })}</Link>
            </Button>
          </div>
        </div>
      </>
    );
  }

  const data = detail.data;
  const frozen = data.version.status === "frozen";
  const mayEdit = canEditDraft(me.data, data.version);

  return (
    <>
      <PageHeader title={id}>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={data.version.status} />
          <ReviewBadge state={data.review.state} />
          {data.review.gold ? <Badge>{t("sets.item.gold")}</Badge> : null}
          <BackLink name={name} version={version} />
        </div>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        {frozen ? (
          <FrozenItem detail={data} language={i18n.language} />
        ) : (
          <ItemWorkspace
            key={resetKey}
            name={name}
            version={version}
            detail={data}
            base={{ etag: data.version.etag, corpus: data.version.corpus }}
            initial={data.item}
            me={me.data}
            mayEdit={mayEdit}
            owner={data.version.owner}
            language={i18n.language}
            onReload={() => {
              void detail.refetch().then(() => setResetKey((key) => key + 1));
            }}
            onRefresh={() => detail.refetch()}
          />
        )}
      </div>
    </>
  );
}

export function NewItemPage() {
  const { t, i18n } = useTranslation();
  const { name = "", version = "" } = useParams();
  const page = useVersionPage(name, version, {}, 0);
  const me = useMe();
  const title = t("sets.editor.newTitle");

  if (page.isPending) {
    return (
      <>
        <PageHeader title={title} />
        <Loading />
      </>
    );
  }
  if (page.isError) {
    return (
      <>
        <PageHeader title={title} />
        <ProblemView error={page.error} />
      </>
    );
  }
  const current = page.data.version;
  const mayEdit = canEditDraft(me.data, current);
  return (
    <>
      <PageHeader title={title}>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={current.status} />
          <BackLink name={name} version={version} />
        </div>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        {mayEdit ? (
          <ItemWorkspace
            name={name}
            version={version}
            detail={null}
            base={{ etag: current.etag, corpus: current.corpus }}
            initial={newItemTemplate(current.corpus)}
            me={me.data}
            mayEdit
            owner={current.owner}
            language={i18n.language}
            onReload={() => undefined}
            onRefresh={() => page.refetch()}
          />
        ) : (
          <p className="text-muted-foreground">
            {t("sets.item.cannotAdd", { owner: current.owner })}
          </p>
        )}
      </div>
    </>
  );
}

export { ItemPage as Component };
