import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ProblemField } from "@/shared/api/errors";
import { TextField } from "@/shared/forms/TextField";
import { Button } from "@/shared/ui/button";
import { AreaField, SelectField } from "./fields";
import { isPlainObject } from "./itemDraft";
import { rowProblems } from "./problems";

type Fact = Record<string, unknown>;

const TYPES = ["text", "expr", "num"] as const;

function numberOrText(raw: string): number | string {
  const trimmed = raw.trim();
  return trimmed !== "" && Number.isFinite(Number(trimmed)) ? Number(trimmed) : raw;
}

function show(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

/** Builds the fact the format wants for a type: a number has `value`, `rel` and `unit`, text has `value` and `alts`. */
export function factOfType(fact: Fact, type: string): Fact {
  const next: Fact = { type };
  if (fact.paper !== undefined) next.paper = fact.paper;
  if (type === "num") {
    const value = typeof fact.value === "string" ? numberOrText(fact.value) : fact.value;
    next.value = value ?? "";
    next.rel = typeof fact.rel === "number" ? fact.rel : 0;
    if (fact.unit !== undefined) next.unit = fact.unit;
  } else {
    next.value = show(fact.value);
    if (Array.isArray(fact.alts)) next.alts = fact.alts;
  }
  return next;
}

function FactRow({
  index,
  fact,
  problems,
  onChange,
  onRemove,
}: {
  index: number;
  fact: Fact;
  problems: Map<string, string[]>;
  onChange: (fact: Fact) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const type = typeof fact.type === "string" ? fact.type : "text";
  const isNum = type === "num";
  // The text of a number is kept apart, so that "1." or "-" can be typed on the way to a number.
  const [valueText, setValueText] = useState(show(fact.value));
  const [relText, setRelText] = useState(show(fact.rel));
  const [altsText, setAltsText] = useState(
    Array.isArray(fact.alts) ? fact.alts.map(show).join("\n") : "",
  );

  const set = (key: string, value: unknown) => {
    const next = { ...fact };
    if (value === undefined || value === "") delete next[key];
    else next[key] = value;
    onChange(next);
  };

  return (
    <fieldset className="grid gap-3 rounded-lg border p-3">
      <legend className="px-1 text-sm font-medium">
        {t("sets.editor.keyFact", { number: index + 1 })}
      </legend>
      {problems.get("")?.map((message) => (
        <p key={message} className="text-sm text-destructive">
          {message}
        </p>
      ))}
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          label={t("sets.editor.factType", { number: index + 1 })}
          value={type}
          onValueChange={(next) => onChange(factOfType(fact, next))}
          options={TYPES.map((value) => ({ value, label: value }))}
          errors={problems.get("type")}
        />
        <TextField
          label={t("sets.editor.factValue", { number: index + 1 })}
          value={valueText}
          error={problems.get("value")?.join(" ")}
          onChange={(event) => {
            setValueText(event.target.value);
            set("value", isNum ? numberOrText(event.target.value) : event.target.value);
          }}
        />
        {isNum ? (
          <>
            <TextField
              label={t("sets.editor.factRel", { number: index + 1 })}
              value={relText}
              hint={t("sets.editor.factRelHint")}
              error={problems.get("rel")?.join(" ")}
              onChange={(event) => {
                setRelText(event.target.value);
                set("rel", numberOrText(event.target.value));
              }}
            />
            <TextField
              label={t("sets.editor.factUnit", { number: index + 1 })}
              value={typeof fact.unit === "string" ? fact.unit : ""}
              error={problems.get("unit")?.join(" ")}
              onChange={(event) => set("unit", event.target.value)}
            />
          </>
        ) : (
          <div className="sm:col-span-2">
            <AreaField
              label={t("sets.editor.factAlts", { number: index + 1 })}
              value={altsText}
              hint={t("sets.editor.factAltsHint")}
              errors={problems.get("alts")}
              onValueChange={(next) => {
                setAltsText(next);
                const lines = next.split("\n").filter((line) => line.trim() !== "");
                set("alts", lines.length > 0 ? lines : undefined);
              }}
            />
          </div>
        )}
        <TextField
          label={t("sets.editor.factPaper", { number: index + 1 })}
          value={typeof fact.paper === "string" ? fact.paper : ""}
          hint={t("sets.editor.factPaperHint")}
          error={problems.get("paper")?.join(" ")}
          onChange={(event) => set("paper", event.target.value)}
        />
      </div>
      <div>
        <Button type="button" variant="outline" size="sm" onClick={onRemove}>
          {t("sets.editor.removeFact", { number: index + 1 })}
        </Button>
      </div>
    </fieldset>
  );
}

/** The facts an answer must contain, one row each. The server checks them; the rows only shape them. */
export function KeyFactsEditor({
  facts,
  problems,
  onChange,
}: {
  facts: readonly unknown[];
  problems: readonly ProblemField[];
  onChange: (facts: Fact[] | undefined) => void;
}) {
  const { t } = useTranslation();
  const rows = facts.map((fact) => (isPlainObject(fact) ? fact : {}));
  const wholeList = problems.filter((problem) => problem.path === "/key_facts");
  const commit = (next: Fact[]) => onChange(next.length > 0 ? next : undefined);
  return (
    <div className="grid gap-3">
      {wholeList.map((problem) => (
        <p key={problem.message} className="text-sm text-destructive">
          {problem.message}
        </p>
      ))}
      {rows.map((fact, index) => (
        <FactRow
          // Rows have no identity of their own, so removing one remounts the rows after it.
          key={`${index}-${rows.length}`}
          index={index}
          fact={fact}
          problems={rowProblems(problems, index)}
          onChange={(next) => commit(rows.map((row, at) => (at === index ? next : row)))}
          onRemove={() => commit(rows.filter((_, at) => at !== index))}
        />
      ))}
      <div>
        <Button
          type="button"
          variant="outline"
          onClick={() => commit([...rows, { type: "text", value: "" }])}
        >
          {t("sets.editor.addFact")}
        </Button>
      </div>
    </div>
  );
}
