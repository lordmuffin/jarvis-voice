import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { useWide } from "../useWide";

export interface Pane {
  id: string;
  label: string;
  content: ComponentChildren;
}

/** Side by side on wide screens, tabs on narrow ones. */
export function Panes({ panes }: { panes: Pane[] }) {
  const wide = useWide();
  const [active, setActive] = useState(panes[0]!.id);
  if (wide) {
    return (
      <div class="panes panes-wide">
        {panes.map((p) => (
          <section key={p.id} class="pane" aria-label={p.label}>
            <h2 class="pane-title label">{p.label}</h2>
            <div class="pane-body">{p.content}</div>
          </section>
        ))}
      </div>
    );
  }
  const current = panes.find((p) => p.id === active) ?? panes[0]!;
  return (
    <div class="panes">
      <div class="tabs" role="tablist">
        {panes.map((p) => (
          <button
            key={p.id}
            role="tab"
            id={`tab-${p.id}`}
            aria-selected={p.id === current.id}
            aria-controls={`panel-${p.id}`}
            class={`tab${p.id === current.id ? " tab-active" : ""}`}
            onClick={() => setActive(p.id)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div
        class="pane-body"
        role="tabpanel"
        id={`panel-${current.id}`}
        aria-labelledby={`tab-${current.id}`}
      >
        {current.content}
      </div>
    </div>
  );
}
