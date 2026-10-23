import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { createBrowserRouter, type RouteObject } from "react-router";
import { RouterProvider } from "react-router/dom";
import { LOGIN_PATH, loginPath } from "@/features/auth/guards";
import { LanguageProvider } from "./LanguageProvider";
import { createQueryClient } from "./queryClient";
import { appRoutes } from "./router";
import { ThemeProvider } from "./ThemeProvider";

type DataRouter = ReturnType<typeof createBrowserRouter>;

export interface AppInstance {
  queryClient: QueryClient;
  router: DataRouter;
}

/**
 * The query client and the router know each other: a 401 anywhere sends the user to the login
 * screen. Tests pass a memory router instead of the browser one.
 */
export function createApp(
  makeRouter: (routes: RouteObject[]) => DataRouter = (routes) => createBrowserRouter(routes),
): AppInstance {
  const queryClient: QueryClient = createQueryClient(() => {
    const { pathname, search } = router.state.location;
    if (pathname === LOGIN_PATH) return;
    queryClient.clear();
    void router.navigate(loginPath(pathname + search), { replace: true });
  });
  const router = makeRouter(appRoutes(queryClient));
  return { queryClient, router };
}

export function App({ app }: { app: AppInstance }) {
  return (
    <QueryClientProvider client={app.queryClient}>
      <LanguageProvider>
        <ThemeProvider>
          <RouterProvider router={app.router} />
        </ThemeProvider>
      </LanguageProvider>
    </QueryClientProvider>
  );
}
