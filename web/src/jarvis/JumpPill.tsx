/** Floats above the dock when you've scrolled up: how much arrived, and a way back. */
export function JumpPill({ count, onJump }: { count: number; onJump: () => void }) {
  return (
    <div class="d-jump" aria-live="polite">
      <button type="button" class="i-btn i-btn--secondary i-jump" onClick={onJump}>
        ↓ Jump to latest{count > 0 ? ` · ${count} new` : ""}
      </button>
    </div>
  );
}
