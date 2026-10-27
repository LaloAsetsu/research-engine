import { zodResolver } from "@hookform/resolvers/zod";
import { CircleCheck } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import { useModels, useServiceStatus, useSets, useStartRuns } from "./api";
import { frozenVersions, oracleUnavailable, shortHash, versionKey } from "./domain";
import { CHECKBOX_CLASS, SELECT_CLASS } from "./fields";
import { ProblemAlert } from "./ProblemAlert";
import { runFormSchema, type RunFormValues } from "./schemas";
import { Section } from "./Section";
import { StateBadge } from "./StateBadge";
import { ARMS, type ArmName, type Job } from "./types";

function toggled<T>(list: readonly T[], item: T, on: boolean): T[] {
  const without = list.filter((entry) => entry !== item);
  return on ? [...without, item] : without;
}

/**
 * Starts runs: a frozen version of a set, the arms and the models. The API makes one job for
 * each arm and model; they wait in one line and run one at a time.
 */
export function RunForm() {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.form" });
  const { t: root } = useTranslation();
  const setsQuery = useSets();
  const modelsQuery = useModels();
  const status = useServiceStatus();
  const start = useStartRuns();
  const [started, setStarted] = useState<Job[] | null>(null);
  const [refused, setRefused] = useState(0);
  const ids = useId();

  const versions = frozenVersions(setsQuery.data ?? []);
  const models = modelsQuery.data ?? [];
  const noOracle = oracleUnavailable(status.data?.mode);

  const {
    control,
    register,
    handleSubmit,
    getValues,
    setValue,
    formState: { errors, isSubmitted },
  } = useForm<RunFormValues>({
    resolver: zodResolver(runFormSchema),
    defaultValues: { set: "", arms: ["bare", "harness"], models: [], repeats: 1, notes: "" },
  });

  // The first frozen version and the first model that answers are the starting choice.
  const firstVersion = versions[0] ? versionKey(versions[0]) : "";
  const firstModel = models.find((model) => model.available)?.name;
  useEffect(() => {
    if (firstVersion && getValues("set") === "") setValue("set", firstVersion);
  }, [firstVersion, getValues, setValue]);
  useEffect(() => {
    if (firstModel && getValues("models").length === 0) setValue("models", [firstModel]);
  }, [firstModel, getValues, setValue]);
  // The live service cannot run the oracle arm: take it out of a choice made before that was known.
  useEffect(() => {
    if (noOracle && getValues("arms").includes("oracle")) {
      setValue(
        "arms",
        getValues("arms").filter((arm) => arm !== "oracle"),
      );
    }
  }, [noOracle, getValues, setValue]);

  const chosenArms = useWatch({ control, name: "arms" });
  const chosenModels = useWatch({ control, name: "models" });
  const jobCount = chosenArms.length * chosenModels.length;

  const submit = handleSubmit(
    (values) => {
      const separator = values.set.lastIndexOf("/");
      const usable = values.models.filter(
        (name) => models.find((model) => model.name === name)?.available === true,
      );
      if (usable.length === 0) return;
      setStarted(null);
      start.mutate(
        {
          set: { name: values.set.slice(0, separator), version: values.set.slice(separator + 1) },
          arms: values.arms.map((name) => ({ name })),
          models: usable,
          repeats: values.repeats,
          notes: values.notes.trim(),
        },
        { onSuccess: (jobs) => setStarted(jobs) },
      );
    },
    () => setRefused((count) => count + 1),
  );

  // A refused submit takes the reader to the list of what is wrong, once it is on the page.
  useEffect(() => {
    if (refused > 0) document.getElementById(`${ids}-errors`)?.focus();
  }, [refused, ids]);

  const messages = Object.values(errors)
    .map((error) => error.message)
    .filter((message): message is string => typeof message === "string");

  return (
    <Section title={t("title")} description={t("description")} id="start-run">
      <form onSubmit={submit} noValidate className="grid gap-5">
        {messages.length > 0 && isSubmitted ? (
          <div
            id={`${ids}-errors`}
            tabIndex={-1}
            role="alert"
            className="grid gap-1 text-sm text-destructive"
          >
            <p className="font-medium">{t("errors.summary")}</p>
            <ul className="list-disc pl-5">
              {messages.map((message) => (
                <li key={message}>{root(message)}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {start.error ? <ProblemAlert error={start.error} /> : null}

        <div className="grid gap-1.5">
          <Label htmlFor={`${ids}-set`}>{t("set")}</Label>
          {setsQuery.isError ? (
            <ProblemAlert error={setsQuery.error} onRetry={() => void setsQuery.refetch()} />
          ) : versions.length === 0 && !setsQuery.isPending ? (
            <p className="text-sm text-muted-foreground">
              {t("noFrozen")}{" "}
              <Link className="underline underline-offset-4" to="/sets">
                {t("toSets")}
              </Link>
            </p>
          ) : (
            <select
              id={`${ids}-set`}
              className={`${SELECT_CLASS} max-w-xl`}
              aria-describedby={`${ids}-set-hint`}
              aria-invalid={errors.set ? true : undefined}
              disabled={setsQuery.isPending}
              {...register("set")}
            >
              {versions.map((version) => (
                <option key={versionKey(version)} value={versionKey(version)}>
                  {t("setOption", {
                    name: version.name,
                    version: version.version,
                    hash: shortHash(version.md5),
                    count: version.items,
                  })}
                </option>
              ))}
            </select>
          )}
          <p id={`${ids}-set-hint`} className="text-sm text-muted-foreground">
            {t("setHint")}
          </p>
        </div>

        <Controller
          control={control}
          name="arms"
          render={({ field }) => (
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium">{t("arms")}</legend>
              {ARMS.map((arm: ArmName) => {
                const blocked = arm === "oracle" && noOracle;
                const inputId = `${ids}-arm-${arm}`;
                return (
                  <div key={arm} className="flex items-start gap-2">
                    <input
                      id={inputId}
                      type="checkbox"
                      className={`${CHECKBOX_CLASS} mt-0.5`}
                      checked={field.value.includes(arm)}
                      disabled={blocked}
                      aria-describedby={`${inputId}-hint`}
                      onChange={(event) =>
                        field.onChange(toggled(field.value, arm, event.target.checked))
                      }
                    />
                    <div className="grid gap-0.5">
                      <Label htmlFor={inputId}>
                        <span className="font-mono">{arm}</span>
                      </Label>
                      <p id={`${inputId}-hint`} className="text-sm text-muted-foreground">
                        {t(`armHint.${arm}`)}
                        {blocked ? (
                          <>
                            {" "}
                            <strong className="font-medium text-foreground">
                              {t("oracleLive")}
                            </strong>
                          </>
                        ) : null}
                      </p>
                    </div>
                  </div>
                );
              })}
            </fieldset>
          )}
        />

        <Controller
          control={control}
          name="models"
          render={({ field }) => (
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium">{t("models")}</legend>
              {modelsQuery.isError ? (
                <ProblemAlert
                  error={modelsQuery.error}
                  onRetry={() => void modelsQuery.refetch()}
                />
              ) : models.length === 0 && !modelsQuery.isPending ? (
                <p className="text-sm text-muted-foreground">{t("noModels")}</p>
              ) : null}
              {models.map((model) => {
                const inputId = `${ids}-model-${model.name}`;
                return (
                  <div key={model.name} className="flex items-start gap-2">
                    <input
                      id={inputId}
                      type="checkbox"
                      className={`${CHECKBOX_CLASS} mt-0.5`}
                      checked={field.value.includes(model.name)}
                      disabled={!model.available}
                      aria-describedby={`${inputId}-hint`}
                      onChange={(event) =>
                        field.onChange(toggled(field.value, model.name, event.target.checked))
                      }
                    />
                    <div className="grid gap-0.5">
                      <Label htmlFor={inputId}>{model.label}</Label>
                      <p id={`${inputId}-hint`} className="text-sm text-muted-foreground">
                        <span className="font-mono">{model.name}</span>
                        {model.revision ? `, ${t("revision", { revision: model.revision })}` : ""}
                        {". "}
                        {model.available ? t("modelUp") : t("modelDown")}
                      </p>
                    </div>
                  </div>
                );
              })}
            </fieldset>
          )}
        />

        <div className="grid max-w-xl gap-4 sm:grid-cols-[8rem_1fr]">
          <div className="grid gap-1.5">
            <Label htmlFor={`${ids}-repeats`}>{t("repeats")}</Label>
            <Input
              id={`${ids}-repeats`}
              type="number"
              inputMode="numeric"
              min={1}
              max={10}
              aria-invalid={errors.repeats ? true : undefined}
              aria-describedby={`${ids}-repeats-hint`}
              {...register("repeats", { valueAsNumber: true })}
            />
            <p id={`${ids}-repeats-hint`} className="text-sm text-muted-foreground">
              {t("repeatsHint")}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${ids}-notes`}>{t("notes")}</Label>
            <Input
              id={`${ids}-notes`}
              type="text"
              autoComplete="off"
              aria-invalid={errors.notes ? true : undefined}
              {...register("notes")}
            />
            <p className="text-sm text-muted-foreground">{t("notesHint")}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={start.isPending}>
            {start.isPending ? t("starting") : t("start")}
          </Button>
          <p className="text-sm text-muted-foreground">
            {jobCount > 0 ? t("summary", { count: jobCount }) : t("summaryNone")}
          </p>
        </div>
      </form>

      <div aria-live="polite">
        {started ? (
          <Alert>
            <CircleCheck aria-hidden="true" />
            <AlertTitle>{t("startedTitle", { count: started.length })}</AlertTitle>
            <AlertDescription>
              <ul className="grid gap-1">
                {started.map((job) => (
                  <li key={job.id} className="flex flex-wrap items-center gap-2">
                    <Link
                      className="underline underline-offset-4"
                      to={`/benchmarks/jobs/${job.id}`}
                    >
                      {t("startedJob", {
                        arm: job.run?.arm.name ?? "",
                        model: job.run?.model ?? "",
                      })}
                    </Link>
                    <StateBadge state={job.state} />
                    {job.position !== null && job.position !== undefined ? (
                      <span className="text-muted-foreground">
                        {job.position === 0 ? t("next") : t("ahead", { count: job.position })}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
      </div>
    </Section>
  );
}
