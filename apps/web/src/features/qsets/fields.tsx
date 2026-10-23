import { useId, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/shared/lib/utils";
import { Label } from "@/shared/ui/label";
import { Textarea } from "@/shared/ui/textarea";

/** A button that removes or refuses something: outlined, because axe measures the tinted destructive variant below 4.5:1 on a light page. */
export const DESTRUCTIVE = "border-destructive/40 text-destructive hover:text-destructive";

export const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive dark:bg-input/30";

/** What every field shows under its control: a hint, and the problems the API found at it. */
function FieldNotes({
  id,
  hint,
  errors,
}: {
  id: string;
  hint?: string | undefined;
  errors?: readonly string[] | undefined;
}) {
  return (
    <>
      {hint ? (
        <p id={`${id}-hint`} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {errors?.length ? (
        <ul id={`${id}-error`} className="grid gap-0.5 text-sm text-destructive">
          {errors.map((message, index) => (
            <li key={`${index}-${message}`}>{message}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

function describedBy(id: string, hint?: string, errors?: readonly string[]) {
  const parts = [hint ? `${id}-hint` : null, errors?.length ? `${id}-error` : null].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : undefined;
}

interface SelectFieldProps extends Omit<ComponentProps<"select">, "id" | "onChange" | "value"> {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  options: readonly { value: string; label: string }[];
  hint?: string;
  errors?: readonly string[];
}

export function SelectField({
  label,
  value,
  onValueChange,
  options,
  hint,
  errors,
  className,
  ...rest
}: SelectFieldProps) {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        className={cn(SELECT_CLASS, className)}
        value={value}
        aria-invalid={errors?.length ? true : undefined}
        aria-describedby={describedBy(id, hint, errors)}
        onChange={(event) => onValueChange(event.target.value)}
        {...rest}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <FieldNotes id={id} hint={hint} errors={errors} />
    </div>
  );
}

interface AreaFieldProps extends Omit<ComponentProps<"textarea">, "id" | "onChange" | "value"> {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  hint?: string;
  errors?: readonly string[];
  mono?: boolean;
}

export function AreaField({
  label,
  value,
  onValueChange,
  hint,
  errors,
  mono,
  className,
  ...rest
}: AreaFieldProps) {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Textarea
        id={id}
        value={value}
        className={cn(mono && "font-mono text-xs md:text-xs", className)}
        aria-invalid={errors?.length ? true : undefined}
        aria-describedby={describedBy(id, hint, errors)}
        onChange={(event) => onValueChange(event.target.value)}
        {...rest}
      />
      <FieldNotes id={id} hint={hint} errors={errors} />
    </div>
  );
}

/** A field with a custom control: gives it the label, hint and problems around it. */
export function FieldShell({
  label,
  hint,
  errors,
  children,
}: {
  label: string;
  hint?: string;
  errors?: readonly string[];
  children: (props: { id: string; "aria-describedby": string | undefined }) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children({ id, "aria-describedby": describedBy(id, hint, errors) })}
      <FieldNotes id={id} hint={hint} errors={errors} />
    </div>
  );
}
