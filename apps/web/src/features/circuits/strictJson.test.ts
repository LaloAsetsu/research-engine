import { findDuplicateKey, readStrictJson } from "./strictJson";

describe("findDuplicateKey", () => {
  it("finds a key written twice in one object", () => {
    expect(findDuplicateKey('{"wires": 1, "wires": 2}')).toBe("wires");
  });

  it("finds the repeat in a nested object and inside an array of objects", () => {
    expect(findDuplicateKey('{"parts": [[{"kind": "x", "controls": [1], "controls": [2]}]]}')).toBe(
      "controls",
    );
  });

  it("reads two spellings of one key as the same key", () => {
    expect(findDuplicateKey('{"a": 1, "\\u0061": 2}')).toBe("a");
  });

  it("allows the same key in different objects, and the same text as a value", () => {
    expect(
      findDuplicateKey('{"a": {"b": 1}, "c": {"b": 2}, "d": ["b", "b"], "e": "a"}'),
    ).toBeNull();
    expect(findDuplicateKey('[{"a": 1}, {"a": 2}]')).toBeNull();
  });

  it("is not fooled by brackets, commas or escaped quotes inside a string", () => {
    expect(findDuplicateKey('{"a,b": "}, \\"x\\": {", "c": 1}')).toBeNull();
    expect(findDuplicateKey('{"q\\"": 1, "q\\"": 2}')).toBe('q"');
  });

  it("names the first repeat in reading order", () => {
    expect(findDuplicateKey('{"b": 1, "a": 1, "b": 2, "a": 2}')).toBe("b");
  });
});

describe("readStrictJson", () => {
  it("returns the value of valid JSON", () => {
    expect(readStrictJson('{"a": [1, 2]}')).toEqual({ ok: true, value: { a: [1, 2] } });
  });

  it("tells empty text from broken text", () => {
    expect(readStrictJson("  \n ")).toEqual({ ok: false, reason: "empty" });
    expect(readStrictJson("{")).toEqual({ ok: false, reason: "syntax" });
    expect(readStrictJson('{"a": NaN}')).toEqual({ ok: false, reason: "syntax" });
    expect(readStrictJson('{"a": 1,}')).toEqual({ ok: false, reason: "syntax" });
  });

  it("refuses a repeated key and names it", () => {
    expect(readStrictJson('{"unitary": true, "unitary": false}')).toEqual({
      ok: false,
      reason: "duplicate",
      key: "unitary",
    });
  });
});
