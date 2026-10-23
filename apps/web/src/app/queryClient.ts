import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { isApiError } from "@/shared/api/errors";

/** Retry only what can pass: not an answer from the server that says no. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status < 500) return false;
  return failureCount < 2;
}

/**
 * `onUnauthorized` runs when a query or mutation comes back 401, which means the session ended
 * while the page was open. The handler decides what to do (the login screen itself ignores it).
 * The session check is exempt: its 401 is what the route guards read, and reacting to it would
 * start another navigation while the first one is still loading.
 */
export function createQueryClient(onUnauthorized: () => void): QueryClient {
  const check = (error: unknown) => {
    if (isApiError(error, 401)) onUnauthorized();
  };
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (query.meta?.expectsUnauthorized !== true) check(error);
      },
    }),
    mutationCache: new MutationCache({ onError: check }),
    defaultOptions: { queries: { retry: shouldRetry, refetchOnWindowFocus: false } },
  });
}
