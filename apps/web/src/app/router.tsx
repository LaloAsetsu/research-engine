import type { QueryClient } from "@tanstack/react-query";
import { Navigate, type RouteObject } from "react-router";
import { loginLoader, requireSession } from "@/features/auth/guards";
import { LoginPage } from "@/features/auth/LoginPage";
import { NotFoundPage } from "./NotFoundPage";
import { RootLayout } from "./RootLayout";
import { RouteError, ScreenError } from "./RouteError";

/** One lazy route per screen, so a screen's code (and later KaTeX and the editor) loads on demand. */
export function appRoutes(queryClient: QueryClient): RouteObject[] {
  return [
    {
      path: "/login",
      loader: loginLoader(queryClient),
      Component: LoginPage,
      ErrorBoundary: RouteError,
    },
    {
      path: "/",
      loader: requireSession(queryClient),
      Component: RootLayout,
      ErrorBoundary: RouteError,
      children: [
        {
          // One boundary for every screen: an error in a screen (or in the chunk that holds it)
          // replaces that screen only, and the layout stays.
          ErrorBoundary: ScreenError,
          children: [
            { index: true, element: <Navigate to="/ask" replace /> },
            { path: "ask", lazy: () => import("@/features/ask/AskPage") },
            { path: "benchmarks", lazy: () => import("@/features/benchmarks/BenchmarksPage") },
            { path: "sets", lazy: () => import("@/features/qsets/SetsPage") },
            { path: "sets/:name/:version", lazy: () => import("@/features/qsets/VersionPage") },
            {
              path: "sets/:name/:version/items/:id",
              lazy: () => import("@/features/qsets/ItemPage"),
            },
            {
              path: "sets/:name/:version/new-item",
              lazy: () => import("@/features/qsets/NewItemPage"),
            },
            {
              path: "sets/:name/:version/freeze",
              lazy: () => import("@/features/qsets/FreezePage"),
            },
            { path: "circuits", lazy: () => import("@/features/circuits/CircuitsPage") },
            { path: "*", Component: NotFoundPage },
          ],
        },
      ],
    },
  ];
}
