import { render, screen } from "@testing-library/react";
import { isWebUrl, MarkdownMath } from "./MarkdownMath";

describe("which addresses become links", () => {
  it("accepts web addresses only", () => {
    expect(isWebUrl("https://example.org/a?b=c")).toBe(true);
    expect(isWebUrl("http://example.org")).toBe(true);
    for (const href of [
      undefined,
      "",
      "javascript:alert(1)",
      "data:text/html,x",
      "mailto:a@b.c",
      "/relative/path",
      "//example.org/x",
      "figure.png",
      "#cite-1",
    ]) {
      expect(isWebUrl(href), String(href)).toBe(false);
    }
  });
});

describe("Markdown with math", () => {
  it("renders formulas as HTML with MathML for a screen reader", () => {
    const { container } = render(
      <MarkdownMath>{"Energy $E = mc^2$ and\n\n$$\n\\int_0^1 x\\,dx\n$$"}</MarkdownMath>,
    );
    expect(container.querySelectorAll(".katex").length).toBe(2);
    expect(container.querySelector(".katex-display")).not.toBeNull();
    expect(container.querySelector("math")).not.toBeNull();
    expect(container.querySelector(".katex-html")).toHaveAttribute("aria-hidden", "true");
  });

  it("shows a formula it cannot read as typed, without failing", () => {
    const { container } = render(<MarkdownMath>{"Broken $\\frac{1}{$ here"}</MarkdownMath>);
    expect(container.textContent).toContain("here");
    expect(container.querySelector(".katex-error")).not.toBeNull();
  });

  it("does not follow a formula that asks for a link", () => {
    const { container } = render(
      <MarkdownMath>{"$\\href{https://evil.example}{click}$"}</MarkdownMath>,
    );
    expect(container.querySelector("a")).toBeNull();
  });

  it("drops raw HTML instead of rendering or showing it", () => {
    const { container } = render(
      <MarkdownMath>
        {'Before\n\n<script>window.__x = 1</script>\n\n<b onclick="x()">bold</b>\n\nAfter'}
      </MarkdownMath>,
    );
    expect(container.querySelector("script, b")).toBeNull();
    expect(container.textContent).toContain("Before");
    expect(container.textContent).toContain("After");
    expect(container.textContent).not.toContain("<script>");
  });

  it("links web addresses and nothing else", () => {
    render(
      <MarkdownMath>
        {"[web](https://example.org) [js](javascript:alert(1)) [rel](/papers/1) [file](x.pdf)"}
      </MarkdownMath>,
    );
    const link = screen.getByRole("link", { name: "web" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("js")).toBeInTheDocument();
    expect(screen.getByText("rel")).toBeInTheDocument();
  });

  it("loads no image and shows its description instead", () => {
    const { container } = render(
      <MarkdownMath>
        {"![Figure 1: levels](figure1.png) and ![](https://example.org/p.png)"}
      </MarkdownMath>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("[Figure 1: levels]")).toBeInTheDocument();
  });

  it("starts the headings of a paper below the heading of the page", () => {
    const { container } = render(
      <MarkdownMath>
        {"# One\n\n## Two\n\n### Three\n\n#### Four\n\n##### Five\n\n###### Six"}
      </MarkdownMath>,
    );
    expect([...container.querySelectorAll("h1, h2, h3, h4, h5, h6")].map((h) => h.tagName)).toEqual(
      ["H3", "H4", "H5", "H6", "H6", "H6"],
    );
  });

  it("lets a caller replace an element", () => {
    render(
      <MarkdownMath components={{ em: ({ children }) => <mark>{children}</mark> }}>
        {"*marked*"}
      </MarkdownMath>,
    );
    expect(screen.getByText("marked").tagName).toBe("MARK");
  });

  it("keeps lists, code and quotes", () => {
    const { container } = render(
      <MarkdownMath>{"- a\n- b\n\n1. x\n\n> quote\n\n```\ncode\n```"}</MarkdownMath>,
    );
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelector("ol li")).not.toBeNull();
    expect(container.querySelector("blockquote")).not.toBeNull();
    expect(container.querySelector("pre code")).toHaveTextContent("code");
  });
});
