import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { SelectField } from "./fields";
import { hasFilters, type FilterKey, type ItemFilters as Filters } from "./filters";
import { CONTENTS, FORMS, LANGS, REVIEW_STATES, SPLITS } from "./types";

interface Props {
  filters: Filters;
  /** Several filters at once: two calls in a row would each start from the same address. */
  onChange: (updates: Partial<Record<FilterKey, string | undefined>>) => void;
  onClear: () => void;
}

/** The text boxes keep what is typed until the search is sent; the lists apply at once. */
function TextFilters({ filters, onChange }: Pick<Props, "filters" | "onChange">) {
  const { t } = useTranslation();
  const [q, setQ] = useState(filters.q ?? "");
  const [tag, setTag] = useState(filters.tag ?? "");

  function submit(event: FormEvent) {
    event.preventDefault();
    onChange({ q, tag });
  }

  return (
    <form className="flex flex-wrap items-end gap-3" onSubmit={submit} role="search">
      <div className="grid gap-1.5">
        <Label htmlFor="filter-q">{t("sets.filters.q")}</Label>
        <Input
          id="filter-q"
          value={q}
          maxLength={200}
          onChange={(event) => setQ(event.target.value)}
          className="w-56"
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="filter-tag">{t("sets.filters.tag")}</Label>
        <Input
          id="filter-tag"
          value={tag}
          placeholder="category=notation"
          onChange={(event) => setTag(event.target.value)}
          className="w-48"
        />
      </div>
      <Button type="submit" variant="secondary">
        {t("sets.filters.search")}
      </Button>
    </form>
  );
}

export function ItemFilters({ filters, onChange, onClear }: Props) {
  const { t } = useTranslation();
  const all = { value: "", label: t("sets.filters.any") };
  return (
    <div className="grid gap-3" role="group" aria-label={t("sets.filters.title")}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <SelectField
          label={t("sets.filters.form")}
          value={filters.form ?? ""}
          onValueChange={(value) => onChange({ form: value })}
          options={[all, ...FORMS.map((value) => ({ value, label: value }))]}
        />
        <SelectField
          label={t("sets.filters.content")}
          value={filters.content ?? ""}
          onValueChange={(value) => onChange({ content: value })}
          options={[all, ...CONTENTS.map((value) => ({ value, label: value }))]}
        />
        <SelectField
          label={t("sets.filters.lang")}
          value={filters.lang ?? ""}
          onValueChange={(value) => onChange({ lang: value })}
          options={[all, ...LANGS.map((value) => ({ value, label: value }))]}
        />
        <SelectField
          label={t("sets.filters.split")}
          value={filters.split ?? ""}
          onValueChange={(value) => onChange({ split: value })}
          options={[all, ...SPLITS.map((value) => ({ value, label: value }))]}
        />
        <SelectField
          label={t("sets.filters.review")}
          value={filters.review ?? ""}
          onValueChange={(value) => onChange({ review: value })}
          options={[
            all,
            ...REVIEW_STATES.map((value) => ({ value, label: t(`sets.review.state.${value}`) })),
          ]}
        />
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <TextFilters
          key={`${filters.q ?? ""}|${filters.tag ?? ""}`}
          filters={filters}
          onChange={onChange}
        />
        {hasFilters(filters) ? (
          <Button type="button" variant="outline" onClick={onClear}>
            {t("sets.filters.clear")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
