import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useBlocker, useNavigate } from "react-router";
import { isApiError, type ProblemField } from "@/shared/api/errors";
import type { Me } from "@/shared/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { useAddItem, useDeleteItem, useSaveItem, useValidateItem } from "./api";
import { BehindAlert, MergeReportView, RemoteNotice, type MergeReport } from "./EditNotices";
import { BUSY, DESTRUCTIVE } from "./fields";
import { markFocus, restoreFocus, type FocusMark } from "./focusMark";
import { ItemFields } from "./ItemFields";
import {
  changedFields,
  changesBetween,
  hasChanges,
  isPlainObject,
  mergeItems,
  partOf,
  sameValue,
  standing,
  type FieldConflict,
} from "./itemDraft";
import { JsonField } from "./JsonField";
import { ProblemList, ProblemView } from "./ProblemView";
import { ReviewHistory } from "./ReviewHistory";
import { ReviewPanel } from "./ReviewPanel";
import { conflictKind } from "./problems";
import { Section } from "./Section";
import { itemPath, versionPath, type Item, type ItemDetail } from "./types";

/** An item as the server holds it, with the etag of the draft it was read from and when it was read. */
export interface Fresh {
  /** Null when there is no item to read (a new item): only the draft's etag moved. */
  item: Item | null;
  etag: string;
  /** When the read happened (ms). A copy read earlier than the one the editor stands on is stale. */
  at: number;
}

interface Read {
  item: Item;
  etag: string;
  at: number;
}

interface Props {
  name: string;
  version: string;
  /** The item as the server holds it now (it changes when the query is read again). Null for a new item. */
  detail: ItemDetail | null;
  /** When `detail` was read (ms). */
  readAt: number;
  /** The draft's etag and corpus: a new item has no item to read them from. */
  draftVersion: { etag: string; corpus: string | null };
  initial: Item;
  me: Me | undefined;
  mayEdit: boolean;
  owner: string;
  language: string;
  /** Read the item (or, for a new item, the draft) again from the server. */
  onRefresh: () => Promise<Fresh | null>;
}

type Notice = { kind: "saved" } | { kind: "valid" } | { kind: "kept" } | { kind: "loaded" } | null;

/** Where focus goes once the page has changed: the first error of a refused form, or a notice. */
interface FocusRequest {
  n: number;
  to: "error" | "notice";
}

/** The app's sign-in route. A session that ends sends the person there whatever they were doing. */
const SIGN_IN = "/login";

const FIRST_ERROR = '[aria-invalid="true"], [data-first-error]';
const NOTICE = "[data-note-focus]";
const FOCUS_RING = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";

/** A block that can take focus when it explains what went wrong, so the keyboard lands on it. */
function ErrorBox({ children }: { children: ReactNode }) {
  return (
    <div
      data-first-error
      tabIndex={-1}
      className="rounded-lg outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      {children}
    </div>
  );
}

/**
 * The editor and the review of one item of a draft. It owns the text being edited and the
 * version of the item that text was built from (`base`). Every write carries the etag of `base`
 * and every comparison with the server is against it, so a read that lands under unsaved edits
 * (a reconnect, a review that failed) never moves the ground the edits stand on.
 */
export function ItemWorkspace({
  name,
  version,
  detail,
  readAt,
  draftVersion,
  initial,
  me,
  mayEdit,
  owner,
  language,
  onRefresh,
}: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const ref = { name, version };
  const isNew = detail === null;
  const [base, setBase] = useState<Read>({
    item: initial,
    etag: detail?.version.etag ?? draftVersion.etag,
    at: readAt,
  });
  const [draft, setDraft] = useState<Item>(initial);
  const [formKey, setFormKey] = useState(0);
  // Parts of the form that keep their own text (the tags, the key facts, the boxes of JSON) start
  // over only when the text they show changed under them, so that typing, focus and a row just
  // added survive a save that changes something else. "$" is the box of the whole item.
  const [epochs, setEpochs] = useState<Record<string, number>>({});
  const [tab, setTab] = useState("form");
  // The text of the box that could not be read when a switch of view was refused.
  const [refusedFor, setRefusedFor] = useState<string | null>(null);
  const leaving = useRef(false);
  const [jsonProblems, setJsonProblems] = useState<Record<string, string>>({});
  // The same list for code that runs outside a render (the answer to a save).
  const jsonProblemsRef = useRef<Record<string, string>>({});
  // The draft for code that runs outside a render (the answer to a save). `commitDraft` writes it
  // together with the state, so it never waits for an effect; the effect below covers the one
  // place that sets the draft while rendering.
  const draftRef = useRef<Item>(initial);
  // Where focus stood in a part of the form that is being drawn again, to put it back after.
  const focusMark = useRef<FocusMark | null>(null);
  const [problems, setProblems] = useState<ProblemField[]>([]);
  const [notice, setNotice] = useState<Notice>(null);
  const [remote, setRemote] = useState<string[] | null>(null);
  const [report, setReport] = useState<MergeReport | null>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [proposeSignal, setProposeSignal] = useState(0);
  const [focus, setFocus] = useState<FocusRequest>({ n: 0, to: "error" });
  const [root, setRoot] = useState<HTMLDivElement | null>(null);

  // What the server holds, as far as the editor knows. A copy read before the one the editor
  // stands on can still be in the props for a render after the editor took a newer read
  // (the cache notifies the screen a moment after a read resolves): it is not "latest".
  const read: Read | null = detail
    ? { item: detail.item, etag: detail.version.etag, at: readAt }
    : null;
  const latest = read && read.at >= base.at ? read : null;

  /** Set the draft and the ref that code outside a render reads, in one step. */
  function commitDraft(next: Item) {
    draftRef.current = next;
    setDraft(next);
  }

  function show(to: FocusRequest["to"]) {
    setFocus((current) => ({ n: current.n + 1, to }));
  }

  /** Rebuild the editor from `fresh`; the text, the notices and the problems start over. */
  function reset(fresh: Read) {
    setBase(fresh);
    setDraft(fresh.item);
    setFormKey((key) => key + 1);
    setProblems([]);
    setSaveError(null);
    setDeleteError(null);
    setConfirmDelete(false);
    setNotice(null);
    setReport(null);
    setRemote(null);
  }

  // A box of JSON that cannot be read yet is an edit the draft does not show: the draft still holds
  // the last good value. It counts as unsaved text everywhere the draft is compared.
  const blocked = Object.values(jsonProblems)[0] ?? null;
  const unreadable = blocked !== null;

  // The server holds other text than the editor was built from. With nothing to lose the editor
  // shows it (and says so); with the person's own save, it only takes the new etag.
  // The two branches set state while rendering, which React allows for the component's own state:
  // the render runs again at once and the second pass finds the base up to date, so it ends there.
  const stand = latest ? standing(base.item, draft, latest.item, unreadable) : "same";
  if (latest && stand === "adopt") {
    setBase(latest);
  } else if (latest && stand === "rebuild") {
    const fields = changedFields(base.item, latest.item);
    reset(latest);
    setRemote(fields);
  }

  // The draft can also be set while rendering (a rebuild, above): this keeps the ref in step.
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  /**
   * Start over the parts of the form that show the fields in `fields` and keep their own text,
   * and give focus back to the control that had it. A box of JSON that cannot be read is left
   * alone: starting it over would drop what was typed.
   */
  function restartParts(fields: string[]) {
    if (fields.length === 0) return;
    const broken = jsonProblemsRef.current;
    const parts = new Set(["$", ...fields.map(partOf)].filter((part) => !(part in broken)));
    if (parts.size === 0) return;
    focusMark.current = markFocus(root, parts);
    setEpochs((current) => {
      const next = { ...current };
      for (const part of parts) next[part] = (next[part] ?? 0) + 1;
      return next;
    });
  }

  // Runs once the parts are drawn again, before the browser paints.
  useLayoutEffect(() => {
    const mark = focusMark.current;
    if (mark === null) return;
    focusMark.current = null;
    restoreFocus(root, mark);
  }, [epochs, root]);

  const save = useSaveItem(ref, (stored, sent) => {
    // The editor stands on what the server stored. The fields stay editable while the save is in
    // flight, so the draft may hold more than was sent: that text is compared with `sent`, never
    // with the answer, and goes on top of the stored item as unsaved changes. If the server
    // stored something other than what was sent, the fields it changed show what it stored.
    if (!stored?.item) return;
    const fresh = stored.item;
    setBase({ item: fresh, etag: stored.version.etag, at: Date.now() });
    if (sameValue(fresh, sent)) return;
    const current = draftRef.current;
    const merged = mergeItems(sent, current, fresh).merged;
    commitDraft(merged);
    // Only what the server changed is shown again; the rest of the form is left as it is.
    restartParts(changedFields(current, merged));
  });
  const add = useAddItem(ref);
  const del = useDeleteItem(ref);
  const validate = useValidateItem();

  const setJsonProblem = useCallback((key: string, message: string | null) => {
    const current = jsonProblemsRef.current;
    if (message === null) {
      if (!(key in current)) return;
      jsonProblemsRef.current = Object.fromEntries(
        Object.entries(current).filter(([other]) => other !== key),
      );
    } else {
      if (current[key] === message) return;
      jsonProblemsRef.current = { ...current, [key]: message };
    }
    setJsonProblems(jsonProblemsRef.current);
  }, []);

  useEffect(() => {
    if (focus.n === 0 || root === null) return;
    const target = root.querySelector<HTMLElement>(focus.to === "error" ? FIRST_ERROR : NOTICE);
    target?.focus();
  }, [focus, root]);

  const changes = changesBetween(isNew ? {} : base.item, draft);
  const dirty = unreadable || (isNew ? !sameValue(draft, initial) : hasChanges(changes));

  // Edits live only in this page: leaving it (a link, the back button, closing the tab) asks first.
  const blocker = useBlocker(
    ({ nextLocation }) => dirty && !leaving.current && nextLocation.pathname !== SIGN_IN,
  );
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const blockedNav = blocker.state === "blocked";
  // The question to the person takes focus when it appears, so the keyboard lands on it.
  const focusOnMount = useCallback((element: HTMLElement | null) => element?.focus(), []);

  function changeTab(next: string) {
    if (blocked !== null && next !== tab) {
      setRefusedFor(blocked);
      return;
    }
    setRefusedFor(null);
    setTab(next);
  }
  const id = typeof draft.id === "string" ? draft.id : "";
  const storedId = typeof base.item.id === "string" ? base.item.id : id;
  const behind = stand === "behind" && latest !== null;
  // The etag of the draft when the item has not moved under the editor; otherwise the one the
  // editor was built from, which the server refuses if the draft moved.
  const writeEtag = isNew
    ? draftVersion.etag
    : latest && stand === "same"
      ? latest.etag
      : base.etag;
  const preview = behind ? mergeItems(base.item, draft, latest.item) : null;

  function clearOutcome() {
    setNotice(null);
    setSaveError(null);
    setDeleteError(null);
  }

  function onSaved() {
    setProblems([]);
    setReport(null);
    setRemote(null);
    setNotice({ kind: "saved" });
  }

  function onFailed(error: unknown) {
    setSaveError(error);
    const list = isApiError(error, 422) ? error.errors : [];
    setProblems(list);
    show("error");
  }

  function submitSave() {
    if (busy) return;
    clearOutcome();
    if (blocked !== null) return;
    if (isNew) {
      add.mutate(
        { item: draft, etag: writeEtag },
        {
          onSuccess: () => {
            setProblems([]);
            leaving.current = true;
            void navigate(itemPath(name, version, id));
          },
          onError: onFailed,
        },
      );
      return;
    }
    save.mutate(
      { id: storedId, item: draft, etag: writeEtag },
      { onSuccess: onSaved, onError: onFailed },
    );
  }

  function check() {
    if (validate.isPending) return;
    clearOutcome();
    if (blocked !== null) return;
    validate.mutate(draft, {
      onSuccess: (result) => {
        const list = result?.errors ?? [];
        setProblems(list);
        setNotice(result?.ok ? { kind: "valid" } : null);
        if (list.length > 0) show("error");
      },
      onError: (error) => {
        onFailed(error);
      },
    });
  }

  function remove() {
    if (del.isPending) return;
    clearOutcome();
    if (!detail) return;
    del.mutate(
      { id: storedId, etag: writeEtag },
      {
        onSuccess: () => {
          leaving.current = true;
          void navigate(versionPath(name, version));
        },
        onError: (error) => {
          setConfirmDelete(false);
          setDeleteError(error);
          show("error");
        },
      },
    );
  }

  /** Put the person's edits on top of `fresh` and say what that did. */
  function rebase(fresh: Fresh) {
    // The draft does not hold the text of a box that cannot be read: a merge would drop it.
    if (blocked !== null) return;
    if (fresh.item === null) {
      setBase((current) => ({ ...current, etag: fresh.etag }));
      setSaveError(null);
      setNotice({ kind: "kept" });
      show("notice");
      return;
    }
    const merge = mergeItems(base.item, draft, fresh.item);
    reset({ item: fresh.item, etag: fresh.etag, at: fresh.at });
    commitDraft(merge.merged);
    if (merge.fromLatest.length === 0 && merge.conflicts.length === 0) {
      setNotice({ kind: "kept" });
    } else {
      setReport({ fromLatest: merge.fromLatest, conflicts: merge.conflicts });
    }
    show("notice");
  }

  /** Drop the edits and show what the server holds. */
  function loadLatest(fresh: Fresh) {
    if (fresh.item === null) {
      setSaveError(null);
      return;
    }
    reset({ item: fresh.item, etag: fresh.etag, at: fresh.at });
    setNotice({ kind: "loaded" });
    show("notice");
  }

  async function refreshThen(next: (fresh: Fresh) => void) {
    const fresh = await onRefresh();
    if (fresh) next(fresh);
  }

  /**
   * The box that shows `field` cannot be read (the box of the whole item shows every field). That
   * box keeps its text when the rest is drawn again, so taking the latest value now would leave
   * the box on the old text, and fixing it later would write over the value just taken.
   */
  function takeBlocked(field: string): boolean {
    return "$" in jsonProblems || partOf(field) in jsonProblems;
  }

  function useLatestValue(conflict: FieldConflict) {
    if (takeBlocked(conflict.field)) return;
    const next = { ...draft };
    if (conflict.theirs === undefined) delete next[conflict.field];
    else next[conflict.field] = conflict.theirs;
    commitDraft(next);
    restartParts([conflict.field]);
    setReport((current) =>
      current
        ? { ...current, conflicts: current.conflicts.filter((c) => c.field !== conflict.field) }
        : current,
    );
  }

  const kind = conflictKind(saveError);
  const busy = save.isPending || add.isPending || del.isPending;
  const showStale = kind === "stale" && !behind;

  return (
    <>
      <div ref={setRoot} className="grid grid-cols-[minmax(0,1fr)] gap-6">
        {blockedNav ? (
          <Alert ref={focusOnMount} variant="destructive" tabIndex={-1} className={FOCUS_RING}>
            <AlertTitle>{t("sets.editor.leaveTitle")}</AlertTitle>
            <AlertDescription>
              <p>{t("sets.editor.leaveBody")}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button type="button" variant="outline" onClick={() => blocker.reset()}>
                  {t("sets.editor.leaveStay")}
                </Button>
                <Button type="button" variant="outline" onClick={() => blocker.proceed()}>
                  {t("sets.editor.leaveGo")}
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        ) : null}
        {remote ? <RemoteNotice fields={remote} onDismiss={() => setRemote(null)} /> : null}
        {behind && latest && preview ? (
          <BehindAlert
            fields={changedFields(base.item, latest.item)}
            both={preview.conflicts.map((conflict) => conflict.field)}
            mergeBlocked={unreadable}
            onMerge={() => rebase(latest)}
            onLoadLatest={() => loadLatest(latest)}
          />
        ) : null}
        {report && report.conflicts.length + report.fromLatest.length > 0 ? (
          <MergeReportView report={report} isBlocked={takeBlocked} onUseLatest={useLatestValue} />
        ) : null}
        <Section
          title={isNew ? t("sets.editor.newTitle") : t("sets.editor.title")}
          description={
            mayEdit ? t("sets.editor.description") : t("sets.editor.proposeOnly", { owner })
          }
        >
          <Tabs value={tab} onValueChange={changeTab}>
            <TabsList aria-label={t("sets.editor.views")}>
              <TabsTrigger value="form">{t("sets.editor.formTab")}</TabsTrigger>
              <TabsTrigger value="json">{t("sets.editor.jsonTab")}</TabsTrigger>
            </TabsList>
            <TabsContent value="form" className="pt-3">
              <ItemFields
                key={formKey}
                epochs={epochs}
                draft={draft}
                onChange={(next) => {
                  commitDraft(next);
                  setNotice(null);
                }}
                problems={problems}
                idEditable={isNew}
                onProblem={setJsonProblem}
              />
            </TabsContent>
            <TabsContent value="json" className="pt-3">
              <JsonField
                key={`${formKey}-${epochs.$ ?? 0}`}
                fieldKey="$"
                label={t("sets.editor.wholeItem")}
                value={draft}
                hint={t("sets.editor.wholeItemHint")}
                problems={problems}
                wholeItem
                accept={(value) => (isPlainObject(value) ? null : t("sets.editor.mustBeObject"))}
                onChange={(value) => {
                  commitDraft(isPlainObject(value) ? value : {});
                  setNotice(null);
                }}
                onProblem={setJsonProblem}
              />
            </TabsContent>
          </Tabs>

          {blocked ? (
            <p role="alert" className="text-sm text-destructive">
              {t("sets.editor.cannotUse", { field: blocked })}
              {refusedFor === blocked ? ` ${t("sets.editor.cannotSwitch")}` : ""}
            </p>
          ) : null}

          {saveError && kind === null ? (
            <ErrorBox>
              <ProblemView error={saveError} showFields={false}>
                {problems.length > 0 ? <ProblemList problems={problems} /> : null}
              </ProblemView>
            </ErrorBox>
          ) : null}
          {showStale ? (
            <ErrorBox>
              <Alert variant="destructive">
                <AlertTitle>{t("sets.editor.staleTitle")}</AlertTitle>
                <AlertDescription>
                  <p>{t("sets.editor.staleBody")}</p>
                  {unreadable ? <p>{t("sets.editor.keepBlocked")}</p> : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {!isNew ? (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => void refreshThen(loadLatest)}
                      >
                        {t("sets.editor.loadLatest")}
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="outline"
                      disabled={unreadable}
                      onClick={() => void refreshThen(rebase)}
                    >
                      {t("sets.editor.keepEdits")}
                    </Button>
                  </div>
                </AlertDescription>
              </Alert>
            </ErrorBox>
          ) : null}
          {kind === "item-has-decisions" ? (
            <ErrorBox>
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
            </ErrorBox>
          ) : null}
          {validate.isError ? (
            <ErrorBox>
              <ProblemView error={validate.error} showFields={false} />
            </ErrorBox>
          ) : null}

          <div aria-live="polite">
            {notice?.kind === "saved" ? <p role="status">{t("sets.editor.saved")}</p> : null}
            {notice?.kind === "valid" ? <p role="status">{t("sets.editor.valid")}</p> : null}
            {notice?.kind === "loaded" ? (
              <p role="status" tabIndex={-1} data-note-focus className="outline-none">
                {t("sets.editor.loaded")}
              </p>
            ) : null}
            {notice?.kind === "kept" ? (
              <p role="status" tabIndex={-1} data-note-focus className="outline-none">
                {t("sets.editor.kept")}
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {mayEdit ? (
              <Button
                onClick={submitSave}
                disabled={blocked !== null || behind || (!isNew && !hasChanges(changes))}
                aria-disabled={busy || undefined}
                className={BUSY}
              >
                {isNew ? t("sets.editor.add") : t("sets.editor.save")}
              </Button>
            ) : null}
            <Button
              variant="outline"
              onClick={check}
              disabled={blocked !== null}
              aria-disabled={validate.isPending || undefined}
              className={BUSY}
            >
              {t("sets.editor.check")}
            </Button>
            {!isNew ? (
              <Button
                variant="outline"
                disabled={!hasChanges(changes) && !unreadable}
                aria-disabled={busy || undefined}
                className={BUSY}
                onClick={() => {
                  if (busy) return;
                  if (latest && behind) loadLatest(latest);
                  else void refreshThen(loadLatest);
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
            <ErrorBox>
              <ProblemView error={deleteError} showFields={false}>
                {conflictKind(deleteError) === "stale" ? (
                  <div className="mt-3">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void refreshThen(loadLatest)}
                    >
                      {t("sets.editor.loadLatest")}
                    </Button>
                  </div>
                ) : null}
              </ProblemView>
            </ErrorBox>
          ) : null}

          {confirmDelete ? (
            <Alert variant="destructive">
              <AlertTitle>{t("sets.editor.deleteTitle", { id })}</AlertTitle>
              <AlertDescription>
                <p>{t("sets.editor.deleteBody")}</p>
                <div className="mt-3 flex gap-2">
                  <Button
                    variant="outline"
                    className={`${DESTRUCTIVE} ${BUSY}`}
                    onClick={remove}
                    aria-disabled={del.isPending || undefined}
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
                etag={writeEtag}
                username={me?.username}
                changes={changes}
                blocked={blocked}
                waiting={behind}
                remoteFields={remote ?? []}
                focusSignal={proposeSignal}
                onRecorded={() => setRemote(null)}
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
      </div>
    </>
  );
}

function conflictDetail(error: unknown): string {
  return error instanceof Error ? error.message : "";
}
