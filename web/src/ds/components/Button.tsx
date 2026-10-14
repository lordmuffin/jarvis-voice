import type { PartialAnchorHTMLAttributes, ButtonHTMLAttributes, ComponentChildren } from "preact";
import { cx } from "../classes";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger" | "danger-quiet";
export type ButtonSize = "sm" | "md" | "lg";

const look = (variant: ButtonVariant, size: ButtonSize, extra?: string) =>
  cx("i-btn", `i-btn--${variant}`, size !== "md" && `i-btn--${size}`, extra);

type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "size"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

/** A button. Defaults to type="button" so it never submits a form by accident. */
export function Button({ variant = "secondary", size = "md", type = "button", class: c, ...rest }: ButtonProps) {
  return <button {...rest} type={type} class={look(variant, size, c as string | undefined)} />;
}

type ButtonLinkProps = Omit<PartialAnchorHTMLAttributes<HTMLAnchorElement>, "size"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  href: string;
  children?: ComponentChildren;
};

/** A link that looks like a button (e.g. "Review" opening a pull request). */
export function ButtonLink({ variant = "secondary", size = "md", class: c, ...rest }: ButtonLinkProps) {
  return <a {...(rest as object)} class={look(variant, size, c as string | undefined)} />;
}

/** A button that opens the file picker; put the (hidden) file input in `children`. */
export function FilePick({ label, children }: { label: ComponentChildren; children: ComponentChildren }) {
  return (
    <label class="i-btn i-btn--secondary d-file">
      {label}
      {children}
    </label>
  );
}
