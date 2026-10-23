import { z } from "zod";

/** The server's rule (scope section 3.1) mirrored for instant feedback; the server decides. */
export const MIN_PASSWORD_LENGTH = 12;

// Messages are translation keys; the form translates them when it shows them.
const required = { error: "auth.errors.required" };

export const loginSchema = z.object({
  username: z.string().trim().min(1, required),
  password: z.string().min(1, required),
});
export type LoginValues = z.infer<typeof loginSchema>;

export function changePasswordSchema(username: string) {
  return z
    .object({
      current: z.string().min(1, required),
      new: z.string().min(MIN_PASSWORD_LENGTH, { error: "auth.errors.tooShort" }),
      confirm: z.string().min(1, required),
    })
    .superRefine((values, context) => {
      if (values.new.length >= MIN_PASSWORD_LENGTH) {
        if (values.new.toLowerCase() === username.toLowerCase()) {
          context.addIssue({
            code: "custom",
            path: ["new"],
            message: "auth.errors.sameAsUsername",
          });
        } else if (values.new === values.current) {
          context.addIssue({ code: "custom", path: ["new"], message: "auth.errors.sameAsCurrent" });
        }
      }
      if (values.confirm !== "" && values.confirm !== values.new) {
        context.addIssue({ code: "custom", path: ["confirm"], message: "auth.errors.mismatch" });
      }
    });
}
export type ChangePasswordValues = z.infer<ReturnType<typeof changePasswordSchema>>;
