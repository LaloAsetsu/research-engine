/** Seconds as a short number in the reader's language: `1.8`, `0.6`, `12`. */
export function formatSeconds(seconds: number, language: string): string {
  return new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(seconds);
}

/** One line of a context, cut to `max` characters at a space where it can. Heading marks of the Markdown are dropped. */
export function excerpt(text: string, max = 280): string {
  const flat = text
    .replace(/^#{1,6}[ \t]+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}...`;
}
