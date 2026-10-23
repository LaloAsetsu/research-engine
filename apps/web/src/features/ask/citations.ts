import type { Citation } from "./types";

/** The 16 hex characters a citation prints for a paper. */
export function shortId(docId: string): string {
  return docId.slice(0, 16);
}

/**
 * Turns each citation the answer prints, `(<16 hex> pages 3-4)`, into a link to `#cite-<n>` (the
 * position in `citations`), so the text and the list of sources open the same page. Only the
 * exact printed form is touched; any other parenthesis stays as written.
 */
export function linkCitations(answer: string, citations: readonly Citation[]): string {
  let text = answer;
  const done = new Set<string>();
  citations.forEach((citation, index) => {
    if (done.has(citation.key)) return;
    done.add(citation.key);
    text = text.split(`(${citation.key})`).join(`[(${citation.key})](#cite-${index})`);
  });
  return text;
}

const CITE_HREF = /^#cite-(\d+)$/;

/** The position of the citation a link made by `linkCitations` points at, or null for any other link. */
export function citationIndex(href: string | undefined): number | null {
  const match = href === undefined ? null : CITE_HREF.exec(href);
  return match?.[1] === undefined ? null : Number(match[1]);
}
