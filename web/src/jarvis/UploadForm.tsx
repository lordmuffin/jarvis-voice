import { Banner, Button, Field, InlineForm, Input, Segmented, Text } from "../ds/components";
import { bytes } from "../format";

type Mode = "solo" | "meeting";

/**
 * Title and mode for a chosen recording, then Upload. `progress` (bytes sent) and `blocked`
 * (a reason checked before sending) are for states the artboards show but nothing drives yet:
 * the browser upload reports no progress, and the size/empty checks live on the server.
 */
export function UploadForm({
  file,
  mode,
  busy,
  error,
  progress,
  blocked,
  onMode,
  onSubmit,
  onCancel,
  onChangeFile,
}: {
  file: { name: string; size: number };
  mode: Mode;
  busy: boolean;
  error: string | null;
  progress?: { sent: number; total: number };
  blocked?: string;
  onMode: (m: Mode) => void;
  onSubmit: (e: Event) => void;
  onCancel: () => void;
  onChangeFile: () => void;
}) {
  const stem = file.name.replace(/\.[^.]+$/, "");
  const pct = progress ? Math.round((progress.sent / progress.total) * 100) : null;
  return (
    <InlineForm data-testid="upload-form" onSubmit={onSubmit}>
      <div class="i-field">
        <span class="i-label">File</span>
        <div class="i-row">
          <Text size="caption" mono wrap>
            {file.name}
          </Text>
          <Text size="meta" tone="muted" mono>
            {bytes(file.size)}
          </Text>
          {!busy && (
            <Button variant="quiet" size="sm" onClick={onChangeFile}>
              Change
            </Button>
          )}
        </div>
      </div>
      <Field label="Title (optional)" htmlFor="upload-title">
        <Input id="upload-title" name="title" placeholder={stem} disabled={busy} />
      </Field>
      <div class="i-field">
        <span class="i-label">Mode</span>
        <Segmented<Mode>
          label="Mode"
          value={mode}
          onChange={onMode}
          options={[
            { id: "solo", label: "Solo" },
            { id: "meeting", label: "Meeting" },
          ]}
        />
      </div>
      <div class="d-bar">
        <Button variant="primary" type="submit" disabled={busy || blocked !== undefined}>
          {busy ? "Uploading…" : "Upload"}
        </Button>
        <Button variant="quiet" data-testid="upload-cancel" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {pct !== null && (
        <div class="i-stack">
          <div class="i-progress" role="progressbar" aria-label="Upload progress" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <span
              ref={(el) => {
                if (el) el.style.width = `${pct}%`;
              }}
            />
          </div>
          <span class="t-mono t-meta t-2">
            {bytes(progress!.sent)} of {bytes(progress!.total)} · {pct}%
          </span>
        </div>
      )}
      {(error ?? blocked) && <Banner variant="error">{error ?? blocked}</Banner>}
    </InlineForm>
  );
}
