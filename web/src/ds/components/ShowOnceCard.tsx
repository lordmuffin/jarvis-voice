import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Banner } from "./Banner";
import { Button } from "./Button";
import { Field, Input } from "./Fields";
import { Text } from "./Text";

/**
 * A secret the server will never show again (a device token). Selected on arrival, with a Copy
 * button and a Done that dismisses it for good.
 */
export function ShowOnceCard({
  heading,
  intro,
  warning,
  value,
  valueLabel,
  footnote,
  onDone,
}: {
  heading: ComponentChildren;
  intro: ComponentChildren;
  warning: ComponentChildren;
  value: string;
  /** Names the field for screen readers and tests, e.g. "Device token". */
  valueLabel: string;
  footnote?: ComponentChildren;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const input = () => wrap.current?.querySelector<HTMLInputElement>("input") ?? null;

  useEffect(() => {
    input()?.select();
  }, [value]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied("yes");
    } catch {
      input()?.select();
      setCopied("no");
    }
  }

  return (
    <div class="d-token" role="status" data-testid="new-token">
      <Text as="h2" size="body-lg" strong>
        {heading}
      </Text>
      <Text as="p">{intro}</Text>
      <Banner variant="warning">
        <span aria-hidden="true">⚑ </span>
        {warning}
      </Banner>
      <Field label="Token" hideLabel htmlFor="show-once-value">
        <div class="d-form-row" ref={wrap}>
          <Input
            id="show-once-value"
            mono
            readOnly
            aria-label={valueLabel}
            value={value}
            onFocus={(e) => (e.target as HTMLInputElement).select()}
          />
          <Button onClick={() => void copy()}>⎘ Copy</Button>
        </div>
      </Field>
      {copied === "yes" && (
        <Text size="meta" tone="muted">
          Copied to the clipboard.
        </Text>
      )}
      {copied === "no" && (
        <Text size="meta" tone="muted">
          Couldn't copy. The token is selected: copy it by hand.
        </Text>
      )}
      <div class="d-bar">
        <Button variant="primary" onClick={onDone}>
          Done
        </Button>
        {footnote && (
          <Text size="meta" tone="muted">
            {footnote}
          </Text>
        )}
      </div>
    </div>
  );
}
