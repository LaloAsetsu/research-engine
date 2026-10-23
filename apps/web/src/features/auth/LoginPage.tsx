import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useLoaderData } from "react-router";
import type { Me } from "@/shared/api/pending";
import { useFocusPageTitle } from "@/shared/layout/useFocusPageTitle";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/ui/card";
import { ChangePasswordForm } from "./ChangePasswordForm";
import { LoginForm } from "./LoginForm";

/** The only screen outside the session. With a session that must change its password, it shows the change. */
export function LoginPage() {
  const { t } = useTranslation();
  const me = useLoaderData<Me | null>();
  const forced = me !== null;
  const title = forced ? t("auth.change.title") : t("auth.login.title");

  // Signing in with a temporary password swaps the sign-in form for the change form under the
  // focused button, and signing out opens this screen anew: the heading takes focus in both.
  useFocusPageTitle(forced);

  useEffect(() => {
    document.title = `${title} | ${t("app.name")}`;
  }, [title, t]);

  return (
    <main className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>
            <h1 id="page-title" tabIndex={-1} className="text-xl font-semibold">
              {title}
            </h1>
          </CardTitle>
          <CardDescription>
            {forced ? t("auth.change.description") : t("auth.login.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {forced ? <ChangePasswordForm username={me.username} /> : <LoginForm />}
        </CardContent>
      </Card>
    </main>
  );
}

export { LoginPage as Component };
