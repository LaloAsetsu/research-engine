import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ProblemField } from "@/shared/api/errors";
import { TextField } from "@/shared/forms/TextField";
import { Button } from "@/shared/ui/button";
import { AreaField, SelectField } from "./fields";
import {
  HANDLED_FIELDS,
  isCircuitForm,
  isPlainObject,
  needsSearchRecord,
  needsSynthesis,
  otherFields,
  tagsFromRows,
  tagsOf,
  text,
  withField,
  withOtherFields,
} from "./itemDraft";
import { JsonField } from "./JsonField";
import { KeyFactsEditor } from "./KeyFactsEditor";
import { ProblemList } from "./ProblemView";
import { groupByField, innerPath, pointerParts } from "./problems";
import { CONTENTS, FORMS, LANG_ORIGINS, LANGS, SPLITS, SYNTHESIS_KINDS, type Item } from "./types";

interface Props {
  draft: Item;
  onChange: (draft: Item) => void;
  problems: readonly ProblemField[];
  /** A new item names its id; an existing one cannot change it. */
  idEditable: boolean;
  onProblem: (key: string, message: string | null) => void;
}

/** The message of a problem at a field: the text alone when it is at the field, with its place when it is inside. */
function messagesAt(problems: readonly ProblemField[] | undefined): string[] {
  return (problems ?? []).map((problem) => {
    const inner = innerPath(problem.path);
    return inner ? `${inner}: ${problem.message}` : problem.message;
  });
}

function TagsEditor({
  tags,
  errors,
  onChange,
}: {
  tags: [string, string][];
  errors: string[];
  onChange: (tags: Item | undefined) => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<[string, string][]>(tags);
  const commit = (next: [string, string][]) => {
    setRows(next);
    onChange(tagsFromRows(next));
  };
  return (
    <fieldset className="grid gap-2">
      <legend className="text-sm font-medium">{t("sets.editor.tags")}</legend>
      {rows.map(([key, value], index) => (
        <div key={index} className="flex flex-wrap items-end gap-2">
          <div className="min-w-32 flex-1">
            <TextField
              label={t("sets.editor.tagKey", { number: index + 1 })}
              value={key}
              onChange={(event) =>
                commit(rows.map((row, at) => (at === index ? [event.target.value, row[1]] : row)))
              }
            />
          </div>
          <div className="min-w-32 flex-1">
            <TextField
              label={t("sets.editor.tagValue", { number: index + 1 })}
              value={value}
              onChange={(event) =>
                commit(rows.map((row, at) => (at === index ? [row[0], event.target.value] : row)))
              }
            />
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={() => commit(rows.filter((_, at) => at !== index))}
          >
            {t("sets.editor.removeTag", { number: index + 1 })}
          </Button>
        </div>
      ))}
      {errors.map((message) => (
        <p key={message} className="text-sm text-destructive">
          {message}
        </p>
      ))}
      <div>
        <Button type="button" variant="outline" onClick={() => commit([...rows, ["", ""]])}>
          {t("sets.editor.addTag")}
        </Button>
      </div>
    </fieldset>
  );
}

/** The fields of an item, by what its form asks for. Every field shows the problems the API found at it. */
export function ItemFields({ draft, onChange, problems, idEditable, onProblem }: Props) {
  const { t } = useTranslation();
  const grouped = groupByField(problems).byField;
  const at = (key: string) => grouped.get(key);
  const form = text(draft, "form");
  const set = (key: string, value: unknown) => onChange(withField(draft, key, value));
  const optional = (label: string) => ({ value: "", label });
  const synthesis = isPlainObject(draft.synthesis) ? draft.synthesis : {};
  const setSynthesis = (key: string, value: string) => {
    const next = { ...synthesis, [key]: value };
    const empty = Object.values(next).every((entry) => entry === "" || entry === undefined);
    set("synthesis", empty ? undefined : next);
  };
  const showSynthesis = needsSynthesis(form) || "synthesis" in draft;
  const showNecessity = needsSynthesis(form) || "necessity" in draft;
  const showCircuit = isCircuitForm(form) || "circuit" in draft;
  const showSearch = needsSearchRecord(form) || "search_record" in draft;
  const otherProblems = [...grouped.entries()]
    .filter(([key]) => !HANDLED.has(key))
    .flatMap(([, list]) => list);
  const visible = new Set<string>([
    "id",
    "lang",
    "lang_origin",
    "mirror_of",
    "form",
    "content",
    "corpus",
    "split",
    "question",
    "answer",
    "rationale",
    "notes",
    "tags",
    "key_facts",
    "gold_papers",
  ]);
  if (showSynthesis) visible.add("synthesis");
  if (showNecessity) visible.add("necessity");
  if (showCircuit) visible.add("circuit");
  if (showSearch) visible.add("search_record");
  // A problem at a part of the item that this form does not show must not get lost.
  const hidden = problems.filter((problem) => {
    const [first] = pointerParts(problem.path);
    return first === undefined || (HANDLED.has(first) && !visible.has(first));
  });

  return (
    <div className="grid gap-4">
      {hidden.length > 0 ? (
        <div role="group" aria-label={t("sets.editor.hiddenProblems")}>
          <p className="text-sm font-medium text-destructive">{t("sets.editor.hiddenProblems")}</p>
          <ProblemList problems={hidden} />
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <TextField
          label={t("sets.editor.id")}
          value={text(draft, "id")}
          readOnly={!idEditable}
          hint={idEditable ? t("sets.editor.idHint") : undefined}
          error={messagesAt(at("id")).join(" ") || undefined}
          onChange={(event) => set("id", event.target.value)}
          autoComplete="off"
        />
        <SelectField
          label={t("sets.editor.lang")}
          value={text(draft, "lang")}
          onValueChange={(value) => set("lang", value)}
          options={[
            ...(LANGS.includes(text(draft, "lang") as (typeof LANGS)[number])
              ? []
              : [optional(t("sets.editor.choose"))]),
            ...LANGS.map((value) => ({ value, label: value })),
          ]}
          errors={messagesAt(at("lang"))}
        />
        <SelectField
          label={t("sets.editor.form")}
          value={form}
          onValueChange={(value) => set("form", value)}
          options={[
            ...(FORMS.includes(form as (typeof FORMS)[number])
              ? []
              : [optional(t("sets.editor.choose"))]),
            ...FORMS.map((value) => ({ value, label: value })),
          ]}
          errors={messagesAt(at("form"))}
        />
        <SelectField
          label={t("sets.editor.content")}
          value={text(draft, "content")}
          onValueChange={(value) => set("content", value)}
          options={[
            optional(t("sets.editor.notSet")),
            ...CONTENTS.map((value) => ({ value, label: value })),
          ]}
          errors={messagesAt(at("content"))}
        />
        <TextField
          label={t("sets.editor.corpus")}
          value={text(draft, "corpus")}
          error={messagesAt(at("corpus")).join(" ") || undefined}
          onChange={(event) => set("corpus", event.target.value)}
          autoComplete="off"
        />
        <SelectField
          label={t("sets.editor.split")}
          value={text(draft, "split")}
          onValueChange={(value) => set("split", value)}
          options={[
            optional(t("sets.editor.notSet")),
            ...SPLITS.map((value) => ({ value, label: value })),
          ]}
          errors={messagesAt(at("split"))}
        />
        <SelectField
          label={t("sets.editor.langOrigin")}
          value={text(draft, "lang_origin")}
          onValueChange={(value) => set("lang_origin", value)}
          options={[
            optional(t("sets.editor.notSet")),
            ...LANG_ORIGINS.map((value) => ({ value, label: value })),
          ]}
          errors={messagesAt(at("lang_origin"))}
        />
        <TextField
          label={t("sets.editor.mirrorOf")}
          value={text(draft, "mirror_of")}
          hint={t("sets.editor.mirrorOfHint")}
          error={messagesAt(at("mirror_of")).join(" ") || undefined}
          onChange={(event) => set("mirror_of", event.target.value)}
          autoComplete="off"
        />
      </div>

      <AreaField
        label={t("sets.editor.question")}
        value={text(draft, "question")}
        onValueChange={(value) => set("question", value)}
        errors={messagesAt(at("question"))}
      />
      {form === "circuit_verify" ? (
        <SelectField
          label={t("sets.editor.answer")}
          value={text(draft, "answer")}
          onValueChange={(value) => set("answer", value)}
          options={[
            optional(t("sets.editor.choose")),
            { value: "yes", label: "yes" },
            { value: "no", label: "no" },
          ]}
          hint={t("sets.editor.answerVerifyHint")}
          errors={messagesAt(at("answer"))}
        />
      ) : (
        <AreaField
          label={t("sets.editor.answer")}
          value={text(draft, "answer")}
          onValueChange={(value) => set("answer", value)}
          errors={messagesAt(at("answer"))}
        />
      )}
      <AreaField
        label={t("sets.editor.rationale")}
        value={text(draft, "rationale")}
        onValueChange={(value) => set("rationale", value)}
        errors={messagesAt(at("rationale"))}
      />
      <AreaField
        label={t("sets.editor.notes")}
        value={text(draft, "notes")}
        onValueChange={(value) => set("notes", value)}
        errors={messagesAt(at("notes"))}
      />

      <TagsEditor
        tags={tagsOf(draft)}
        errors={messagesAt(at("tags"))}
        onChange={(tags) => set("tags", tags)}
      />

      <section className="grid gap-2" aria-labelledby="key-facts-heading">
        <h3 id="key-facts-heading" className="text-sm font-medium">
          {t("sets.editor.keyFacts")}
        </h3>
        <p className="text-sm text-muted-foreground">{t("sets.editor.keyFactsHint")}</p>
        <KeyFactsEditor
          facts={Array.isArray(draft.key_facts) ? (draft.key_facts as unknown[]) : []}
          problems={at("key_facts") ?? []}
          onChange={(facts) => set("key_facts", facts)}
        />
      </section>

      <JsonField
        fieldKey="gold_papers"
        label={t("sets.editor.goldPapers")}
        value={draft.gold_papers}
        hint={t("sets.editor.goldPapersHint")}
        problems={at("gold_papers")}
        onChange={(value) => set("gold_papers", value)}
        onProblem={onProblem}
      />

      {showSynthesis ? (
        <fieldset className="grid gap-3 rounded-lg border p-3">
          <legend className="px-1 text-sm font-medium">{t("sets.editor.synthesis")}</legend>
          <SelectField
            label={t("sets.editor.synthesisKind")}
            value={typeof synthesis.kind === "string" ? synthesis.kind : ""}
            onValueChange={(value) => setSynthesis("kind", value)}
            options={[
              optional(t("sets.editor.choose")),
              ...SYNTHESIS_KINDS.map((value) => ({ value, label: value })),
            ]}
            errors={messagesAt(at("synthesis")?.filter((p) => p.path.endsWith("/kind")))}
          />
          <TextField
            label={t("sets.editor.synthesisStatement")}
            value={typeof synthesis.statement === "string" ? synthesis.statement : ""}
            error={
              messagesAt(at("synthesis")?.filter((p) => p.path.endsWith("/statement"))).join(" ") ||
              undefined
            }
            onChange={(event) => setSynthesis("statement", event.target.value)}
          />
          <AreaField
            label={t("sets.editor.synthesisRubric")}
            value={typeof synthesis.rubric === "string" ? synthesis.rubric : ""}
            onValueChange={(value) => setSynthesis("rubric", value)}
            errors={messagesAt(at("synthesis")?.filter((p) => p.path.endsWith("/rubric")))}
          />
          {messagesAt(at("synthesis")?.filter((p) => p.path === "/synthesis")).map((message) => (
            <p key={message} className="text-sm text-destructive">
              {message}
            </p>
          ))}
        </fieldset>
      ) : null}
      {showNecessity ? (
        <JsonField
          fieldKey="necessity"
          label={t("sets.editor.necessity")}
          value={draft.necessity}
          hint={t("sets.editor.necessityHint")}
          problems={at("necessity")}
          onChange={(value) => set("necessity", value)}
          onProblem={onProblem}
        />
      ) : null}
      {showCircuit ? (
        <JsonField
          fieldKey="circuit"
          label={t("sets.editor.circuit")}
          value={draft.circuit}
          hint={t("sets.editor.circuitHint")}
          problems={at("circuit")}
          onChange={(value) => set("circuit", value)}
          onProblem={onProblem}
        />
      ) : null}
      {showSearch ? (
        <JsonField
          fieldKey="search_record"
          label={t("sets.editor.searchRecord")}
          value={draft.search_record}
          hint={t("sets.editor.searchRecordHint")}
          problems={at("search_record")}
          onChange={(value) => set("search_record", value)}
          onProblem={onProblem}
        />
      ) : null}

      <JsonField
        fieldKey="other"
        label={t("sets.editor.other")}
        value={otherFields(draft)}
        hint={t("sets.editor.otherHint")}
        problems={otherProblems}
        accept={(value) => (isPlainObject(value) ? null : t("sets.editor.mustBeObject"))}
        onChange={(value) => onChange(withOtherFields(draft, isPlainObject(value) ? value : {}))}
        onProblem={onProblem}
      />
    </div>
  );
}

const HANDLED = new Set<string>(HANDLED_FIELDS);
