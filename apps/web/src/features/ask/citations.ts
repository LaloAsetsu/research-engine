import type { Citation } from "./types";

/** The 16 hex characters a citation prints for a paper. */
export function shortId(docId: string): string {
  return docId.slice(0, 16);
}

/**
 * Turns each citation the answer prints, `(<16 hex> pages 3-4)`, into a link to `#cite-<n>` (the
 * position in `citations`), so the text and the list of sources open the same page. A parenthesis
 * that joins several, `(<key>, <key>)`, links each one inside it. Only printed keys are touched;
 * any other parenthesis stays as written.
 */
export function linkCitations(answer: string, citations: readonly Citation[]): string {
  const positions = new Map<string, number>();
  citations.forEach((citation, index) => {
    if (!positions.has(citation.key)) positions.set(citation.key, index);
  });
  if (positions.size === 0) return answer;
  return answer.replace(/\(([^()]+)\)/g, (whole, inner: string) => {
    const single = positions.get(inner);
    if (single !== undefined) return `[(${inner})](#cite-${single})`;
    const parts = inner.split(/(\s*[,;]\s*)/);
    if (parts.length < 3 || !parts.some((part, i) => i % 2 === 0 && positions.has(part))) {
      return whole;
    }
    const linked = parts.map((part, i) => {
      const position = i % 2 === 0 ? positions.get(part) : undefined;
      return position === undefined ? part : `[${part}](#cite-${position})`;
    });
    return `(${linked.join("")})`;
  });
}

const CITE_HREF = /^#cite-(\d+)$/;

/** The position of the citation a link made by `linkCitations` points at, or null for any other link. */
export function citationIndex(href: string | undefined): number | null {
  const match = href === undefined ? null : CITE_HREF.exec(href);
  return match?.[1] === undefined ? null : Number(match[1]);
}
