import { ApiError, isApiError, problemFromResponse } from "./errors";

function problemResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 422,
    headers: { "content-type": "application/problem+json" },
    ...init,
  });
}

describe("problemFromResponse", () => {
  it("reads a problem document", async () => {
    const error = await problemFromResponse(
      problemResponse({
        type: "urn:research-api:problem:invalid-input",
        title: "Unprocessable Content",
        status: 422,
        detail: "Bad value.",
        instance: "/api/v1/x",
        errors: [{ path: "/new", message: "Too short." }, { nope: 1 }, "x"],
        error_id: "abc",
      }),
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.detail).toBe("Bad value.");
    expect(error.message).toBe("Bad value.");
    expect(error.type).toBe("urn:research-api:problem:invalid-input");
    expect(error.instance).toBe("/api/v1/x");
    expect(error.errors).toEqual([{ path: "/new", message: "Too short." }]);
    expect(error.errorId).toBe("abc");
  });

  it("still gives an error when the body is not JSON", async () => {
    const error = await problemFromResponse(
      new Response("<html>bad gateway</html>", { status: 502 }),
    );
    expect(error.status).toBe(502);
    expect(error.detail).toBe("HTTP 502");
    expect(error.type).toBe("about:blank");
    expect(error.errors).toEqual([]);
  });

  it("still gives an error when the JSON is not an object", async () => {
    const error = await problemFromResponse(problemResponse("text", { status: 500 }));
    expect(error.status).toBe(500);
    expect(error.title).toBe("");
  });

  it("reads Retry-After in seconds and ignores other forms", async () => {
    const limited = await problemFromResponse(
      problemResponse({ detail: "slow down" }, { status: 429, headers: { "retry-after": "30" } }),
    );
    expect(limited.retryAfter).toBe(30);
    const dated = await problemFromResponse(
      problemResponse(
        {},
        { status: 503, headers: { "retry-after": "Wed, 21 Oct 2026 07:28:00 GMT" } },
      ),
    );
    expect(dated.retryAfter).toBeUndefined();
  });

  it("does not consume the response it was given", async () => {
    const response = problemResponse({ detail: "x" });
    await problemFromResponse(response);
    expect(response.bodyUsed).toBe(false);
  });
});

describe("isApiError", () => {
  it("matches the class and, when asked, the status", () => {
    const error = new ApiError({ status: 401 });
    expect(isApiError(error)).toBe(true);
    expect(isApiError(error, 401)).toBe(true);
    expect(isApiError(error, 403)).toBe(false);
    expect(isApiError(new Error("x"))).toBe(false);
  });
});
