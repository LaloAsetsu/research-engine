import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { TextField } from "@/shared/forms/TextField";
import { Button } from "@/shared/ui/button";
import { useCreateSet } from "./api";
import { SelectField } from "./fields";
import { ProblemView } from "./ProblemView";
import { Section } from "./Section";
import { versionPath, type SetEntry } from "./types";

const NAME_RULE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** A new empty draft, or the next version of a set as a copy of one of its versions. */
export function NewSetSection({ entries }: { entries: readonly SetEntry[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useCreateSet();
  const formId = useId();
  const [from, setFrom] = useState("");
  const [name, setName] = useState("");
  const [corpus, setCorpus] = useState("");
  const [nameError, setNameError] = useState<string | undefined>();

  const sources = entries.flatMap((entry) =>
    entry.versions.map((version) => ({
      value: `${version.name}/${version.version}`,
      label: t("sets.new.copyOf", { name: version.name, version: version.version }),
    })),
  );
  const copying = from !== "";

  function submit(event: FormEvent) {
    event.preventDefault();
    const [copyName, copyVersion] = from.split("/");
    const chosen = copying ? (copyName ?? "") : name.trim();
    if (!NAME_RULE.test(chosen)) {
      setNameError(t("sets.new.nameRule"));
      return;
    }
    setNameError(undefined);
    create.mutate(
      {
        name: chosen,
        ...(copying && copyVersion ? { copy_from: copyVersion } : {}),
        ...(!copying && corpus.trim() ? { corpus: corpus.trim() } : {}),
      },
      {
        onSuccess: (version) => {
          if (version) void navigate(versionPath(version.name, version.version));
        },
      },
    );
  }

  return (
    <Section title={t("sets.new.title")} description={t("sets.new.description")}>
      <form id={formId} className="grid gap-3" onSubmit={submit} noValidate>
        <SelectField
          label={t("sets.new.start")}
          value={from}
          onValueChange={setFrom}
          options={[{ value: "", label: t("sets.new.empty") }, ...sources]}
        />
        {copying ? (
          <p className="text-sm text-muted-foreground">
            {t("sets.new.copyHint", { name: from.split("/")[0] ?? "" })}
          </p>
        ) : (
          <>
            <TextField
              label={t("sets.new.name")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              error={nameError}
              hint={t("sets.new.nameHint")}
              autoComplete="off"
            />
            <TextField
              label={t("sets.new.corpus")}
              value={corpus}
              onChange={(event) => setCorpus(event.target.value)}
              hint={t("sets.new.corpusHint")}
              autoComplete="off"
            />
          </>
        )}
        {create.isError ? <ProblemView error={create.error} /> : null}
        <div>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? t("sets.new.creating") : t("sets.new.create")}
          </Button>
        </div>
      </form>
    </Section>
  );
}
