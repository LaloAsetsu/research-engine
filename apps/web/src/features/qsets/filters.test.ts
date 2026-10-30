import {
  hasFilters,
  readFilters,
  readOffset,
  withFilters,
  withOffset,
  withoutFilters,
} from "./filters";

const params = (query: string) => new URLSearchParams(query);

describe("reading the filters from the address", () => {
  it("reads every filter the API knows", () => {
    expect(
      readFilters(
        params(
          "form=single&content=math&lang=es&split=dev&tag=category%3Dnotation&review=accepted&q=alpha",
        ),
      ),
    ).toEqual({
      form: "single",
      content: "math",
      lang: "es",
      split: "dev",
      tag: "category=notation",
      review: "accepted",
      q: "alpha",
    });
  });

  it("drops a value the API would refuse, so no request is sent with it", () => {
    expect(readFilters(params("form=nonsense&lang=fr&review=maybe&split=train&content="))).toEqual(
      {},
    );
  });

  it("ignores unknown keys and trims spaces", () => {
    expect(readFilters(params("q=%20spaced%20&other=1&offset=50"))).toEqual({ q: "spaced" });
  });

  it("cuts a text longer than the API takes", () => {
    expect(readFilters(params(`q=${"x".repeat(300)}`)).q).toHaveLength(200);
  });
});

describe("paging", () => {
  it("reads a whole number of items to skip", () => {
    expect(readOffset(params("offset=100"))).toBe(100);
  });

  it.each(["offset=-5", "offset=abc", "offset=1.5", "offset=", "offset=12345678901"])(
    "reads %s as the first page",
    (query) => {
      expect(readOffset(params(query))).toBe(0);
    },
  );

  it("is the first page when the address says nothing", () => {
    expect(readOffset(params(""))).toBe(0);
  });
});

describe("changing the address", () => {
  it("sets a filter and goes back to the first page", () => {
    const next = withFilters(params("offset=100&lang=en"), { form: "single" });
    expect(next.get("form")).toBe("single");
    expect(next.get("lang")).toBe("en");
    expect(next.has("offset")).toBe(false);
  });

  it("takes a filter out when it is emptied", () => {
    expect(withFilters(params("form=single&q=a"), { form: "  " }).toString()).toBe("q=a");
    expect(withFilters(params("form=single"), { form: undefined }).toString()).toBe("");
  });

  it("sets several filters in one step", () => {
    const next = withFilters(params("form=single"), { q: "alpha", tag: "k=v" });
    expect(next.get("q")).toBe("alpha");
    expect(next.get("tag")).toBe("k=v");
    expect(next.get("form")).toBe("single");
  });

  it("does not change the address it was given", () => {
    const current = params("form=single");
    withFilters(current, { lang: "en" });
    expect(current.toString()).toBe("form=single");
  });

  it("sets and clears the offset", () => {
    expect(withOffset(params("form=single"), 50).get("offset")).toBe("50");
    expect(withOffset(params("offset=50"), 0).has("offset")).toBe(false);
  });

  it("clears every filter and the page, and leaves other keys", () => {
    expect(withoutFilters(params("form=single&q=a&offset=50&keep=1")).toString()).toBe("keep=1");
  });

  it("knows when a filter is set", () => {
    expect(hasFilters({})).toBe(false);
    expect(hasFilters({ q: "a" })).toBe(true);
  });
});
