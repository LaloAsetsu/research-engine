import { useId, type ComponentProps } from "react";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

interface TextFieldProps extends Omit<ComponentProps<typeof Input>, "id"> {
  label: string;
  error?: string | undefined;
  hint?: string | undefined;
}

/** A labelled input whose hint and error are announced with it. */
export function TextField({ label, error, hint, ...input }: TextFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        {...input}
      />
      {hint ? (
        <p id={hintId} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
