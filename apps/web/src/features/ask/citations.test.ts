import { answerOf } from "@/test/ask-api";
import { citationIndex, linkCitations, shortId } from "./citations";
import type { Citation } from "./types";

const CITATIONS: Citation[] = [
  {
    key: "aaaaaaaaaaaaaaaa pages 3-4",
    doc_id: `aaaaaaaaaaaaaaaa${"0".repeat(48)}`,
    first_page: 3,
    last_page: 4,
  },
  {
    key: "bbbbbbbbbbbbbbbb pages 1-1",
    doc_id: `bbbbbbbbbbbbbbbb${"0".repeat(48)}`,
    first_page: 1,
    last_page: 1,
  },
];

describe("linking the citations of an answer", () => {
  it("turns each printed citation into a link to its position", () => {
    const text = "One (aaaaaaaaaaaaaaaa pages 3-4) and two (bbbbbbbbbbbbbbbb pages 1-1).";
    expect(linkCitations(text, CITATIONS)).toBe(
      "One [(aaaaaaaaaaaaaaaa pages 3-4)](#cite-0) and two [(bbbbbbbbbbbbbbbb pages 1-1)](#cite-1).",
    );
  });

  it("links every occurrence of a citation", () => {
    const text = "(aaaaaaaaaaaaaaaa pages 3-4) again (aaaaaaaaaaaaaaaa pages 3-4)";
    expect(linkCitations(text, CITATIONS)).toBe(
      "[(aaaaaaaaaaaaaaaa pages 3-4)](#cite-0) again [(aaaaaaaaaaaaaaaa pages 3-4)](#cite-0)",
    );
  });

  it("leaves any other parenthesis alone", () => {
    const text = "A fact (see Smith) and (cccccccccccccccc pages 1-2) which is not cited.";
    expect(linkCitations(text, CITATIONS)).toBe(text);
  });

  it("uses the first position of a citation listed twice", () => {
    const twice = [CITATIONS[0]!, CITATIONS[0]!];
    expect(linkCitations("(aaaaaaaaaaaaaaaa pages 3-4)", twice)).toBe(
      "[(aaaaaaaaaaaaaaaa pages 3-4)](#cite-0)",
    );
  });

  it("changes nothing when there are no citations", () => {
    expect(linkCitations("No sources here.", [])).toBe("No sources here.");
  });

  it("links every citation of a recorded answer to a position that exists", () => {
    const answer = answerOf("transmon");
    const linked = linkCitations(answer.answer, answer.citations);
    const found = [...linked.matchAll(/\]\(#cite-(\d+)\)/g)].map((match) => Number(match[1]));
    expect(found.length).toBeGreaterThan(0);
    for (const index of found) expect(answer.citations[index]).toBeDefined();
  });
});

describe("reading a citation link", () => {
  it("reads the position", () => {
    expect(citationIndex("#cite-0")).toBe(0);
    expect(citationIndex("#cite-12")).toBe(12);
  });

  it("ignores any other link", () => {
    expect(citationIndex(undefined)).toBeNull();
    expect(citationIndex("https://example.org")).toBeNull();
    expect(citationIndex("#cite-")).toBeNull();
    expect(citationIndex("#cite-1x")).toBeNull();
    expect(citationIndex("#other-1")).toBeNull();
  });
});

describe("the short id", () => {
  it("is the first 16 characters", () => {
    expect(shortId(`0123456789abcdef${"f".repeat(48)}`)).toBe("0123456789abcdef");
    expect(shortId("abc")).toBe("abc");
  });
});
