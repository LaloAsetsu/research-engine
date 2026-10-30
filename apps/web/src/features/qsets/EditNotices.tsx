import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { describeValue, type FieldConflict } from "./itemDraft";

const FOCUS_RING = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";

/** What a merge brought in, kept on screen until the person has seen it. */
export interface MergeReport {
  /** Fields that were taken from the latest version because the person had not touched them. */
  fromLatest: string[];
  /** Fields both changed: the person's value is in the editor. */
  conflicts: FieldConflict[];
}

function valueText(value: unknown, removed: string): string {
  return value === undefined ? removed : describeValue(value);
}

/** The editor was rebuilt from a newer version of the item because the person had no edits. */
export function RemoteNotice({ fields, onDismiss }: { fields: string[]; onDismiss: () => void }) {
  const { t } = useTranslation();
  return (
    <Alert role="status" tabIndex={-1} data-note-focus className={FOCUS_RING}>
      <AlertTitle>{t("sets.editor.remoteTitle")}</AlertTitle>
      <AlertDescription>
        <p>{t("sets.editor.remoteBody", { fields: fields.join(", ") })}</p>
        <div className="mt-3">
          <Button type="button" variant="outline" onClick={onDismiss}>
            {t("sets.editor.dismiss")}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

/** The item changed on the server while the person has edits: nothing is saved or decided until they choose. */
export function BehindAlert({
  fields,
  both,
  mergeBlocked,
  onMerge,
  onLoadLatest,
}: {
  fields: string[];
  both: string[];
  /** A box of JSON cannot be read yet: a merge would drop its text, so it waits. */
  mergeBlocked: boolean;
  onMerge: () => void;
  onLoadLatest: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Alert variant="destructive" tabIndex={-1} data-first-error className={FOCUS_RING}>
      <AlertTitle>{t("sets.editor.behindTitle")}</AlertTitle>
      <AlertDescription>
        <p>{t("sets.editor.behindBody", { fields: fields.join(", ") })}</p>
        {both.length > 0 ? <p>{t("sets.editor.behindBoth", { fields: both.join(", ") })}</p> : null}
        {mergeBlocked ? <p>{t("sets.editor.behindUnreadable")}</p> : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button type="button" variant="outline" disabled={mergeBlocked} onClick={onMerge}>
            {t("sets.editor.merge")}
          </Button>
          <Button type="button" variant="outline" onClick={onLoadLatest}>
            {t("sets.editor.loadLatestDrop")}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

/** After a merge: what came from the latest version, and the fields both sides changed with both values. */
export function MergeReportView({
  report,
  isBlocked,
  onUseLatest,
}: {
  report: MergeReport;
  /** The box that shows this field cannot be read: it keeps its text, so taking the value waits. */
  isBlocked: (field: string) => boolean;
  onUseLatest: (conflict: FieldConflict) => void;
}) {
  const { t } = useTranslation();
  const removed = t("sets.editor.removedValue");
  const anyBlocked = report.conflicts.some((conflict) => isBlocked(conflict.field));
  return (
    <Alert role="status" tabIndex={-1} data-note-focus className={FOCUS_RING}>
      <AlertTitle>{t("sets.editor.mergedTitle")}</AlertTitle>
      <AlertDescription>
        {report.fromLatest.length > 0 ? (
          <p>{t("sets.editor.mergedLatest", { fields: report.fromLatest.join(", ") })}</p>
        ) : null}
        {report.conflicts.length > 0 ? (
          <>
            <p>{t("sets.editor.mergedConflicts")}</p>
            {anyBlocked ? <p>{t("sets.editor.useLatestBlocked")}</p> : null}
            <ul className="grid gap-3">
              {report.conflicts.map((conflict) => (
                <li key={conflict.field} className="grid gap-1">
                  <code className="w-fit rounded bg-muted px-1 py-0.5 text-xs text-foreground">
                    {conflict.field}
                  </code>
                  <span className="break-words">
                    {t("sets.editor.conflictYours", { value: valueText(conflict.mine, removed) })}
                  </span>
                  <span className="break-words">
                    {t("sets.editor.conflictLatest", {
                      value: valueText(conflict.theirs, removed),
                    })}
                  </span>
                  <div>
                    <Button
                      type="button"
                      variant="outline"
                      aria-label={t("sets.editor.useLatestOf", { field: conflict.field })}
                      disabled={isBlocked(conflict.field)}
                      onClick={() => onUseLatest(conflict)}
                    >
                      {t("sets.editor.useLatest")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p>{t("sets.editor.kept")}</p>
      </AlertDescription>
    </Alert>
  );
}
