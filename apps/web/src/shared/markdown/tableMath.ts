/**
 * A pipe table splits its rows at every `|`, formula or not, so `$|\psi\rangle$` in a cell cuts the
 * cell in two and the row no longer fits the table. The text is rewritten before it is parsed:
 * inside `$...$` on the rows of a pipe table, `|` becomes `\vert ` and `||` or `\|` become
 * `\Vert ` (a norm). Text outside a formula, lines outside a table, display math (`$$` blocks)
 * and fenced code are returned as they were, and so is a row whose cells already fit the table.
 * Formulas are found the way micromark finds them: no escapes inside, and the closing run of
 * dollars has exactly the length of the opening one.
 */

const DELIMITER_ROW = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const FENCE = /^\s*(```|~~~)/;

/** The inside of a formula: a bar is a bar of the formula, not a cell border. */
function escapeBars(math: string): string {
  let out = "";
  for (let i = 0; i < math.length; i++) {
    const char = math[i];
    if (char === "\\") {
      if (math[i + 1] === "|") {
        out += "\\Vert ";
        i += 1;
      } else {
        out += char + (math[i + 1] ?? "");
        i += 1;
      }
    } else if (char === "|") {
      if (math[i + 1] === "|") {
        out += "\\Vert ";
        i += 1;
      } else {
        out += "\\vert ";
      }
    } else {
      out += char;
    }
  }
  return out;
}

/** One table line: finds each `$...$` (or `$$...$$`) and escapes the bars inside it. */
function escapeLine(line: string): string {
  let out = "";
  let i = 0;
  while (i < line.length) {
    const char = line[i];
    if (char === "\\") {
      out += line.slice(i, i + 2);
      i += 2;
    } else if (char === "`") {
      // A code span is copied as it is; its `$` is not a formula.
      const run = /^`+/.exec(line.slice(i))?.[0] ?? "`";
      const close = line.indexOf(run, i + run.length);
      const end = close === -1 ? i + run.length : close + run.length;
      out += line.slice(i, end);
      i = end;
    } else if (char === "$") {
      const run = /^\$+/.exec(line.slice(i))?.[0] ?? "$";
      const start = i + run.length;
      let close = -1;
      // As in micromark: a backslash escapes nothing here, and only a run of exactly the opening length closes.
      for (let j = start; j < line.length; j++) {
        if (line[j] !== "$") continue;
        let k = j;
        while (line[k] === "$") k++;
        if (k - j === run.length) {
          close = j;
          break;
        }
        j = k - 1;
      }
      if (close === -1 || close === start) {
        out += run;
        i = start;
      } else {
        out += run + escapeBars(line.slice(start, close)) + run;
        i = close + run.length;
      }
    } else {
      out += char;
      i += 1;
    }
  }
  return out;
}

/** How many cells a row has by its bars: one outer bar on each side does not count, `\|` is no border. */
function cellCount(line: string): number {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  let count = 1;
  for (let i = 0; i < row.length; i++) {
    if (row[i] === "\\") i += 1;
    else if (row[i] === "|") count += 1;
  }
  return count;
}

/** Keeps `|` inside the formulas of pipe-table rows from being read as a cell border. */
export function escapeBarsInTableMath(text: string): string {
  if (!text.includes("|") || !text.includes("$")) return text;
  const lines = text.split("\n");
  let fenced = false;
  let display = false;
  let inTable = false;
  let fence = 0;
  let width = 0;
  for (let n = 0; n < lines.length; n++) {
    const line = lines[n] ?? "";
    if (!display && FENCE.test(line)) {
      fenced = !fenced;
      inTable = false;
      continue;
    }
    if (fenced) continue;
    // A display block opens at a line of `$$` or more with no other dollar, and closes only at a line of dollars alone.
    if (display) {
      if ((/^ {0,3}(\$+)\s*$/.exec(line)?.[1]?.length ?? 0) >= fence) display = false;
      continue;
    }
    const opening = /^ {0,3}(\$\$+)[^$]*$/.exec(line);
    if (opening) {
      display = true;
      fence = opening[1]?.length ?? 2;
      inTable = false;
      continue;
    }
    if (!line.includes("|")) {
      inTable = false;
      continue;
    }
    if (!inTable) {
      // A table starts at a header line whose next line is the delimiter row.
      const next = lines[n + 1] ?? "";
      if (!next.includes("|") || !DELIMITER_ROW.test(next)) continue;
      inTable = true;
      width = cellCount(next);
    }
    if (DELIMITER_ROW.test(line) || cellCount(line) === width) continue;
    lines[n] = escapeLine(line);
  }
  return lines.join("\n");
}
