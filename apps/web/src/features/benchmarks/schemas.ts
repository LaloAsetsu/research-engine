import { z } from "zod";
import { ARMS } from "./types";

/** The limits of `POST /runs`, mirrored for instant feedback; the server decides. */
export const MAX_REPEATS = 10;
export const MAX_NOTES = 1000;

// Messages are translation keys; the form translates them when it shows them.
export const runFormSchema = z.object({
  set: z.string().min(1, { error: "benchmarks.form.errors.set" }),
  arms: z
    .array(z.enum(ARMS))
    .min(1, { error: "benchmarks.form.errors.arms" })
    .max(ARMS.length, { error: "benchmarks.form.errors.arms" }),
  models: z
    .array(z.string().min(1))
    .min(1, { error: "benchmarks.form.errors.models" })
    .max(4, { error: "benchmarks.form.errors.manyModels" }),
  repeats: z
    .number({ error: "benchmarks.form.errors.repeats" })
    .int({ error: "benchmarks.form.errors.repeats" })
    .min(1, { error: "benchmarks.form.errors.repeats" })
    .max(MAX_REPEATS, { error: "benchmarks.form.errors.repeats" }),
  notes: z.string().max(MAX_NOTES, { error: "benchmarks.form.errors.notes" }),
});
export type RunFormValues = z.infer<typeof runFormSchema>;
