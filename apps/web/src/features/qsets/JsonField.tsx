import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ProblemField } from "@/shared/api/errors";
import { Label } from "@/shared/ui/label";
import { Textarea } from "@/shared/ui/textarea";
import { parseJson, prettyJson } from "./itemDraft";
import { innerPath } from "./problems";

interface Props {
  /** Names the field for the list of text that cannot be read yet. */
  fieldKey: string;
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
  onProblem: (key: string, message: string | null) => void;
  problems?: readonly ProblemField[] | undefined;
  hint?: string | undefined;
  /** Say whether a parsed value can be used (an object for the whole item, say). */
  accept?: ((value: unknown) => string | null) | undefined;
  /** Print each problem with its whole pointer: the box holds the whole item, not one field of it. */
  wholeItem?: boolean | undefined;
}

/**
 * A box of JSON text for the parts of an item that have no control of their own. While the text
 * cannot be read, the field tells the page, which then refuses to save: the last good value
 * would otherwise be saved without the person knowing the box shows something else.
 */
export function JsonField({
  fieldKey,
  label,
  value,
  onChange,
  onProblem,
  problems,
  hint,
  accept,
  wholeItem,
}: Props) {
  const { t } = useTranslation();
  const id = useId();
  const [textValue, setText] = useState(() => (value === undefined ? "" : prettyJson(value)));
  const [parseError, setParseError] = useState<string | null>(null);

  useEffect(() => () => onProblem(fieldKey, null), [fieldKey, onProblem]);

  function edit(next: string) {
    setText(next);
    if (next.trim() === "") {
      setParseError(null);
      onProblem(fieldKey, null);
      onChange(undefined);
      return;
    }
    const parsed = parseJson(next);
    if (!parsed.ok) {
      const message = t("sets.editor.jsonInvalid", { message: parsed.message });
      setParseError(message);
      onProblem(fieldKey, `${label}: ${message}`);
      return;
    }
    const refusal = accept?.(parsed.value) ?? null;
    if (refusal) {
      setParseError(refusal);
      onProblem(fieldKey, `${label}: ${refusal}`);
      return;
    }
    setParseError(null);
    onProblem(fieldKey, null);
    onChange(parsed.value);
  }

  const messages = (problems ?? []).map((problem) => {
    const place = wholeItem ? problem.path : innerPath(problem.path);
    return place ? `${place}: ${problem.message}` : problem.message;
  });
  const errors = parseError ? [parseError, ...messages] : messages;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, errors.length > 0 ? errorId : null]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Textarea
        id={id}
        value={textValue}
        spellCheck={false}
        onChange={(event) => edit(event.target.value)}
        aria-invalid={errors.length > 0 ? true : undefined}
        aria-describedby={describedBy || undefined}
        className="min-h-20 font-mono text-xs md:text-xs"
      />
      {hint ? (
        <p id={hintId} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {errors.length > 0 ? (
        <ul id={errorId} className="grid gap-0.5 text-sm text-destructive">
          {errors.map((message, index) => (
            <li key={`${index}-${message}`}>{message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
