import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { useRevalidator } from "react-router";
import { isApiError } from "@/shared/api/errors";
import { TextField } from "@/shared/forms/TextField";
import { Alert, AlertDescription } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { forgetMe, useChangePassword } from "./api";
import { changePasswordSchema, MIN_PASSWORD_LENGTH, type ChangePasswordValues } from "./schemas";

export function ChangePasswordForm({ username }: { username: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const revalidator = useRevalidator();
  const change = useChangePassword();
  const schema = useMemo(() => changePasswordSchema(username), [username]);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<ChangePasswordValues>({
    resolver: zodResolver(schema),
    defaultValues: { current: "", new: "", confirm: "" },
  });

  const submit = handleSubmit((values) => {
    change.mutate(
      { current: values.current, new: values.new },
      {
        onSuccess: () => {
          forgetMe(queryClient);
          void revalidator.revalidate();
        },
        onError: (error) => {
          // A refused field (a common password, say) is shown on the field, not as a banner.
          const refused = refusedFields(error);
          refused.forEach(({ field, message }, index) =>
            setError(field, { type: SERVER_ERROR, message }, { shouldFocus: index === 0 }),
          );
          if (refused.length > 0) change.reset();
        },
      },
    );
  });

  const message = (error: { type?: string; message?: string } | undefined) => {
    if (!error?.message) return undefined;
    return error.type === SERVER_ERROR
      ? error.message
      : t(error.message, { min: MIN_PASSWORD_LENGTH });
  };

  return (
    <form onSubmit={submit} noValidate className="grid gap-4">
      {change.error ? (
        <Alert variant="destructive">
          <AlertDescription>{changeMessage(change.error, t)}</AlertDescription>
        </Alert>
      ) : null}
      <TextField
        label={t("auth.change.current")}
        type="password"
        autoComplete="current-password"
        error={message(errors.current)}
        {...register("current")}
      />
      <TextField
        label={t("auth.change.new")}
        type="password"
        autoComplete="new-password"
        hint={t("auth.change.hint", { min: MIN_PASSWORD_LENGTH })}
        error={message(errors.new)}
        {...register("new")}
      />
      <TextField
        label={t("auth.change.confirm")}
        type="password"
        autoComplete="new-password"
        error={message(errors.confirm)}
        {...register("confirm")}
      />
      <Button type="submit" disabled={change.isPending}>
        {change.isPending ? t("auth.change.submitting") : t("auth.change.submit")}
      </Button>
    </form>
  );
}

const SERVER_ERROR = "server";

const SERVER_FIELDS = { "/current": "current", "/new": "new" } as const;

/** The fields that a 422 names, with the server's message for each. */
function refusedFields(error: unknown) {
  if (!isApiError(error, 422)) return [];
  return error.errors.flatMap(({ path, message }) => {
    const field = Object.hasOwn(SERVER_FIELDS, path)
      ? SERVER_FIELDS[path as keyof typeof SERVER_FIELDS]
      : null;
    return field === null ? [] : [{ field, message }];
  });
}

type Translate = ReturnType<typeof useTranslation>["t"];

export function changeMessage(error: unknown, t: Translate): string {
  if (isApiError(error, 401) || isApiError(error, 403)) return t("auth.change.wrongCurrent");
  return t("auth.change.failed");
}
