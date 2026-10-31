import { useEffect, useState } from "preact/hooks";
import { Button, Text } from "../ds/components";
import { copyText } from "../workitems";

/** Copies `text()` to the clipboard, then says so for a moment. */
export function CopyButton({ label, text }: { label: string; text: () => string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const t = window.setTimeout(() => setState("idle"), 2000);
    return () => window.clearTimeout(t);
  }, [state]);
  return (
    <>
      <Button variant="quiet" size="sm" onClick={async () => setState((await copyText(text())) ? "copied" : "failed")}>
        {state === "copied" ? "✓ Copied" : label}
      </Button>
      {state !== "idle" && (
        <Text as="span" size="meta" tone="muted" role="status">
          {state === "copied" ? "Copied to the clipboard." : "Couldn't copy."}
        </Text>
      )}
    </>
  );
}
