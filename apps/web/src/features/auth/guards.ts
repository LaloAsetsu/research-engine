import { redirect, type LoaderFunctionArgs } from "react-router";
import type { QueryClient } from "@tanstack/react-query";
import type { Me } from "@/shared/api/types";
import { ensureMe } from "./api";

export const DEFAULT_PATH = "/ask";
export const LOGIN_PATH = "/login";

// Control characters: a browser drops tabs and line breaks inside a URL, so `/<tab>/host` would
// read as `//host`.
// eslint-disable-next-line no-control-regex
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;

/** A return path is kept only when it stays inside the app (no scheme, no `//host`). */
export function safeNext(value: string | null): string | null {
  if (
    value === null ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    CONTROL_CHARACTERS.test(value) ||
    new URL(value, window.location.origin).origin !== window.location.origin
  ) {
    return null;
  }
  return value === LOGIN_PATH || value.startsWith(`${LOGIN_PATH}?`) ? null : value;
}

export function loginPath(next: string | null): string {
  const target = safeNext(next);
  return target === null || target === DEFAULT_PATH
    ? LOGIN_PATH
    : `${LOGIN_PATH}?next=${encodeURIComponent(target)}`;
}

/** Loader of every screen behind the session: sends anyone else to the login screen. */
export function requireSession(queryClient: QueryClient) {
  return async ({ request }: LoaderFunctionArgs): Promise<Me | Response> => {
    const me = await ensureMe(queryClient);
    const url = new URL(request.url);
    if (me === null) return redirect(loginPath(url.pathname + url.search));
    if (me.must_change_password) return redirect(LOGIN_PATH);
    return me;
  };
}

/** Loader of the login screen: a user who is in already moves on; a forced change stays here. */
export function loginLoader(queryClient: QueryClient) {
  return async ({ request }: LoaderFunctionArgs): Promise<Me | Response | null> => {
    const me = await ensureMe(queryClient);
    if (me !== null && !me.must_change_password) {
      return redirect(safeNext(new URL(request.url).searchParams.get("next")) ?? DEFAULT_PATH);
    }
    return me;
  };
}
