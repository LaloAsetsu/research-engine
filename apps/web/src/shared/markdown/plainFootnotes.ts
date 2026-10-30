/**
 * remark-gfm reads `[^1]: text` as a footnote and prints a definition only next to a reference in
 * the same Markdown. A paper is shown one page at a time, so a note whose reference sits on
 * another page would vanish, and the rest would get an English "Footnotes" section of its own.
 * This plugin (placed after remark-gfm) writes every footnote back as the text it was: a
 * reference as `[^1]`, a definition as a paragraph that starts with `[^1]: `.
 */
interface MdNode {
  type: string;
  label?: string | null | undefined;
  identifier?: string;
  value?: string;
  children?: MdNode[];
}

const text = (value: string): MdNode => ({ type: "text", value });

function plain(node: MdNode): void {
  if (!node.children) return;
  const out: MdNode[] = [];
  for (const child of node.children) {
    plain(child);
    const label = child.label ?? child.identifier ?? "";
    if (child.type === "footnoteReference") {
      out.push(text(`[^${label}]`));
    } else if (child.type === "footnoteDefinition") {
      const [first, ...rest] = child.children ?? [];
      if (first?.type === "paragraph") {
        first.children = [text(`[^${label}]: `), ...(first.children ?? [])];
        out.push(first, ...rest);
      } else {
        out.push(
          { type: "paragraph", children: [text(`[^${label}]:`)] },
          ...(child.children ?? []),
        );
      }
    } else {
      out.push(child);
    }
  }
  node.children = out;
}

export function remarkPlainFootnotes() {
  return (tree: MdNode) => plain(tree);
}
