import {
  bareTag,
  canEditDraft,
  decidedCount,
  decidedOnEarlierDraft,
  latestDecisionBy,
} from "./rules";
import type { ReviewLine } from "./types";

const draft = { owner: "beto", status: "draft" } as const;
const frozen = { owner: "beto", status: "frozen" } as const;

describe("who may edit a draft", () => {
  it("lets the owner and an admin", () => {
    expect(canEditDraft({ username: "beto", role: "member" }, draft)).toBe(true);
    expect(canEditDraft({ username: "ana", role: "admin" }, draft)).toBe(true);
  });

  it("does not let another member", () => {
    expect(canEditDraft({ username: "dani", role: "member" }, draft)).toBe(false);
  });

  it("lets nobody change a frozen version", () => {
    expect(canEditDraft({ username: "ana", role: "admin" }, frozen)).toBe(false);
    expect(canEditDraft({ username: "beto", role: "member" }, frozen)).toBe(false);
  });

  it("lets nobody before the user is known", () => {
    expect(canEditDraft(undefined, draft)).toBe(false);
  });
});

const line = (
  reviewer: string,
  verdict: ReviewLine["verdict"],
  set_md5: string | null,
): ReviewLine => ({
  item_id: "A",
  target: "item",
  reviewer,
  role: "human",
  verdict,
  date: "2026-01-01T00:00:00+00:00",
  set_md5,
});

describe("reading decisions", () => {
  const reviews = [
    line("ana", "accept", "m1"),
    line("beto", "reject", "m1"),
    line("ana", "edit", "m2"),
  ];

  it("finds the latest decision of a reviewer", () => {
    expect(latestDecisionBy(reviews, "ana")?.verdict).toBe("edit");
    expect(latestDecisionBy(reviews, "beto")?.verdict).toBe("reject");
    expect(latestDecisionBy(reviews, "dani")).toBeUndefined();
    expect(latestDecisionBy(reviews, undefined)).toBeUndefined();
  });

  it("knows a decision made on an earlier state of the draft", () => {
    expect(decidedOnEarlierDraft(line("ana", "accept", "m1"), "m2")).toBe(true);
    expect(decidedOnEarlierDraft(line("ana", "accept", "m2"), "m2")).toBe(false);
    expect(decidedOnEarlierDraft(line("ana", "accept", null), "m2")).toBe(false);
  });

  it("counts the items that have a decision", () => {
    expect(decidedCount({ items: 7, none: 3 })).toBe(4);
  });

  it("takes the quotes off an etag", () => {
    expect(bareTag('"abc"')).toBe("abc");
    expect(bareTag('W/"abc"')).toBe("abc");
  });
});
