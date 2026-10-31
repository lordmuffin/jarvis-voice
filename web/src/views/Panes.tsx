import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { Pane as PaneBox, PaneBody, PaneGrid, SectionLabel, Segmented, TabBar } from "../ds/components";
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
      <PaneGrid>
        {panes.map((p) => (
          <PaneBox key={p.id} label={p.label} heading={<SectionLabel>{p.label}</SectionLabel>}>
            <PaneBody>{p.content}</PaneBody>
          </PaneBox>
        ))}
      </PaneGrid>
    );
  }
  const current = panes.find((p) => p.id === active) ?? panes[0]!;
  return (
    <PaneBox>
      <TabBar>
        <Segmented
          role="tablist"
          idPrefix="tab"
          label="Panes"
          value={current.id}
          options={panes.map((p) => ({ id: p.id, label: p.label }))}
          onChange={setActive}
        />
      </TabBar>
      <PaneBody role="tabpanel" id="tab-panel" aria-labelledby={`tab-${current.id}`}>
        {current.content}
      </PaneBody>
    </PaneBox>
  );
}
