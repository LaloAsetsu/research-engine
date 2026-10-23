import { markFocus, restoreFocus } from "./focusMark";

function page(html: string): HTMLElement {
  document.body.innerHTML = `<div id="root">${html}</div>`;
  return document.getElementById("root")!;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("keeping focus when a part of the form is drawn again", () => {
  it("returns focus to the same control of the part, with its caret", () => {
    const root = page(
      '<div data-part="tags"><input id="a" value="alpha"><input id="b" value="beta"></div>',
    );
    const b = document.getElementById("b") as HTMLInputElement;
    b.focus();
    b.setSelectionRange(2, 3);
    const mark = markFocus(root, new Set(["tags"]));
    expect(mark).toMatchObject({ part: "tags", index: 1, start: 2, end: 3 });

    root.innerHTML =
      '<div data-part="tags"><input id="a" value="x"><input id="b" value="bet"></div>';
    expect(document.body).toHaveFocus();
    restoreFocus(root, mark!);
    const again = document.getElementById("b") as HTMLInputElement;
    expect(again).toHaveFocus();
    expect([again.selectionStart, again.selectionEnd]).toEqual([2, 3]);
  });

  it("falls back to the last control when rows went, and keeps the caret inside the text", () => {
    const root = page(
      '<div data-part="other"><textarea id="t">a long text here</textarea><button>x</button></div>',
    );
    const t = document.getElementById("t") as HTMLTextAreaElement;
    t.focus();
    t.setSelectionRange(14, 14);
    const mark = markFocus(root, new Set(["other"]));
    root.innerHTML = '<div data-part="other"><textarea id="t">ab</textarea></div>';
    restoreFocus(root, mark!);
    const again = document.getElementById("t") as HTMLTextAreaElement;
    expect(again).toHaveFocus();
    expect(again.selectionStart).toBe(2);
  });

  it("notes nothing when focus is outside the parts being drawn again", () => {
    const root = page(
      '<input id="q"><div data-part="tags"><input id="a"></div><div data-part="other"><input id="o"></div>',
    );
    document.getElementById("q")!.focus();
    expect(markFocus(root, new Set(["tags"]))).toBeNull();
    document.getElementById("o")!.focus();
    expect(markFocus(root, new Set(["tags"]))).toBeNull();
    expect(markFocus(null, new Set(["tags"]))).toBeNull();
  });
});
