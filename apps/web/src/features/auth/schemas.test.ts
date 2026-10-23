import { changePasswordSchema, loginSchema } from "./schemas";

const issues = (result: {
  success: boolean;
  error?: { issues: { path: PropertyKey[]; message: string }[] };
}) => (result.error?.issues ?? []).map((issue) => `${String(issue.path[0])}:${issue.message}`);

describe("loginSchema", () => {
  it("needs both fields", () => {
    expect(issues(loginSchema.safeParse({ username: " ", password: "" }))).toEqual([
      "username:auth.errors.required",
      "password:auth.errors.required",
    ]);
    expect(loginSchema.safeParse({ username: " ana ", password: "x" }).data?.username).toBe("ana");
  });
});

describe("changePasswordSchema", () => {
  const schema = changePasswordSchema("ana");
  const ok = { current: "old-password-1", new: "a-fresh-password", confirm: "a-fresh-password" };

  it("accepts a good change", () => {
    expect(schema.safeParse(ok).success).toBe(true);
  });

  it("asks for twelve characters", () => {
    expect(issues(schema.safeParse({ ...ok, new: "short", confirm: "short" }))).toEqual([
      "new:auth.errors.tooShort",
    ]);
  });

  it("refuses the username, in any case", () => {
    const long = changePasswordSchema("a-very-long-username");
    expect(
      issues(
        long.safeParse({ ...ok, new: "A-Very-Long-Username", confirm: "A-Very-Long-Username" }),
      ),
    ).toEqual(["new:auth.errors.sameAsUsername"]);
  });

  it("refuses the current password", () => {
    expect(issues(schema.safeParse({ ...ok, current: "a-fresh-password" }))).toEqual([
      "new:auth.errors.sameAsCurrent",
    ]);
  });

  it("wants the confirmation to match", () => {
    expect(issues(schema.safeParse({ ...ok, confirm: "another-password" }))).toEqual([
      "confirm:auth.errors.mismatch",
    ]);
    expect(issues(schema.safeParse({ ...ok, confirm: "" }))).toEqual([
      "confirm:auth.errors.required",
    ]);
  });
});
