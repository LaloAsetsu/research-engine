import en from "./en.json";
import es from "./es.json";

interface Tree {
  [key: string]: string | Tree;
}

function leaves(tree: Tree, prefix = ""): Map<string, string> {
  const found = new Map<string, string>();
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") found.set(path, value);
    else for (const [inner, text] of leaves(value, path)) found.set(inner, text);
  }
  return found;
}

const EM_DASH = String.fromCharCode(0x2014);
const spanish = leaves(es);
const english = leaves(en);
const placeholders = (text: string) =>
  [...text.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort();

describe("translations", () => {
  it("have the same keys in Spanish and English", () => {
    expect([...spanish.keys()].sort()).toEqual([...english.keys()].sort());
  });

  it("have no empty text", () => {
    for (const [key, text] of [...spanish, ...english]) expect(text.trim(), key).not.toBe("");
  });

  it("use the same placeholders in both languages", () => {
    for (const [key, text] of spanish) {
      expect(placeholders(text), key).toEqual(placeholders(english.get(key) ?? ""));
    }
  });

  it("do not use em dashes", () => {
    for (const [key, text] of [...spanish, ...english]) expect(text, key).not.toContain(EM_DASH);
  });
});
