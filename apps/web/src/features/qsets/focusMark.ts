const FOCUSABLE = "input, textarea, select, button, [tabindex]:not([tabindex='-1'])";

/** Where focus stood inside a part of the form that is about to be drawn again. */
export interface FocusMark {
  part: string;
  /** Which of the part's controls had focus, counted in document order. */
  index: number;
  start: number | null;
  end: number | null;
}

function controls(container: Element): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(FOCUSABLE)];
}

/**
 * Note the control that has focus when it sits in one of `parts` (elements marked `data-part`
 * inside `root`). A part drawn again loses its controls, and focus would fall to the page.
 */
export function markFocus(root: HTMLElement | null, parts: ReadonlySet<string>): FocusMark | null {
  const active = document.activeElement;
  if (root === null || !(active instanceof HTMLElement) || !root.contains(active)) return null;
  const container = active.closest<HTMLElement>("[data-part]");
  const part = container?.dataset.part;
  if (!container || part === undefined || !parts.has(part)) return null;
  const index = controls(container).indexOf(active);
  if (index < 0) return null;
  const typed = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement;
  return {
    part,
    index,
    start: typed ? active.selectionStart : null,
    end: typed ? active.selectionEnd : null,
  };
}

/** Put focus back on the same control of the part once it is drawn again (the last one if rows went). */
export function restoreFocus(root: HTMLElement | null, mark: FocusMark): void {
  if (root === null) return;
  const container = [...root.querySelectorAll<HTMLElement>("[data-part]")].find(
    (element) => element.dataset.part === mark.part,
  );
  if (!container) return;
  const list = controls(container);
  const target = list[Math.min(mark.index, list.length - 1)];
  if (!target) return;
  target.focus({ preventScroll: true });
  if (
    mark.start !== null &&
    mark.end !== null &&
    (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)
  ) {
    const length = target.value.length;
    try {
      target.setSelectionRange(Math.min(mark.start, length), Math.min(mark.end, length));
    } catch {
      // A type of input without a caret (a number, say): focus alone is enough.
    }
  }
}
