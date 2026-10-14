import { useRef, useState } from "preact/hooks";
import { ApiError, uploadRecording } from "../api";
import { Banner, Button, Field, FilePick, InlineForm, Input, Select, Text } from "../ds/components";
import { navigate } from "../store";

// What phones and recorders save; the server decodes anything ffmpeg reads.
const ACCEPT = "audio/*,video/*,.m4a,.mp3,.wav,.ogg,.opus,.flac,.aac,.amr,.caf,.webm,.wma";

const stem = (name: string) => name.replace(/\.[^.]+$/, "");

/**
 * Upload a recording made elsewhere (a voice memo, a meeting recorded on another app). It
 * becomes a session that is transcribed and gets a note like a live one; `onUploaded` gets its
 * id (default: open it, so the transcript can be watched arriving).
 */
export function UploadRecording({
  onUploaded = (id) => navigate(`/sessions/${id}`),
}: {
  onUploaded?: (id: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setFile(null);
    setError(null);
    if (input.current) input.current.value = "";
  }

  async function submit(e: Event) {
    e.preventDefault();
    if (!file || busy) return;
    const form = new FormData(e.currentTarget as HTMLFormElement);
    const title = String(form.get("title") ?? "");
    const mode = form.get("mode") === "meeting" ? "meeting" : "solo";
    setBusy(true);
    setError(null);
    try {
      const id = await uploadRecording(file, { mode, title });
      reset();
      onUploaded(id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't upload the recording.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <FilePick label="Upload recording">
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            const chosen = (e.target as HTMLInputElement).files?.[0] ?? null;
            setFile(chosen);
            setError(null);
          }}
        />
      </FilePick>
      {file && (
        <InlineForm data-testid="upload-form" onSubmit={submit}>
          <Text size="meta" tone="muted" wrap>
            {file.name}
          </Text>
          <Field label="Title" hideLabel htmlFor="upload-title">
            <Input id="upload-title" name="title" placeholder={stem(file.name)} aria-label="Title" />
          </Field>
          <Field label="Mode" hideLabel htmlFor="upload-mode">
            <Select id="upload-mode" name="mode" aria-label="Mode">
              <option value="solo">Solo</option>
              <option value="meeting">Meeting</option>
            </Select>
          </Field>
          <Button variant="primary" type="submit" disabled={busy}>
            {busy ? "Uploading…" : "Upload"}
          </Button>
          <Button variant="quiet" data-testid="upload-cancel" onClick={reset}>
            Cancel
          </Button>
          {error && <Banner variant="error">{error}</Banner>}
        </InlineForm>
      )}
    </>
  );
}
