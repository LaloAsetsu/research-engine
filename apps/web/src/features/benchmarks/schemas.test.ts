import { runFormSchema, type RunFormValues } from "./schemas";

const valid: RunFormValues = {
  set: "demo-papers/v1",
  arms: ["bare", "harness"],
  models: ["fake-model"],
  repeats: 2,
  notes: "",
};

function messages(values: unknown): string[] {
  const result = runFormSchema.safeParse(values);
  return result.success ? [] : result.error.issues.map((issue) => issue.message);
}

describe("the run form schema", () => {
  it("accepts a set, arms, models and repeats", () => {
    expect(runFormSchema.safeParse(valid).success).toBe(true);
    expect(
      runFormSchema.safeParse({ ...valid, repeats: 10, notes: "x".repeat(1000) }).success,
    ).toBe(true);
  });

  it("asks for a set, an arm and a model", () => {
    expect(messages({ ...valid, set: "" })).toEqual(["benchmarks.form.errors.set"]);
    expect(messages({ ...valid, arms: [] })).toEqual(["benchmarks.form.errors.arms"]);
    expect(messages({ ...valid, models: [] })).toEqual(["benchmarks.form.errors.models"]);
  });

  it("refuses an arm the API does not have and more than four models", () => {
    expect(messages({ ...valid, arms: ["magic"] })).not.toEqual([]);
    expect(messages({ ...valid, models: ["a", "b", "c", "d", "e"] })).toEqual([
      "benchmarks.form.errors.manyModels",
    ]);
  });

  it("keeps repeats a whole number from 1 to 10", () => {
    for (const repeats of [0, 11, 1.5, Number.NaN, -1]) {
      expect(messages({ ...valid, repeats }), String(repeats)).toEqual([
        "benchmarks.form.errors.repeats",
      ]);
    }
  });

  it("limits the notes to what the API takes", () => {
    expect(messages({ ...valid, notes: "x".repeat(1001) })).toEqual([
      "benchmarks.form.errors.notes",
    ]);
  });
});
