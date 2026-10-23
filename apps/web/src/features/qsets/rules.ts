import type { Me } from "@/shared/api/types";
import type { ReviewLine, SetVersion } from "./types";

/** Who changes a draft: its owner or an admin. Nobody changes a frozen version. The server decides; this only hides what would be refused. */
export function canEditDraft(
  me: Pick<Me, "username" | "role"> | undefined,
  version: Pick<SetVersion, "owner" | "status">,
): boolean {
  if (!me || version.status !== "draft") return false;
  return me.role === "admin" || me.username === version.owner;
}

/** The latest decision of one reviewer (the lines come oldest first). */
export function latestDecisionBy(
  reviews: readonly ReviewLine[],
  username: string | undefined,
): ReviewLine | undefined {
  if (username === undefined) return undefined;
  return reviews.filter((line) => line.reviewer === username).at(-1);
}

/** A decision made on an earlier state of the draft (the draft has changed since). */
export function decidedOnEarlierDraft(line: ReviewLine, currentMd5: string): boolean {
  return line.set_md5 != null && line.set_md5 !== currentMd5;
}

/** How many items have a decision of any kind. */
export function decidedCount(review: { items: number; none: number }): number {
  return review.items - review.none;
}

/** The md5 without the quotes of an ETag. */
export function bareTag(etag: string): string {
  return etag.replace(/^W\//, "").replaceAll('"', "");
}
