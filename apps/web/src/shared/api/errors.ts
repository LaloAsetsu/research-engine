export interface ProblemField {
  path: string;
  message: string;
}

export interface ProblemInit {
  status: number;
  type?: string;
  title?: string;
  detail?: string;
  instance?: string;
  errors?: ProblemField[];
  errorId?: string;
  retryAfter?: number;
}

/** The one error type of the API client: an RFC 9457 problem, or a stand-in when the body is not one. */
export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly title: string;
  readonly detail: string;
  readonly instance: string;
  readonly errors: ProblemField[];
  readonly errorId: string | undefined;
  /** Seconds, from the Retry-After header of a 429 or 503. */
  readonly retryAfter: number | undefined;

  constructor(init: ProblemInit) {
    const detail = init.detail ?? init.title ?? `HTTP ${init.status}`;
    super(detail);
    this.name = "ApiError";
    this.status = init.status;
    this.type = init.type ?? "about:blank";
    this.title = init.title ?? "";
    this.detail = detail;
    this.instance = init.instance ?? "";
    this.errors = init.errors ?? [];
    this.errorId = init.errorId;
    this.retryAfter = init.retryAfter;
  }
}

export function isApiError(error: unknown, status?: number): error is ApiError {
  return error instanceof ApiError && (status === undefined || error.status === status);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asFields(value: unknown): ProblemField[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.flatMap((item: unknown) => {
    if (typeof item !== "object" || item === null) return [];
    const { path, message } = item as Record<string, unknown>;
    return typeof path === "string" && typeof message === "string" ? [{ path, message }] : [];
  });
}

function retryAfterSeconds(header: string | null): number | undefined {
  if (header === null) return undefined;
  const seconds = Number(header);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined;
}

/** Builds the error for a failed response. A body that is not a problem document still gives an error. */
export async function problemFromResponse(response: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = await response.clone().json();
    if (typeof parsed === "object" && parsed !== null) body = parsed as Record<string, unknown>;
  } catch {
    // not JSON: the status is all we know
  }
  return new ApiError({
    status: response.status,
    type: asString(body.type),
    title: asString(body.title),
    detail: asString(body.detail),
    instance: asString(body.instance),
    errors: asFields(body.errors),
    errorId: asString(body.error_id),
    retryAfter: retryAfterSeconds(response.headers.get("Retry-After")),
  });
}
