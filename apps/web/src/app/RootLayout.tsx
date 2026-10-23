import { useTranslation } from "react-i18next";
import { Outlet, useLoaderData, useLocation } from "react-router";
import type { Me } from "@/shared/api/types";
import { useFocusPageTitle } from "@/shared/layout/useFocusPageTitle";
import { TopBar } from "./TopBar";

/** The frame of every screen behind the session: skip link, top bar, the screen itself. */
export function RootLayout() {
  const { t } = useTranslation();
  const me = useLoaderData<Me>();
  const { pathname } = useLocation();

  // After a navigation (a link, or the redirect that follows signing in) the heading takes
  // focus, so a screen reader starts at the new page. A fresh page load is left alone.
  useFocusPageTitle(pathname);

  return (
    <div className="min-h-svh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:ring-3 focus:ring-ring"
      >
        {t("app.skipToContent")}
      </a>
      <TopBar me={me} />
      <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-6 outline-none">
        <Outlet />
      </main>
    </div>
  );
}
