import openapi from "@/api/openapi.json";
import { PENDING_PATHS } from "./pending";

describe("the pending sign-in paths", () => {
  it("are not in openapi.json yet; delete src/shared/api/pending.ts once they are", () => {
    const published = Object.keys(openapi.paths);
    for (const path of PENDING_PATHS) expect(published).not.toContain(path);
  });
});
