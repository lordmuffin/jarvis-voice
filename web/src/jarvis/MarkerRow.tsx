import { clock } from "../format";
import type { Marker } from "../types";

/** A flag dropped in the transcript. With no label it reads "Marker". */
export function MarkerRow({ marker }: { marker: Marker }) {
  return (
    <div class="j-marker" data-testid="marker">
      <span>
        <span aria-hidden="true">⚑ </span>
        {marker.label || "Marker"}
      </span>
      <span class="t">· {clock(marker.t_ms)}</span>
    </div>
  );
}
