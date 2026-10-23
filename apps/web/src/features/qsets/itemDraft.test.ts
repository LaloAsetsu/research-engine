import {
  changesBetween,
  describeValue,
  hasChanges,
  isCircuitForm,
  isMulti,
  keyFactsOf,
  needsSearchRecord,
  needsSynthesis,
  newItemTemplate,
  otherFields,
  parseJson,
  sameValue,
  stable,
  tagsFromRows,
  tagsOf,
  text,
  withField,
  withOtherFields,
} from "./itemDraft";

describe("comparing items", () => {
  it("treats the same data with keys in another order as equal", () => {
    expect(stable({ b: 1, a: { d: 2, c: 3 } })).toBe(stable({ a: { c: 3, d: 2 }, b: 1 }));
    expect(sameValue([{ a: 1, b: 2 }], [{ b: 2, a: 1 }])).toBe(true);
    expect(sameValue([1, 2], [2, 1])).toBe(false);
  });

  it("lists what the draft changes, adds and removes", () => {
    const stored = { id: "A", question: "q", notes: "n", tags: { k: "v" } };
    const draft = { id: "A", question: "q2", tags: { k: "v" }, answer: "a" };
    const changes = changesBetween(stored, draft);
    expect(changes.changed).toEqual({ question: "q2", answer: "a" });
    expect(changes.removed).toEqual(["notes"]);
    expect(hasChanges(changes)).toBe(true);
  });

  it("finds no change in an item that was only reordered", () => {
    const stored = { id: "A", tags: { a: "1", b: "2" } };
    expect(hasChanges(changesBetween(stored, { tags: { b: "2", a: "1" }, id: "A" }))).toBe(false);
  });
});

describe("changing a field", () => {
  it("sets a value without touching the draft it was given", () => {
    const draft = { id: "A" };
    expect(withField(draft, "question", "q")).toEqual({ id: "A", question: "q" });
    expect(draft).toEqual({ id: "A" });
  });

  it("takes the field out for blank text, undefined and null", () => {
    for (const value of ["", undefined, null]) {
      expect(withField({ id: "A", question: "q" }, "question", value)).toEqual({ id: "A" });
    }
  });

  it("keeps a zero and a false", () => {
    expect(withField({}, "n", 0)).toEqual({ n: 0 });
    expect(withField({}, "f", false)).toEqual({ f: false });
  });

  it("reads text, and an empty string for anything else", () => {
    expect(text({ a: "x", b: 3 }, "a")).toBe("x");
    expect(text({ b: 3 }, "b")).toBe("");
    expect(text({}, "c")).toBe("");
  });
});

describe("what a form kind asks for", () => {
  it("knows the multi forms, the circuit forms and the forms with a search record", () => {
    expect(isMulti("multi_list")).toBe(true);
    expect(isMulti("single")).toBe(false);
    expect(needsSynthesis("multi_chain")).toBe(true);
    expect(needsSynthesis("multi_list")).toBe(false);
    expect(isCircuitForm("circuit_claim")).toBe(true);
    expect(isCircuitForm("single")).toBe(false);
    expect(needsSearchRecord("unanswerable")).toBe(true);
    expect(needsSearchRecord("multi_list")).toBe(true);
    expect(needsSearchRecord("single")).toBe(false);
  });
});

describe("the fields no control handles", () => {
  const item = { id: "A", question: "q", writer: "ana", ext: { x: 1 } };

  it("lists them apart", () => {
    expect(otherFields(item)).toEqual({ writer: "ana", ext: { x: 1 } });
  });

  it("replaces them all at once", () => {
    expect(withOtherFields(item, { reviewer: "beto" })).toEqual({
      id: "A",
      question: "q",
      reviewer: "beto",
    });
    expect(withOtherFields(item, {})).toEqual({ id: "A", question: "q" });
  });
});

describe("a new item", () => {
  it("starts from the corpus of the version", () => {
    const item = newItemTemplate("demo-corpus");
    expect(item).toMatchObject({ id: "", lang: "en", form: "single", corpus: "demo-corpus" });
    expect(item.gold_papers).toEqual([]);
    expect(newItemTemplate(null).corpus).toBe("");
  });
});

describe("reading JSON text", () => {
  it("reads a value", () => {
    expect(parseJson('{"a": [1, 2]}')).toEqual({ ok: true, value: { a: [1, 2] } });
  });

  it("says why it cannot", () => {
    const result = parseJson("{oops");
    expect(result.ok).toBe(false);
    expect(!result.ok && result.message.length > 0).toBe(true);
  });
});

describe("small readers", () => {
  it("shortens a long value for a list of changes", () => {
    expect(describeValue("short")).toBe("short");
    expect(describeValue("x".repeat(200), 10)).toBe(`${"x".repeat(10)}...`);
    expect(describeValue({ a: 1 })).toBe('{"a":1}');
  });

  it("reads key facts and tags from an item that may be odd", () => {
    expect(keyFactsOf({ key_facts: [{ type: "text" }, 5] })).toEqual([{ type: "text" }, {}]);
    expect(keyFactsOf({ key_facts: "no" })).toEqual([]);
    expect(tagsOf({ tags: { a: "1", b: 2 } })).toEqual([
      ["a", "1"],
      ["b", ""],
    ]);
    expect(tagsOf({})).toEqual([]);
  });

  it("makes tags from rows, dropping a row without a key", () => {
    expect(
      tagsFromRows([
        ["a", "1"],
        ["", "lost"],
        [" b ", "2"],
        ["a", "3"],
      ]),
    ).toEqual({ a: "3", b: "2" });
    expect(tagsFromRows([["", ""]])).toBeUndefined();
  });
});
