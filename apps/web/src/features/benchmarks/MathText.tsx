import { MarkdownMath } from "@/shared/markdown/MarkdownMath";

/** A `$...$` pair with no space inside its signs and no digit after the closing one, or a `$$...$$` block. */
const MATH_PAIR = /\$\$[\s\S]+?\$\$|\$(?=\S)[^$\n]*\S\$(?!\d)/;

/**
 * Text a question set holds (a question, a reference answer, a key fact) that may hold LaTeX
 * between `$` signs. Only text with such a pair goes through Markdown, so a plain question keeps
 * its characters (`*`, `_`, `#`) as written, and so do prices ("from $5 to $10").
 */
export function MathText({ children, className }: { children: string; className?: string }) {
  if (!MATH_PAIR.test(children)) return <div className={className}>{children}</div>;
  return <MarkdownMath className={className}>{children}</MarkdownMath>;
}

/** An expression key fact is bare LaTeX (`\alpha^2 + \beta^2 = 1`): shown as inline math. */
export function MathExpr({ children, className }: { children: string; className?: string }) {
  const tex = children.trim();
  if (!tex) return null;
  return <MarkdownMath className={className}>{tex.includes("$") ? tex : `$${tex}$`}</MarkdownMath>;
}
