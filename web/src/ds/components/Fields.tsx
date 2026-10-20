import type {
  ComponentChildren,
  PartialInputHTMLAttributes,
  PartialSelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "preact";
import { cx } from "../classes";

/** A label, a control, and optional help or error text. `children` is the control. */
export function Field({
  label,
  htmlFor,
  help,
  error,
  hideLabel,
  class: c,
  children,
}: {
  label: ComponentChildren;
  htmlFor?: string;
  help?: ComponentChildren;
  error?: ComponentChildren;
  hideLabel?: boolean;
  class?: string;
  children: ComponentChildren;
}) {
  return (
    <div class={cx("i-field", c)}>
      <label class={cx("i-label", hideLabel && "i-sr")} for={htmlFor}>
        {label}
      </label>
      {children}
      {help && <span class="i-help">{help}</span>}
      {error && (
        <span class="i-msg-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

type InputProps = PartialInputHTMLAttributes<HTMLInputElement> & { mono?: boolean; invalid?: boolean };

export function Input({ mono, invalid, class: c, ...rest }: InputProps) {
  return (
    <input
      {...(rest as object)}
      class={cx("i-input", mono && "i-input--mono", c as string | undefined)}
      aria-invalid={invalid ? "true" : undefined}
    />
  );
}

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & { mono?: boolean; tall?: boolean };

export function Textarea({ mono, tall, class: c, ...rest }: TextareaProps) {
  return (
    <textarea
      {...(rest as object)}
      class={cx("i-input", "d-textarea", tall && "d-textarea--tall", mono && "i-input--mono", c as string | undefined)}
    />
  );
}

export function Select({ class: c, children, ...rest }: PartialSelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span class="i-select">
      <select {...(rest as object)} class={cx("i-input", c as string | undefined)}>
        {children}
      </select>
    </span>
  );
}

/** A checkbox with its label. */
export function Choice({
  label,
  ...rest
}: { label: ComponentChildren } & Omit<PartialInputHTMLAttributes<HTMLInputElement>, "type" | "label">) {
  return (
    <label class="i-choice">
      <input {...(rest as object)} type="checkbox" />
      <span>{label}</span>
    </label>
  );
}

/** A segmented radio group (tabs, theme, filters). */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  role = "radiogroup",
  idPrefix,
}: {
  label: string;
  value: T;
  options: readonly { id: T; label: string; count?: number }[];
  onChange: (id: T) => void;
  role?: "radiogroup" | "tablist";
  /** For tabs: ids `${idPrefix}-${id}` on the tab and `${idPrefix}-panel` it controls. */
  idPrefix?: string;
}) {
  const tabs = role === "tablist";
  return (
    <div class="i-seg" role={role} aria-label={label}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          id={tabs && idPrefix ? `${idPrefix}-${o.id}` : undefined}
          aria-controls={tabs && idPrefix ? `${idPrefix}-panel` : undefined}
          role={tabs ? "tab" : "radio"}
          aria-checked={tabs ? undefined : o.id === value}
          aria-selected={tabs ? o.id === value : undefined}
          onClick={() => onChange(o.id)}
        >
          {o.label}
          {o.count !== undefined && (
            <>
              {" "}
              <span class="t-mono">{o.count}</span>
            </>
          )}
        </button>
      ))}
    </div>
  );
}

/** A search box that takes the free space at the end of a toolbar. */
export function SearchInput(props: PartialInputHTMLAttributes<HTMLInputElement>) {
  return <Input {...props} type="search" class="d-search" />;
}
