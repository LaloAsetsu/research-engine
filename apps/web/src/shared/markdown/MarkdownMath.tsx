import "katex/dist/katex.min.css";
import { type ComponentProps, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";
import { cn } from "@/shared/lib/utils";

/**
 * KaTeX renders the formulas to HTML plus MathML (the MathML is what a screen reader reads).
 * `trust: false` keeps `\href`, `\url` and similar commands out; a formula it cannot read is
 * shown as typed instead of throwing.
 */
const KATEX_OPTIONS = {
  throwOnError: false,
  strict: "ignore",
  trust: false,
  output: "htmlAndMathml",
} as const;

const REMARK_PLUGINS = [remarkMath];
const REHYPE_PLUGINS = [[rehypeKatex, KATEX_OPTIONS]] as ComponentProps<
  typeof Markdown
>["rehypePlugins"];

/** Only web addresses become links; anything else (a relative path, `javascript:`) is plain text. */
export function isWebUrl(href: string | undefined): href is string {
  if (href === undefined) return false;
  try {
    const url = new URL(href);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

export function SafeLink({ href, children }: { href?: string | undefined; children?: ReactNode }) {
  if (!isWebUrl(href)) return <span>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

/** A paper may point at an image on disk or on the web. Neither is loaded: the alt text stands in. */
function NoImage({ alt }: { alt?: string | undefined }) {
  return alt ? <span className="italic">[{alt}]</span> : null;
}

/** Headings of a paper start below the page heading of the screen that shows them. */
const heading = (level: 3 | 4 | 5 | 6) => {
  const Tag = `h${level}` as const;
  return function Heading({ children }: { children?: ReactNode }) {
    return <Tag>{children}</Tag>;
  };
};

const SAFE_COMPONENTS: Components = {
  a: ({ href, children }) => <SafeLink href={href}>{children}</SafeLink>,
  img: ({ alt }) => <NoImage alt={alt} />,
  h1: heading(3),
  h2: heading(4),
  h3: heading(5),
  h4: heading(6),
  h5: heading(6),
  h6: heading(6),
};

export interface MarkdownMathProps {
  children: string;
  /** Replaces the safe default of an element (the answer view turns citations into buttons). */
  components?: Components;
  className?: string;
}

/**
 * Markdown with LaTeX math for answers and papers. Raw HTML in the text is dropped (not
 * rendered, not escaped into view), links open only for web addresses, and no image is fetched.
 */
export function MarkdownMath({ children, components, className }: MarkdownMathProps) {
  return (
    <div className={cn("markdown", className)}>
      <Markdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        skipHtml
        components={{ ...SAFE_COMPONENTS, ...components }}
      >
        {children}
      </Markdown>
    </div>
  );
}
