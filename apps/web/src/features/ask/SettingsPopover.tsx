import { SlidersHorizontal } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/shared/ui/popover";
import { RadioGroup, RadioGroupItem } from "@/shared/ui/radio-group";
import { Switch } from "@/shared/ui/switch";
import { changedSettings, useAskStore } from "./store";
import { KEYWORD_MODES, type KeywordMode } from "./types";

const DEFAULT = "default";

function parseMode(value: string): KeywordMode | null {
  return KEYWORD_MODES.find((mode) => mode === value) ?? null;
}

function parseBool(value: string): boolean | null {
  return value === "yes" ? true : value === "no" ? false : null;
}

function boolValue(value: boolean | null): string {
  return value === null ? DEFAULT : value ? "yes" : "no";
}

/**
 * The options of a question: use the index or not, when keyword matching joins, and whether to
 * complete a cut equation or table. "Server setting" sends nothing (`null`), so the server's own
 * configuration decides; the API does not report that configuration, so it cannot be shown here.
 */
export function SettingsPopover({ disabled }: { disabled: boolean }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  const settings = useAskStore((state) => state.settings);
  const setSettings = useAskStore((state) => state.setSettings);
  const resetSettings = useAskStore((state) => state.resetSettings);
  const id = useId();
  const changed = changedSettings(settings);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" disabled={disabled}>
          <SlidersHorizontal aria-hidden="true" />
          {t("options.trigger")}
          {changed > 0 ? (
            <Badge variant="secondary">{t("options.changed", { count: changed })}</Badge>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-labelledby={`${id}-title`}
        className="max-h-(--radix-popover-content-available-height) w-80 gap-4 overflow-y-auto"
      >
        <div className="grid gap-1">
          <h2 id={`${id}-title`} className="font-medium">
            {t("options.title")}
          </h2>
          <p className="text-xs text-muted-foreground">{t("options.description")}</p>
        </div>

        <div className="flex items-start justify-between gap-3">
          <div className="grid gap-0.5">
            <Label htmlFor={`${id}-index`}>{t("options.useIndex.label")}</Label>
            <p id={`${id}-index-help`} className="text-xs text-muted-foreground">
              {t("options.useIndex.help")}
            </p>
          </div>
          <Switch
            id={`${id}-index`}
            aria-describedby={`${id}-index-help`}
            checked={settings.useIndex}
            onCheckedChange={(useIndex) => setSettings({ useIndex })}
          />
        </div>

        <fieldset className="grid gap-2">
          <legend id={`${id}-bm25-label`} className="mb-1 text-sm font-medium">
            {t("options.bm25.label")}
          </legend>
          <p id={`${id}-bm25-help`} className="text-xs text-muted-foreground">
            {t("options.bm25.help")}
          </p>
          <RadioGroup
            aria-labelledby={`${id}-bm25-label`}
            aria-describedby={`${id}-bm25-help`}
            value={settings.bm25 ?? DEFAULT}
            onValueChange={(value) => setSettings({ bm25: parseMode(value) })}
          >
            {[DEFAULT, ...KEYWORD_MODES].map((mode) => (
              <div key={mode} className="flex items-center gap-2">
                <RadioGroupItem value={mode} id={`${id}-bm25-${mode}`} />
                <Label htmlFor={`${id}-bm25-${mode}`} className="font-normal">
                  {t(`options.bm25.${mode}`)}
                </Label>
              </div>
            ))}
          </RadioGroup>
        </fieldset>

        <fieldset className="grid gap-2">
          <legend id={`${id}-blocks-label`} className="mb-1 text-sm font-medium">
            {t("options.wholeBlocks.label")}
          </legend>
          <p id={`${id}-blocks-help`} className="text-xs text-muted-foreground">
            {t("options.wholeBlocks.help")}
          </p>
          <RadioGroup
            aria-labelledby={`${id}-blocks-label`}
            aria-describedby={`${id}-blocks-help`}
            value={boolValue(settings.wholeBlocks)}
            onValueChange={(value) => setSettings({ wholeBlocks: parseBool(value) })}
          >
            {[DEFAULT, "yes", "no"].map((value) => (
              <div key={value} className="flex items-center gap-2">
                <RadioGroupItem value={value} id={`${id}-blocks-${value}`} />
                <Label htmlFor={`${id}-blocks-${value}`} className="font-normal">
                  {t(`options.wholeBlocks.${value}`)}
                </Label>
              </div>
            ))}
          </RadioGroup>
        </fieldset>

        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={changed === 0}
            onClick={resetSettings}
          >
            {t("options.reset")}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
