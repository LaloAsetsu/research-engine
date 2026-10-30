import { CAPTURE } from "@/test/benchmarks-api";
import { isArmName, readKeyResults, readQuestionDetail } from "./types";

describe("readKeyResults", () => {
  it("reads the key records of a score item", () => {
    expect(
      readKeyResults([
        { i: 0, exact: true, lenient: true },
        { i: 2, exact: false, lenient: true },
        { i: 3 },
      ]),
    ).toEqual([
      { index: 0, exact: true, lenient: true },
      { index: 2, exact: false, lenient: true },
      { index: 3, exact: false, lenient: false },
    ]);
  });

  it("leaves out what is not a key record", () => {
    expect(readKeyResults(null)).toEqual([]);
    expect(readKeyResults("x")).toEqual([]);
    expect(
      readKeyResults([null, 5, [], { i: "0" }, { i: -1 }, { i: 1.5 }, { exact: true }]),
    ).toEqual([]);
  });

  it("reads every record the real API wrote", () => {
    for (const items of Object.values(CAPTURE.scoreItems)) {
      for (const item of items) {
        const keys = readKeyResults(item.keys);
        expect(keys).toHaveLength(item.keys?.length ?? 0);
      }
    }
  });
});

describe("readQuestionDetail", () => {
  it("reads the key facts and gold papers of a recorded question", () => {
    const detail = readQuestionDetail(CAPTURE.details["DP-002"]);
    expect(detail?.id).toBe("DP-002");
    expect(detail?.keyFacts.map((fact) => fact.value)).toEqual(["J", "three couplings"]);
    expect(detail?.goldPapers).toHaveLength(2);
    expect(detail?.goldPapers[0]?.doc).toBe("alpha.pdf");
    expect(detail?.answer).toContain("Beta replaces");
  });

  it("keeps a number as text and leaves out what is not a fact", () => {
    const detail = readQuestionDetail({
      item: {
        id: "n",
        question: "q",
        key_facts: [{ type: "num", value: 3, unit: "ns" }, "x", { type: "text" }, null],
        gold_papers: [{ doc_id: "d1" }, { doc: "x" }, 5],
      },
    });
    expect(detail?.keyFacts).toEqual([{ type: "num", value: "3", unit: "ns", paper: null }]);
    expect(detail?.goldPapers).toEqual([{ docId: "d1", doc: null }]);
    expect(detail?.answer).toBeNull();
  });

  it("refuses a body that is not an item with an id and a question", () => {
    expect(readQuestionDetail(null)).toBeNull();
    expect(readQuestionDetail({})).toBeNull();
    expect(readQuestionDetail({ item: { id: "x" } })).toBeNull();
    expect(readQuestionDetail({ item: [] })).toBeNull();
  });
});

describe("isArmName", () => {
  it("knows the three arms", () => {
    expect(["bare", "harness", "oracle"].every(isArmName)).toBe(true);
    expect(isArmName("magic")).toBe(false);
  });
});
