import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { useRevalidator } from "react-router";
import { isApiError } from "@/shared/api/errors";
import { TextField } from "@/shared/forms/TextField";
import { Alert, AlertDescription } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { forgetMe, useLogin } from "./api";
import { loginSchema, type LoginValues } from "./schemas";

export function LoginForm() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const revalidator = useRevalidator();
  const login = useLogin();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: "", password: "" },
  });

  const submit = handleSubmit((values) => {
    login.mutate(values, {
      onSuccess: () => {
        // The route loader asks for the user again and moves on, or shows the forced change.
        forgetMe(queryClient);
        void revalidator.revalidate();
      },
    });
  });

  return (
    <form onSubmit={submit} noValidate className="grid gap-4">
      {login.error ? (
        <Alert variant="destructive">
          <AlertDescription>{loginMessage(login.error, t)}</AlertDescription>
        </Alert>
      ) : null}
      <TextField
        label={t("auth.login.username")}
        autoComplete="username"
        autoCapitalize="none"
        spellCheck={false}
        error={errors.username?.message ? t(errors.username.message) : undefined}
        {...register("username")}
      />
      <TextField
        label={t("auth.login.password")}
        type="password"
        autoComplete="current-password"
        error={errors.password?.message ? t(errors.password.message) : undefined}
        {...register("password")}
      />
      <Button type="submit" disabled={login.isPending}>
        {login.isPending ? t("auth.login.submitting") : t("auth.login.submit")}
      </Button>
    </form>
  );
}

type Translate = ReturnType<typeof useTranslation>["t"];

/** One message for a wrong user and a wrong password, as the API gives one. */
export function loginMessage(error: unknown, t: Translate): string {
  if (isApiError(error, 401)) return t("auth.login.invalid");
  if (isApiError(error, 429)) {
    return error.retryAfter === undefined
      ? t("auth.login.rateLimitedNoWait")
      : t("auth.login.rateLimited", { seconds: Math.ceil(error.retryAfter) });
  }
  return t("auth.login.unavailable");
}
