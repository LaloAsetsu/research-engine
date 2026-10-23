import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/shared/api/errors";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { useAddReview } from "./api";
import { AreaField, DESTRUCTIVE } from "./fields";
import { describeValue, hasChanges, type Changes } from "./itemDraft";
import { ProblemView } from "./ProblemView";
import { ReviewBadge } from "./ReviewBadge";
import { latestDecisionBy } from "./rules";
import type { ItemDetail, Verdict } from "./types";

interface Props {
  name: string;
  version: string;
  id: string;
  detail: ItemDetail;
  username: string | undefined;
  /** What the editor changes against the item as stored: the content of an edit decision. */
  changes: Changes;
  /** Text in the editor that cannot be read yet: a decision would send something else. */
  blocked: string | null;
  /** Changes when the person asks to propose their edits as a decision: the note takes focus. */
  focusSignal: number;
}

/** Accept, edit or reject one item. A decision is recorded against the draft that was read. */
export function ReviewPanel({
  name,
  version,
  id,
  detail,
  username,
  changes,
  blocked,
  focusSignal,
}: Props) {
  const { t } = useTranslation();
  const add = useAddReview({ name, version });
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState<string | null>(null);
  const [recorded, setRecorded] = useState<Verdict | null>(null);
  const [reloaded, setReloaded] = useState(false);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (focusSignal > 0) noteRef.current?.focus();
  }, [focusSignal]);

  const { review, reviews } = detail;
  const mine = latestDecisionBy(reviews, username);
  const stateKey = review.state;

  function decide(verdict: Verdict) {
    setRecorded(null);
    setReloaded(false);
    const clean = note.trim();
    if (verdict !== "accept" && clean === "") {
      setNoteError(t("sets.review.noteRequired"));
      noteRef.current?.focus();
      return;
    }
    setNoteError(null);
    if (verdict === "edit" && !hasChanges(changes)) {
      setNoteError(t("sets.review.noChanges"));
      return;
    }
    add.mutate(
      {
        etag: detail.version.etag,
        review: {
          item_id: id,
          verdict,
          ...(clean ? { note: clean } : {}),
          ...(verdict === "edit" ? { edit: changes.changed } : {}),
        },
      },
      {
        onSuccess: () => {
          setRecorded(verdict);
          setNote("");
        },
        // A 409 means the draft moved while this item was open. The queries are read again
        // (the mutation invalidates them), the note stays, and the person decides again.
        onError: (error) => setReloaded(isApiError(error, 409)),
      },
    );
  }

  const blockedMessage = blocked ? t("sets.review.blocked", { field: blocked }) : null;
  const changed = Object.entries(changes.changed);

  return (
    <div className="grid gap-4">
      <div className="grid gap-1" aria-live="polite">
        <div className="flex flex-wrap items-center gap-2">
          <ReviewBadge state={stateKey} />
          <span className="text-sm">
            {t(`sets.review.explain.${stateKey}`, {
              names: review.accepted_by.join(", "),
              by: review.deciding?.reviewer ?? "",
            })}
          </span>
        </div>
        {mine ? (
          <p className="text-sm text-muted-foreground">
            {t("sets.review.yours", { verdict: t(`sets.review.verdict.${mine.verdict}`) })}
          </p>
        ) : null}
      </div>

      <AreaField
        label={t("sets.review.note")}
        value={note}
        onValueChange={setNote}
        hint={t("sets.review.noteHint")}
        errors={noteError ? [noteError] : undefined}
        ref={noteRef}
        maxLength={4000}
      />

      <div className="grid gap-1">
        <h3 className="text-sm font-medium">{t("sets.review.changesTitle")}</h3>
        {changed.length > 0 ? (
          <p className="text-sm text-muted-foreground">{t("sets.review.unsavedNote")}</p>
        ) : null}
        {changed.length === 0 && changes.removed.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("sets.review.noChanges")}</p>
        ) : (
          <ul className="grid gap-1 text-sm">
            {changed.map(([key, value]) => (
              <li key={key}>
                <code className="rounded bg-muted px-1 py-0.5 text-xs">{key}</code>{" "}
                <span className="break-words">{describeValue(value)}</span>
              </li>
            ))}
          </ul>
        )}
        {changes.removed.length > 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("sets.review.removedIgnored", { fields: changes.removed.join(", ") })}
          </p>
        ) : null}
      </div>

      {blockedMessage ? <p className="text-sm text-destructive">{blockedMessage}</p> : null}

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => decide("accept")} disabled={add.isPending || blocked !== null}>
          {t("sets.review.accept")}
        </Button>
        <Button
          variant="outline"
          onClick={() => decide("edit")}
          disabled={add.isPending || blocked !== null}
        >
          {t("sets.review.edit")}
        </Button>
        <Button
          variant="outline"
          className={DESTRUCTIVE}
          onClick={() => decide("reject")}
          disabled={add.isPending || blocked !== null}
        >
          {t("sets.review.reject")}
        </Button>
      </div>

      {recorded ? (
        <p role="status" className="text-sm">
          {t("sets.review.recorded", { verdict: t(`sets.review.verdict.${recorded}`) })}
        </p>
      ) : null}
      {add.isError && reloaded ? (
        <Alert variant="destructive">
          <AlertTitle>{t("sets.review.staleTitle")}</AlertTitle>
          <AlertDescription>
            <p>{t("sets.review.staleBody")}</p>
          </AlertDescription>
        </Alert>
      ) : null}
      {add.isError && !reloaded ? <ProblemView error={add.error} /> : null}
    </div>
  );
}
