/**
 * JSON that refuses a key written twice. `JSON.parse` keeps the last value of a repeated key
 * without a word, and quantum-sim refuses such a document because the text does not say which
 * value is meant. The API refuses it too (and stays the authority); this check only lets the
 * screen say so before a request is made. It reports the first repeat in reading order.
 */

export type StrictJson =
  | { ok: true; value: unknown }
  | { ok: false; reason: "empty" | "syntax" }
  | { ok: false; reason: "duplicate"; key: string };

/** The first key that an object of valid JSON text names twice, or null. */
export function findDuplicateKey(text: string): string | null {
  interface Scope {
    keys: Set<string> | null; // null for an array
    expectKey: boolean;
  }
  const stack: Scope[] = [];
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '"') {
      let j = i + 1;
      while (text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      const top = stack[stack.length - 1];
      if (top?.keys && top.expectKey) {
        const key = JSON.parse(text.slice(i, j + 1)) as string;
        if (top.keys.has(key)) return key;
        top.keys.add(key);
        top.expectKey = false;
      }
      i = j + 1;
      continue;
    }
    if (ch === "{") stack.push({ keys: new Set(), expectKey: true });
    else if (ch === "[") stack.push({ keys: null, expectKey: false });
    else if (ch === "}" || ch === "]") stack.pop();
    else if (ch === ",") {
      const top = stack[stack.length - 1];
      if (top?.keys) top.expectKey = true;
    }
    i += 1;
  }
  return null;
}

export function readStrictJson(text: string): StrictJson {
  if (text.trim() === "") return { ok: false, reason: "empty" };
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { ok: false, reason: "syntax" };
  }
  const key = findDuplicateKey(text);
  if (key !== null) return { ok: false, reason: "duplicate", key };
  return { ok: true, value };
}
