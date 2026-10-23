/**
 * Endpoints of the sign-in pull request (scope section 2.1) that the API does not serve yet, so
 * the generated `schema.d.ts` does not hold them. The client merges them in so the login screens
 * are typed today. When the API exports them, delete this file and its use in `client.ts`;
 * `pending.test.ts` fails as soon as `openapi.json` holds any of these paths.
 */

export interface Me {
  username: string;
  display_name: string;
  role: "admin" | "member";
  must_change_password: boolean;
  language: "es" | "en";
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface PasswordChangeRequest {
  current: string;
  new: string;
}

interface NoParameters {
  query?: never;
  header?: never;
  path?: never;
  cookie?: never;
}
interface Json<T> {
  content: { "application/json": T };
}
type Headers = Record<string, unknown>;
interface NoContent {
  204: { headers: Headers; content?: never };
}

interface Operation<Responses> {
  parameters: NoParameters;
  requestBody?: never;
  responses: Responses;
}

interface OperationWithBody<Body, Responses> {
  parameters: NoParameters;
  requestBody: Json<Body>;
  responses: Responses;
}

interface GetItem<Op> {
  parameters: NoParameters;
  get: Op;
  put?: never;
  post?: never;
  delete?: never;
  options?: never;
  head?: never;
  patch?: never;
  trace?: never;
}

interface PostItem<Op> {
  parameters: NoParameters;
  get?: never;
  put?: never;
  post: Op;
  delete?: never;
  options?: never;
  head?: never;
  patch?: never;
  trace?: never;
}

export interface PendingPaths {
  "/api/v1/auth/login": PostItem<OperationWithBody<LoginRequest, NoContent>>;
  "/api/v1/auth/logout": PostItem<Operation<NoContent>>;
  "/api/v1/auth/me": GetItem<Operation<{ 200: { headers: Headers } & Json<Me> }>>;
  "/api/v1/auth/password": PostItem<OperationWithBody<PasswordChangeRequest, NoContent>>;
}

/** The paths of this module, for the test that fails once the API publishes them. */
export const PENDING_PATHS = [
  "/api/v1/auth/login",
  "/api/v1/auth/logout",
  "/api/v1/auth/me",
  "/api/v1/auth/password",
] as const satisfies readonly (keyof PendingPaths)[];
