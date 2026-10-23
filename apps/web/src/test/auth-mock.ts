/**
 * A stand-in for the sign-in endpoints of the API, with the same answers. One pure state machine,
 * used by MSW in the Vitest tests and by Playwright's route interception in the end-to-end test,
 * so both exercise the same behavior. The accounts are two of the API's seed file for fake mode
 * (packages/research-api/tests/fixtures/seed-users.json), with the same names and passwords.
 */

export interface MockUser {
  username: string;
  password: string;
  display_name: string;
  role: "admin" | "member";
  must_change_password: boolean;
}

export const ANA: MockUser = {
  username: "ana",
  password: "test-password-ana-01",
  display_name: "Ana Prueba",
  role: "admin",
  must_change_password: false,
};

/** Signs in with a temporary password, so the first login forces a change. */
export const TEMP: MockUser = {
  username: "temp",
  password: "temporary-test-0001",
  display_name: "Cuenta Temporal",
  role: "member",
  must_change_password: true,
};

export const TEST_USERS: readonly MockUser[] = [ANA, TEMP];

export const MAX_FAILURES = 5;
export const RETRY_AFTER_S = 30;

export interface MockRequest {
  method: string;
  path: string;
  body?: unknown;
  csrfHeader?: string | null | undefined;
}

export interface MockResponse {
  status: number;
  body?: unknown;
  headers: Record<string, string>;
}

const PROBLEM = { "content-type": "application/problem+json" };

function problem(status: number, slug: string, title: string, detail: string, path: string) {
  return {
    status,
    body: { type: `urn:research-api:problem:${slug}`, title, status, detail, instance: path },
    headers: PROBLEM,
  } satisfies MockResponse;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

export function createAuthMock() {
  const users = TEST_USERS.map((user) => ({ ...user }));
  const failures = new Map<string, number>();
  let session: string | null = null;

  const current = () => users.find((user) => user.username === session);

  function login(request: MockRequest): MockResponse {
    const { username, password } = record(request.body);
    const name = typeof username === "string" ? username : "";
    if ((failures.get(name) ?? 0) >= MAX_FAILURES) {
      const limited = problem(
        429,
        "rate-limited",
        "Too Many Requests",
        "Too many attempts.",
        request.path,
      );
      return { ...limited, headers: { ...PROBLEM, "retry-after": String(RETRY_AFTER_S) } };
    }
    const user = users.find((candidate) => candidate.username === name);
    if (!user || user.password !== password) {
      failures.set(name, (failures.get(name) ?? 0) + 1);
      return problem(
        401,
        "unauthenticated",
        "Unauthorized",
        "Wrong username or password.",
        request.path,
      );
    }
    failures.delete(name);
    session = user.username;
    return {
      status: 204,
      headers: { "set-cookie": "re_csrf=csrf-test-token; Path=/; SameSite=Strict" },
    };
  }

  function changePassword(request: MockRequest, user: MockUser): MockResponse {
    const { current: given, new: next } = record(request.body);
    if (given !== user.password) {
      return problem(403, "forbidden", "Forbidden", "The current password is wrong.", request.path);
    }
    if (typeof next !== "string" || next.length < 12 || next === user.username) {
      return {
        ...problem(
          422,
          "invalid-input",
          "Unprocessable Content",
          "The new password is refused.",
          request.path,
        ),
        body: {
          type: "urn:research-api:problem:invalid-input",
          title: "Unprocessable Content",
          status: 422,
          detail: "The new password is refused.",
          instance: request.path,
          errors: [{ path: "/new", message: "Too weak." }],
        },
      };
    }
    user.password = next;
    user.must_change_password = false;
    return { status: 204, headers: {} };
  }

  /** The answer to one request, or null when the path is not a sign-in path. */
  function handle(request: MockRequest): MockResponse | null {
    const { method, path } = request;
    if (!path.startsWith("/api/v1/auth/")) return null;
    if (method === "POST" && path === "/api/v1/auth/login") return login(request);
    const user = current();
    if (!user) return problem(401, "unauthenticated", "Unauthorized", "No session.", path);
    if (method === "GET" && path === "/api/v1/auth/me") {
      return {
        status: 200,
        headers: { "content-type": "application/json" },
        body: {
          username: user.username,
          display_name: user.display_name,
          role: user.role,
          must_change_password: user.must_change_password,
          language: "es",
        },
      };
    }
    if (method === "POST" && !request.csrfHeader) {
      return problem(403, "forbidden", "Forbidden", "The CSRF token is missing.", path);
    }
    if (method === "POST" && path === "/api/v1/auth/logout") {
      session = null;
      return { status: 204, headers: {} };
    }
    if (method === "POST" && path === "/api/v1/auth/password") return changePassword(request, user);
    return problem(404, "not-found", "Not Found", "No such endpoint.", path);
  }

  return { handle, signedInAs: () => session };
}

export type AuthMock = ReturnType<typeof createAuthMock>;
