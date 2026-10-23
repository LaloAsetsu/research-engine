import createFetchClient, { type Middleware } from "openapi-fetch";
import createClient from "openapi-react-query";
import { problemFromResponse } from "./errors";
import type { PendingPaths } from "./pending";
import type { paths } from "./schema";

/** Marks a query whose 401 is an answer, not a sign that the session ended (the session check). */
export const EXPECTS_UNAUTHORIZED = { expectsUnauthorized: true } as const;

export const CSRF_COOKIE = "re_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function readCookie(name: string, cookies: string = document.cookie): string | undefined {
  for (const part of cookies.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

/** Every write carries the CSRF token that the API put in a readable cookie. */
export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (SAFE_METHODS.has(request.method)) return undefined;
    const token = readCookie(CSRF_COOKIE);
    if (token !== undefined) request.headers.set(CSRF_HEADER, token);
    return request;
  },
};

/** A failed response becomes an ApiError, so every caller handles one error type. */
export const problemMiddleware: Middleware = {
  async onResponse({ response }) {
    if (!response.ok) throw await problemFromResponse(response);
    return undefined;
  },
};

export type ApiPaths = paths & PendingPaths;

export function createApiClient(baseUrl: string) {
  // Looked up at call time, not captured here: a test double installed later must be used.
  const fetchClient = createFetchClient<ApiPaths>({
    baseUrl,
    fetch: (request) => globalThis.fetch(request),
  });
  fetchClient.use(csrfMiddleware, problemMiddleware);
  return fetchClient;
}

// One origin in production, and a proxy in development, so the base is the page origin.
export const fetchClient = createApiClient(window.location.origin);
export const $api = createClient(fetchClient);
