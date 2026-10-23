import { useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { TextField } from "@/shared/forms/TextField";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { useImportSet, useImportShapes } from "./api";
import { SelectField } from "./fields";
import { ProblemView } from "./ProblemView";
import { Section } from "./Section";
import { versionPath } from "./types";

export const MAX_IMPORT_BYTES = 20 * 1024 * 1024;

/** Import a `qset/1` file, or a file of one of the older shapes, as a new draft. */
export function ImportSection() {
  const { t } = useTranslation();
  const shapes = useImportShapes();
  const importSet = useImportSet();
  const fileInput = useRef<HTMLInputElement>(null);
  const [fileError, setFileError] = useState<string | undefined>();
  const [shape, setShape] = useState("");
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [corpus, setCorpus] = useState("");

  const chosen = shapes.data?.find((candidate) => candidate.name === shape);

  function submit(event: FormEvent) {
    event.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) {
      setFileError(t("sets.import.noFile"));
      return;
    }
    if (file.size > MAX_IMPORT_BYTES) {
      setFileError(t("sets.import.tooBig"));
      return;
    }
    setFileError(undefined);
    importSet.mutate({
      file,
      name: name.trim(),
      version: version.trim(),
      shape,
      corpus: chosen?.needs_corpus ? corpus.trim() : "",
    });
  }

  const done = importSet.data;
  return (
    <Section title={t("sets.import.title")} description={t("sets.import.description")}>
      <form className="grid gap-3" onSubmit={submit} noValidate>
        <div className="grid gap-1.5">
          <Label htmlFor="import-file">{t("sets.import.file")}</Label>
          <Input
            id="import-file"
            type="file"
            ref={fileInput}
            aria-invalid={fileError ? true : undefined}
            aria-describedby={fileError ? "import-file-error" : undefined}
            onChange={() => setFileError(undefined)}
          />
          {fileError ? (
            <p id="import-file-error" className="text-sm text-destructive">
              {fileError}
            </p>
          ) : null}
        </div>
        <SelectField
          label={t("sets.import.shape")}
          value={shape}
          onValueChange={setShape}
          hint={chosen?.description ?? t("sets.import.shapeHint")}
          options={[
            { value: "", label: t("sets.import.shapeQset") },
            ...(shapes.data ?? []).map((candidate) => ({
              value: candidate.name,
              label: candidate.name,
            })),
          ]}
        />
        {chosen?.needs_corpus ? (
          <TextField
            label={t("sets.import.corpus")}
            value={corpus}
            onChange={(event) => setCorpus(event.target.value)}
            hint={t("sets.import.corpusHint")}
            autoComplete="off"
          />
        ) : null}
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label={t("sets.import.name")}
            value={name}
            onChange={(event) => setName(event.target.value)}
            hint={t("sets.import.nameHint")}
            autoComplete="off"
          />
          <TextField
            label={t("sets.import.version")}
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            hint={t("sets.import.versionHint")}
            autoComplete="off"
          />
        </div>
        {importSet.isError ? (
          <ProblemView error={importSet.error}>
            <p className="mt-2">{t("sets.import.refused")}</p>
          </ProblemView>
        ) : null}
        {done ? (
          <Alert role="status">
            <AlertTitle>
              {t("sets.import.done", {
                count: done.items_read,
                name: done.version.name,
                version: done.version.version,
              })}
            </AlertTitle>
            <AlertDescription>
              <Link
                className="underline underline-offset-3"
                to={versionPath(done.version.name, done.version.version)}
              >
                {t("sets.import.open")}
              </Link>
              {done.shape ? <p>{t("sets.import.converted", { shape: done.shape })}</p> : null}
            </AlertDescription>
          </Alert>
        ) : null}
        <div>
          <Button type="submit" disabled={importSet.isPending}>
            {importSet.isPending ? t("sets.import.importing") : t("sets.import.submit")}
          </Button>
        </div>
      </form>
    </Section>
  );
}
