/** A date for people, in the language of the screen. A value that is not a date is shown as it is. */
export function formatDate(value: string | null | undefined, language: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
}

/** The first characters of an md5, for a table; the full value goes in the page of the version. */
export function shortMd5(md5: string): string {
  return md5.slice(0, 8);
}

export function clip(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}
