import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { RadioGroup, RadioGroupItem } from "@/shared/ui/radio-group";
import { Textarea } from "@/shared/ui/textarea";
import type { CircuitFlows } from "./flows";
import { useCanSend } from "./RunPanel";
import { Section } from "./Section";
import { useCircuitStore, type TargetKind } from "./store";

const KINDS: readonly TargetKind[] = ["state", "unitary", "circuit", "drawn"];

export function CheckPanel({ flows }: { flows: CircuitFlows }) {
  const { t } = useTranslation();
  const kind = useCircuitStore((state) => state.targetKind);
  const text = useCircuitStore((state) => state.targetText);
  const busy = useCircuitStore((state) => state.busy);
  const setKind = useCircuitStore((state) => state.setTargetKind);
  const setText = useCircuitStore((state) => state.setTargetText);
  const selectedParts = useCircuitStore(
    (state) => state.reading?.circuits[state.selected]?.parts.length ?? null,
  );
  const canSend = useCanSend();
  const textId = useId();
  const hintId = useId();
  // A drawn equality needs two sides. Before anything is read there is nothing to judge by.
  const drawnImpossible = selectedParts !== null && selectedParts < 2;
  const blocked = !canSend || (kind === "drawn" && drawnImpossible);
  return (
    <Section title={t("circuits.check.title")} description={t("circuits.check.description")}>
      <RadioGroup
        aria-label={t("circuits.check.against")}
        value={kind}
        onValueChange={(value) => {
          const next = KINDS.find((candidate) => candidate === value);
          if (next) setKind(next);
        }}
      >
        {KINDS.map((option) => (
          <div key={option} className="flex items-center gap-2">
            <RadioGroupItem
              value={option}
              id={`${textId}-${option}`}
              disabled={option === "drawn" && drawnImpossible}
            />
            <Label htmlFor={`${textId}-${option}`}>{t(`circuits.check.kinds.${option}`)}</Label>
          </div>
        ))}
      </RadioGroup>
      {kind === "drawn" ? (
        <p className="text-sm text-muted-foreground">
          {t(drawnImpossible ? "circuits.check.drawnNone" : "circuits.check.drawnHint")}
        </p>
      ) : (
        <div className="grid gap-1.5">
          <Label htmlFor={textId}>{t(`circuits.check.label.${kind}`)}</Label>
          <Textarea
            id={textId}
            aria-describedby={hintId}
            className="min-h-24 font-mono text-sm"
            spellCheck={false}
            value={text}
            placeholder={t(`circuits.check.placeholder.${kind}`)}
            onChange={(event) => setText(event.target.value)}
          />
          <p id={hintId} className="text-sm text-muted-foreground">
            {t(`circuits.check.hint.${kind}`)}
          </p>
        </div>
      )}
      <div>
        <Button onClick={() => void flows.check()} disabled={blocked}>
          {busy === "check" ? t("circuits.check.checking") : t("circuits.check.submit")}
        </Button>
      </div>
    </Section>
  );
}
