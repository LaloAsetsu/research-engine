import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { createApiClient, CSRF_HEADER, readCookie } from "./client";
import { ApiError } from "./errors";

const client = createApiClient(window.location.origin);

describe("readCookie", () => {
  it("finds a cookie by name and decodes it", () => {
    expect(readCookie("b", "a=1; b=x%20y; c=3")).toBe("x y");
    expect(readCookie("z", "a=1")).toBeUndefined();
    expect(readCookie("a", "")).toBeUndefined();
  });
});

describe("csrf middleware", () => {
  it("adds the token to writes only", async () => {
    document.cookie = "re_csrf=token-1; Path=/";
    const seen: Record<string, string | null> = {};
    server.use(
      http.get("*/api/v1/health", ({ request }) => {
        seen.get = request.headers.get(CSRF_HEADER);
        return HttpResponse.json({ ok: true });
      }),
      http.post("*/api/v1/auth/logout", ({ request }) => {
        seen.post = request.headers.get(CSRF_HEADER);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await client.GET("/api/v1/health");
    await client.POST("/api/v1/auth/logout");
    expect(seen).toEqual({ get: null, post: "token-1" });
  });

  it("sends a write without the header when there is no cookie", async () => {
    document.cookie = "re_csrf=; Max-Age=0; Path=/";
    let header: string | null = "unset";
    server.use(
      http.post("*/api/v1/auth/logout", ({ request }) => {
        header = request.headers.get(CSRF_HEADER);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await client.POST("/api/v1/auth/logout");
    expect(header).toBeNull();
  });
});

describe("problem middleware", () => {
  it("turns an error response into an ApiError", async () => {
    server.use(
      http.get("*/api/v1/status", () =>
        HttpResponse.json(
          {
            type: "urn:x",
            title: "Unavailable",
            status: 503,
            detail: "Warming up.",
            instance: "/s",
          },
          {
            status: 503,
            headers: { "retry-after": "5", "content-type": "application/problem+json" },
          },
        ),
      ),
    );
    const error = await client.GET("/api/v1/status").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).retryAfter).toBe(5);
  });

  it("passes a good response through", async () => {
    const { data } = await client.GET("/api/v1/status");
    expect(data?.ready).toBe(true);
  });
});
