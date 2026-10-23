import { useId, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { Textarea } from "@/shared/ui/textarea";
import { isBlank } from "./flow";
import { SettingsPopover } from "./SettingsPopover";
import { useAskStore } from "./store";

export const MAX_QUESTION_CHARS = 4000; // the API's limit

interface QuestionFormProps {
  /** The engine is starting or a question is being answered: nothing can be sent now. */
  blocked: boolean;
  /** A question is being answered; the button says so. */
  busy: boolean;
  onSubmit: (question: string) => void;
}

/** The question box. Ctrl+Enter (or Cmd+Enter) sends; a blank question cannot be sent. */
export function QuestionForm({ blocked, busy, onSubmit }: QuestionFormProps) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "ask" });
  const question = useAskStore((state) => state.question);
  const setQuestion = useAskStore((state) => state.setQuestion);
  const id = useId();
  const blank = isBlank(question);
  const canSend = !blank && !blocked;

  const send = () => {
    if (canSend) onSubmit(question);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send();
    }
  };
  const onFormSubmit = (event: FormEvent) => {
    event.preventDefault();
    send();
  };
  const counter = new Intl.NumberFormat(i18n.language);

  return (
    <form onSubmit={onFormSubmit} className="grid gap-3" noValidate>
      <div className="grid gap-1.5">
        <Label htmlFor="ask-question">{t("form.label")}</Label>
        <Textarea
          id="ask-question"
          name="question"
          rows={4}
          className="min-h-24"
          maxLength={MAX_QUESTION_CHARS}
          value={question}
          placeholder={t("form.placeholder")}
          aria-describedby={`${id}-hint ${id}-counter`}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <p id={`${id}-hint`}>{t("form.hint", { max: counter.format(MAX_QUESTION_CHARS) })}</p>
          <p id={`${id}-counter`}>
            {t("form.counter", {
              count: counter.format(question.length),
              max: counter.format(MAX_QUESTION_CHARS),
            })}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!canSend} aria-describedby={`${id}-why`}>
          {busy ? t("form.sending") : t("form.submit")}
        </Button>
        <SettingsPopover disabled={busy} />
        <p id={`${id}-why`} className="text-xs text-muted-foreground">
          {busy ? t("form.busy") : blank ? t("form.blank") : null}
        </p>
      </div>
    </form>
  );
}
