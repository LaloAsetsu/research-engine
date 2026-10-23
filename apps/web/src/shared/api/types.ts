import type { components } from "./schema";

/** The signed-in user, as `GET /auth/me` answers. */
export type Me = components["schemas"]["Me"];
export type LoginRequest = components["schemas"]["LoginRequest"];
export type PasswordChangeRequest = components["schemas"]["PasswordChange"];
