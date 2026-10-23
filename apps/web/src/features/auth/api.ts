import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { $api, EXPECTS_UNAUTHORIZED, fetchClient } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { LoginRequest, Me, PasswordChangeRequest } from "@/shared/api/pending";

const LOGIN = "/api/v1/auth/login";
const LOGOUT = "/api/v1/auth/logout";
const ME = "/api/v1/auth/me";
const PASSWORD = "/api/v1/auth/password";

export const meOptions = $api.queryOptions(
  "get",
  ME,
  {},
  { staleTime: 5 * 60_000, retry: false, meta: EXPECTS_UNAUTHORIZED },
);

/** The signed-in user, or null when there is no session. Other failures propagate. */
export async function ensureMe(queryClient: QueryClient): Promise<Me | null> {
  try {
    return await queryClient.ensureQueryData(meOptions);
  } catch (error) {
    if (isApiError(error, 401)) return null;
    throw error;
  }
}

/** Forget the cached user so the next route load asks the server again. */
export function forgetMe(queryClient: QueryClient): void {
  queryClient.removeQueries({ queryKey: meOptions.queryKey });
}

export function useLogin() {
  return useMutation({
    mutationFn: async (body: LoginRequest) => {
      await fetchClient.POST(LOGIN, { body });
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: async (body: PasswordChangeRequest) => {
      await fetchClient.POST(PASSWORD, { body });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await fetchClient.POST(LOGOUT);
    },
    // Once the server has ended the session, nothing of it may stay in memory. When the call
    // fails the session is still alive, so the page stays as it is (a 401 is handled globally).
    onSuccess: () => queryClient.clear(),
  });
}
