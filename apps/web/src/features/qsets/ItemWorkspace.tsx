import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { isApiError, type ProblemField } from "@/shared/api/errors";
import type { Me } from "@/shared/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { useAddItem, useDeleteItem, useSaveItem, useValidateItem } from "./api";
import { DESTRUCTIVE } from "./fields";
import { ItemFields } from "./ItemFields";
import { changesBetween, hasChanges, isPlainObject } from "./itemDraft";
import { JsonField } from "./JsonField";
import { ProblemList, ProblemView } from "./ProblemView";
import { ReviewHistory } from "./ReviewHistory";
import { ReviewPanel } from "./ReviewPanel";
import { conflictKind } from "./problems";
import { Section } from "./Section";
import { itemPath, versionPath, type Item, type ItemDetail } from "./types";

interface Props {
  name: string;
  version: string;
  /** Null for a new item. */
  detail: ItemDetail | null;
  /** The draft's etag and corpus when the item is new (there is no item to read them from). */
  base: { etag: string; corpus: string | null };
  initial: Item;
  me: Me | undefined;
  mayEdit: boolean;
  owner: string;
  language: string;
  /** Read the item again from the server and rebuild the editor from it. */
  onReload: () => void;
  /** Read the item again but keep the edits. */
  onRefresh: () => Promise<unknown>;
}

type Notice = { kind: "saved" } | { kind: "valid" } | { kind: "kept" } | null;

/** The editor and the review of one item of a draft. It owns the text being edited. */
export function ItemWorkspace({
  name,
  version,
  detail,
  base,
  initial,
  me,
  mayEdit,
  owner,
  language,
  onReload,
  onRefresh,
}: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const ref = { name, version };
  const isNew = detail === null;
  const [draft, setDraft] = useState<Item>(initial);
  const [jsonProblems, setJsonProblems] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<ProblemField[]>([]);
  const [notice, setNotice] = useState<Notice>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [proposeSignal, setProposeSignal] = useState(0);

  const save = useSaveItem(ref);
  const add = useAddItem(ref);
  const del = useDeleteItem(ref);
  const validate = useValidateItem();

  const setJsonProblem = useCallback((key: string, message: string | null) => {
    setJsonProblems((current) => {
      if (message === null) {
        if (!(key in current)) return current;
        return Object.fromEntries(Object.entries(current).filter(([other]) => other !== key));
      }
      return current[key] === message ? current : { ...current, [key]: message };
    });
  }, []);

  const stored = detail?.item ?? {};
  const changes = changesBetween(isNew ? {} : stored, draft);
  const blocked = Object.values(jsonProblems)[0] ?? null;
  const id = typeof draft.id === "string" ? draft.id : "";
  const storedId = typeof stored.id === "string" ? stored.id : id;

  function clearOutcome() {
    setNotice(null);
    setSaveError(null);
    setDeleteError(null);
  }

  function onSaved() {
    setProblems([]);
    setNotice({ kind: "saved" });
  }

  function onFailed(error: unknown) {
    setSaveError(error);
    const list = isApiError(error, 422) ? error.errors : [];
    setProblems(list);
  }

  function submitSave() {
    clearOutcome();
    if (blocked !== null) return;
    if (isNew) {
      add.mutate(
        { item: draft, etag: base.etag },
        {
          onSuccess: () => {
            setProblems([]);
            void navigate(itemPath(name, version, id));
          },
          onError: onFailed,
        },
      );
      return;
    }
    save.mutate(
      { id: storedId, item: draft, etag: detail.version.etag },
      { onSuccess: onSaved, onError: onFailed },
    );
  }

  function check() {
    clearOutcome();
    if (blocked !== null) return;
    validate.mutate(draft, {
      onSuccess: (result) => {
        setProblems(result?.errors ?? []);
        setNotice(result?.ok ? { kind: "valid" } : null);
      },
      onError: onFailed,
    });
  }

  function remove() {
    clearOutcome();
    if (!detail) return;
    del.mutate(
      { id: storedId, etag: detail.version.etag },
      {
        onSuccess: () => void navigate(versionPath(name, version)),
        onError: (error) => {
          setConfirmDelete(false);
          setDeleteError(error);
        },
      },
    );
  }

  const kind = conflictKind(saveError);
  const busy = save.isPending || add.isPending || del.isPending;

  return (
    <>
      <Section
        title={isNew ? t("sets.editor.newTitle") : t("sets.editor.title")}
        description={
          mayEdit ? t("sets.editor.description") : t("sets.editor.proposeOnly", { owner })
        }
      >
        <Tabs defaultValue="form">
          <TabsList aria-label={t("sets.editor.views")}>
            <TabsTrigger value="form">{t("sets.editor.formTab")}</TabsTrigger>
            <TabsTrigger value="json">{t("sets.editor.jsonTab")}</TabsTrigger>
          </TabsList>
          <TabsContent value="form" className="pt-3">
            <ItemFields
              draft={draft}
              onChange={(next) => {
                setDraft(next);
                setNotice(null);
              }}
              problems={problems}
              idEditable={isNew}
              onProblem={setJsonProblem}
            />
          </TabsContent>
          <TabsContent value="json" className="pt-3">
            <JsonField
              fieldKey="$"
              label={t("sets.editor.wholeItem")}
              value={draft}
              hint={t("sets.editor.wholeItemHint")}
              problems={problems}
              accept={(value) => (isPlainObject(value) ? null : t("sets.editor.mustBeObject"))}
              onChange={(value) => {
                setDraft(isPlainObject(value) ? value : {});
                setNotice(null);
              }}
              onProblem={setJsonProblem}
            />
          </TabsContent>
        </Tabs>

        {blocked ? (
          <p role="alert" className="text-sm text-destructive">
            {t("sets.editor.cannotUse", { field: blocked })}
          </p>
        ) : null}

        {saveError && kind === null ? (
          <ProblemView error={saveError} showFields={false}>
            {problems.length > 0 ? <ProblemList problems={problems} /> : null}
          </ProblemView>
        ) : null}
        {kind === "stale" ? (
          <Alert variant="destructive">
            <AlertTitle>{t("sets.editor.staleTitle")}</AlertTitle>
            <AlertDescription>
              <p>{t("sets.editor.staleBody")}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button type="button" variant="outline" onClick={onReload}>
                  {t("sets.editor.loadLatest")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    void onRefresh().then(() => {
                      setSaveError(null);
                      setNotice({ kind: "kept" });
                    });
                  }}
                >
                  {t("sets.editor.keepEdits")}
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        ) : null}
        {kind === "item-has-decisions" ? (
          <Alert variant="destructive">
            <AlertTitle>{t("sets.editor.decisionsTitle")}</AlertTitle>
            <AlertDescription>
              <p>{t("sets.editor.decisionsBody")}</p>
              <p className="text-muted-foreground">{conflictDetail(saveError)}</p>
              <div className="mt-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setProposeSignal((count) => count + 1)}
                >
                  {t("sets.editor.proposeInstead")}
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        ) : null}
        {validate.isError ? <ProblemView error={validate.error} showFields={false} /> : null}

        <div aria-live="polite">
          {notice?.kind === "saved" ? <p role="status">{t("sets.editor.saved")}</p> : null}
          {notice?.kind === "valid" ? <p role="status">{t("sets.editor.valid")}</p> : null}
          {notice?.kind === "kept" ? <p role="status">{t("sets.editor.kept")}</p> : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {mayEdit ? (
            <Button
              onClick={submitSave}
              disabled={busy || blocked !== null || (!isNew && !hasChanges(changes))}
            >
              {isNew ? t("sets.editor.add") : t("sets.editor.save")}
            </Button>
          ) : null}
          <Button
            variant="outline"
            onClick={check}
            disabled={validate.isPending || blocked !== null}
          >
            {t("sets.editor.check")}
          </Button>
          {!isNew ? (
            <Button
              variant="outline"
              disabled={!hasChanges(changes)}
              onClick={() => {
                clearOutcome();
                setProblems([]);
                onReload();
              }}
            >
              {t("sets.editor.discard")}
            </Button>
          ) : null}
          {mayEdit && !isNew && !confirmDelete ? (
            <Button
              variant="outline"
              className={DESTRUCTIVE}
              onClick={() => setConfirmDelete(true)}
            >
              {t("sets.editor.delete")}
            </Button>
          ) : null}
          {hasChanges(changes) && !isNew ? (
            <span className="text-sm text-muted-foreground">
              {t("sets.editor.unsaved", {
                count: Object.keys(changes.changed).length + changes.removed.length,
              })}
            </span>
          ) : null}
        </div>

        {deleteError ? (
          <ProblemView error={deleteError} showFields={false}>
            {conflictKind(deleteError) === "stale" ? (
              <div className="mt-3">
                <Button type="button" variant="outline" onClick={onReload}>
                  {t("sets.editor.loadLatest")}
                </Button>
              </div>
            ) : null}
          </ProblemView>
        ) : null}

        {confirmDelete ? (
          <Alert variant="destructive">
            <AlertTitle>{t("sets.editor.deleteTitle", { id })}</AlertTitle>
            <AlertDescription>
              <p>{t("sets.editor.deleteBody")}</p>
              <div className="mt-3 flex gap-2">
                <Button
                  variant="outline"
                  className={DESTRUCTIVE}
                  onClick={remove}
                  disabled={del.isPending}
                >
                  {t("sets.editor.deleteConfirm")}
                </Button>
                <Button variant="outline" onClick={() => setConfirmDelete(false)}>
                  {t("sets.editor.deleteCancel")}
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        ) : null}
      </Section>

      {detail ? (
        <>
          <Section title={t("sets.review.title")} description={t("sets.review.description")}>
            <ReviewPanel
              name={name}
              version={version}
              id={storedId}
              detail={detail}
              username={me?.username}
              changes={changes}
              blocked={blocked}
              focusSignal={proposeSignal}
            />
          </Section>
          <Section title={t("sets.review.history")}>
            <ReviewHistory
              reviews={detail.reviews}
              currentMd5={detail.version.md5}
              language={language}
            />
          </Section>
        </>
      ) : null}
    </>
  );
}

function conflictDetail(error: unknown): string {
  return error instanceof Error ? error.message : "";
}
