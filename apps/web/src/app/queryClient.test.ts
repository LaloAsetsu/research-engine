import { ApiError } from "@/shared/api/errors";
import { createQueryClient, shouldRetry } from "./queryClient";

describe("shouldRetry", () => {
  it("does not retry an answer that says no", () => {
    expect(shouldRetry(0, new ApiError({ status: 401 }))).toBe(false);
    expect(shouldRetry(0, new ApiError({ status: 422 }))).toBe(false);
  });

  it("retries a server error or a lost connection twice", () => {
    expect(shouldRetry(0, new ApiError({ status: 503 }))).toBe(true);
    expect(shouldRetry(1, new TypeError("network"))).toBe(true);
    expect(shouldRetry(2, new TypeError("network"))).toBe(false);
  });
});

describe("createQueryClient", () => {
  it("calls the handler on a 401 from a query or a mutation, and on nothing else", async () => {
    const onUnauthorized = vi.fn();
    const client = createQueryClient(onUnauthorized);
    await client
      .fetchQuery({
        queryKey: ["a"],
        queryFn: () => Promise.reject(new ApiError({ status: 401 })),
        retry: false,
      })
      .catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await client
      .fetchQuery({
        queryKey: ["b"],
        queryFn: () => Promise.reject(new ApiError({ status: 500 })),
        retry: false,
      })
      .catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await client
      .getMutationCache()
      .build(client, { mutationFn: () => Promise.reject(new ApiError({ status: 401 })) })
      .execute(undefined)
      .catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(2);
  });
});
