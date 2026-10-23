import type { Complex } from "./types";

const MINUS = "−";

function formatter(locale: string, digits: number): Intl.NumberFormat {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: digits, useGrouping: false });
}

function real(value: number, locale: string, digits = 4): string {
  const text = formatter(locale, digits).format(Math.abs(value));
  return value < 0 && Number(text.replace(",", ".")) !== 0 ? MINUS + text : text;
}

/** An amplitude: `0.7071`, `0.5 + 0.5i`, `\u22120.7071i`, `0`. Four decimals, as the screen needs. */
export function formatComplex([re, im]: Complex, locale: string): string {
  const r = real(re, locale);
  const i = real(im, locale);
  if (r === "0" && i === "0") return "0";
  if (i === "0") return r;
  const negative = i.startsWith(MINUS);
  const magnitude = negative ? i.slice(MINUS.length) : i;
  const imaginary = `${magnitude === "1" ? "" : magnitude}i`;
  if (r === "0") return `${negative ? MINUS : ""}${imaginary}`;
  return `${r} ${negative ? MINUS : "+"} ${imaginary}`;
}

/** The finest step of a shown percentage: 0.0001%, as a probability. */
const FINEST = 1e-6;

/** A probability as a percentage: `50%`, `12.5%`, `0.0005%`; a nonzero one is never shown as 0%. */
export function formatProbability(p: number, locale: string): string {
  if (p === 0) return new Intl.NumberFormat(locale, { style: "percent" }).format(0);
  const digits = p >= 0.1 ? 1 : p >= 0.001 ? 2 : 4;
  const percent = (value: number) =>
    new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: digits }).format(
      value,
    );
  // Below half a step the figure would round to 0%; say "less than one step", in the language's own digits
  return p < FINEST / 2 ? `<${percent(FINEST)}` : percent(p);
}

/** `|010>` for outcome `index` of `bits` bits, first wire most significant. */
export function basisLabel(index: number, bits: number): string {
  return `|${index.toString(2).padStart(bits, "0")}⟩`;
}

/** Number of bits that label an outcome list of `length` entries. */
export function bitsFor(length: number): number {
  return Math.max(0, Math.round(Math.log2(length)));
}

export function squaredMagnitude([re, im]: Complex): number {
  return re * re + im * im;
}

/** Smaller than this a probability or an amplitude counts as zero, for hiding rows only. */
export const NEGLIGIBLE = 1e-12;
