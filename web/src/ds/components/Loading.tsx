/** A pending load. */
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p class="d-loading" role="status">
      <span class="i-spin" aria-hidden="true" />
      {label}
    </p>
  );
}
