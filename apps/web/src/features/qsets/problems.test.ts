import { ApiError } from "@/shared/api/errors";
import {
  conflictKind,
  groupByField,
  innerPath,
  pointerParts,
  problemSlug,
  rowProblems,
} from "./problems";

const problem = (status: number, slug: string) =>
  new ApiError({ status, type: `urn:research-api:problem:${slug}` });

describe("telling the conflicts apart", () => {
  it("reads the end of the problem type", () => {
    expect(problemSlug(problem(409, "item-has-decisions"))).toBe("item-has-decisions");
    expect(problemSlug(new ApiError({ status: 500 }))).toBe("blank");
  });

  it("sees a stale draft and an item with decisions", () => {
    expect(conflictKind(problem(409, "conflict"))).toBe("stale");
    expect(conflictKind(problem(409, "item-has-decisions"))).toBe("item-has-decisions");
  });

  it("sees no conflict in anything else", () => {
    expect(conflictKind(problem(422, "invalid-input"))).toBeNull();
    expect(conflictKind(new Error("x"))).toBeNull();
    expect(conflictKind(null)).toBeNull();
  });
});

describe("JSON pointers", () => {
  it("splits and decodes", () => {
    expect(pointerParts("/gold_papers/0/doc_id")).toEqual(["gold_papers", "0", "doc_id"]);
    expect(pointerParts("/a~1b/c~0d")).toEqual(["a/b", "c~d"]);
    expect(pointerParts("")).toEqual([]);
    expect(pointerParts("/")).toEqual([]);
  });

  it("gives the place inside a field", () => {
    expect(innerPath("/gold_papers/0/doc_id")).toBe("/0/doc_id");
    expect(innerPath("/question")).toBe("");
  });
});

describe("grouping problems by the field that shows them", () => {
  const list = [
    { path: "/question", message: "required" },
    { path: "/gold_papers/0/doc_id", message: "bad id" },
    { path: "/gold_papers/1/passages", message: "required" },
    { path: "", message: "whole item" },
  ];

  it("groups by the first part of the pointer", () => {
    const { byField, whole } = groupByField(list);
    expect(byField.get("question")).toHaveLength(1);
    expect(byField.get("gold_papers")).toHaveLength(2);
    expect(whole).toEqual([{ path: "", message: "whole item" }]);
  });

  it("finds the problems of one row of a list", () => {
    const facts = [
      { path: "/key_facts/0/rel", message: "needs rel" },
      { path: "/key_facts/1/value", message: "not a number" },
      { path: "/key_facts/1", message: "row problem" },
      { path: "/key_facts/1/value", message: "again" },
    ];
    const row = rowProblems(facts, 1);
    expect(row.get("value")).toEqual(["not a number", "again"]);
    expect(row.get("")).toEqual(["row problem"]);
    expect(rowProblems(facts, 0).get("rel")).toEqual(["needs rel"]);
    expect(rowProblems(undefined, 0).size).toBe(0);
  });
});
