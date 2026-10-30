import { escapeBarsInTableMath as escape } from "./tableMath";

const head = "| State | Value |\n| --- | --- |\n";

describe("bars inside a formula in a pipe table", () => {
  it("turns the bars of a ket into \\vert, in the header and in a row", () => {
    expect(escape("| $|\\psi\\rangle$ | b |\n| --- | --- |\n| $\\langle\\phi|$ | 1 |")).toBe(
      "| $\\vert \\psi\\rangle$ | b |\n| --- | --- |\n| $\\langle\\phi\\vert $ | 1 |",
    );
  });

  it("turns || and \\| into a norm", () => {
    expect(escape(`${head}| $||x||$ | $\\|y\\|$ |`)).toBe(
      `${head}| $\\Vert x\\Vert $ | $\\Vert y\\Vert $ |`,
    );
  });

  it("keeps a bar outside a formula as a cell border", () => {
    expect(escape(`${head}| a | b |\n| $|x|$ | c | d |`)).toBe(
      `${head}| a | b |\n| $\\vert x\\vert $ | c | d |`,
    );
    expect(escape(`${head}| a|b | $x$ |`)).toBe(`${head}| a|b | $x$ |`);
  });

  it("works for a table without outer bars and for a double-dollar formula", () => {
    expect(escape("a | b\n--- | ---\n$|x|$ | $$|y|$$")).toBe(
      "a | b\n--- | ---\n$\\vert x\\vert $ | $$\\vert y\\vert $$",
    );
  });

  it("leaves a line outside a table alone, even with a formula that has bars", () => {
    const text = "The set $|x|$ has size $|S| = 2$.\n\nA | B and $|y|$ in a sentence.";
    expect(escape(text)).toBe(text);
  });

  it("stops at the first line of the table that has no bar", () => {
    expect(escape(`${head}| $|a|$ | 1 |\n\nAfter $|b|$ | c`)).toBe(
      `${head}| $\\vert a\\vert $ | 1 |\n\nAfter $|b|$ | c`,
    );
  });

  it("does not treat an escaped dollar as a formula", () => {
    const text = `${head}| \\$|a|\\$ | b |`;
    expect(escape(text)).toBe(text);
    expect(escape(`${head}| \\$ | $|a|$ |`)).toBe(`${head}| \\$ | $\\vert a\\vert $ |`);
  });

  it("leaves a row alone when its cells already fit the table", () => {
    const text = `${head}| $5 | $6 |`;
    expect(escape(text)).toBe(text);
    const fits = `${head}| $a\\|b$ | $\\alpha$ |`;
    expect(escape(fits)).toBe(fits);
  });

  it("finds a formula as micromark does: no escapes inside it", () => {
    // `$\ \$` is the formula `\ \` (a backslash escapes nothing), so the later `$|b|$` is a formula of its own.
    const row = String.raw`| $\ \$ a | $|b|$ |`;
    expect(escape(`${head}${row}`)).toBe(`${head}${String.raw`| $\ \$ a | $\vert b\vert $ |`}`);
  });

  it("finds a formula as micromark does: a closing run of the opening length", () => {
    // In `$_3$$^*|$` the run `$$` is not a closer for `$`, so the formula is `_3$$^*|`.
    const row = String.raw`| x$_3$$^*|$ | y | z |`;
    expect(escape(`${head}${row}`)).toBe(`${head}${String.raw`| x$_3$$^*\vert $ | y | z |`}`);
  });

  it("does not stop a formula for a digit after its closing dollar", () => {
    const row = String.raw`| $2\pi|$2.05 MHz | $|k|$ | x |`;
    expect(escape(`${head}${row}`)).toBe(
      `${head}${String.raw`| $2\pi\vert $2.05 MHz | $\vert k\vert $ | x |`}`,
    );
  });

  it("leaves display math blocks and fenced code alone", () => {
    const display = `${head}| a | b |\n\n$$\n| $|x|$ |\n| --- |\n$$\n`;
    expect(escape(display)).toBe(display);
    const code = "```\n| a | b |\n| --- | --- |\n| $|x|$ | 1 |\n```";
    expect(escape(code)).toBe(code);
  });

  it("leaves a code span alone", () => {
    const text = `${head}| \`$|x|$\` | b |`;
    expect(escape(text)).toBe(text);
  });

  it("returns text without a table, a bar or a dollar as it was", () => {
    expect(escape("plain")).toBe("plain");
    expect(escape("| a | b |\n| --- | --- |\n| 1 | 2 |")).toBe(
      "| a | b |\n| --- | --- |\n| 1 | 2 |",
    );
  });
});
