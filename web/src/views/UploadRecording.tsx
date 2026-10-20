import { useRef, useState } from "preact/hooks";
import { ApiError, uploadRecording } from "../api";
import { FilePick } from "../ds/components";
import { UploadForm } from "../jarvis/UploadForm";
import { navigate } from "../store";

// What phones and recorders save; the server decodes anything ffmpeg reads.
const ACCEPT = "audio/*,video/*,.m4a,.mp3,.wav,.ogg,.opus,.flac,.aac,.amr,.caf,.webm,.wma";

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
  const [mode, setMode] = useState<"solo" | "meeting">("solo");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setFile(null);
    setMode("solo");
    setError(null);
    if (input.current) input.current.value = "";
  }

  async function submit(e: Event) {
    e.preventDefault();
    if (!file || busy) return;
    const form = new FormData(e.currentTarget as HTMLFormElement);
    const title = String(form.get("title") ?? "");
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
        <UploadForm
          file={file}
          mode={mode}
          busy={busy}
          error={error}
          onMode={setMode}
          onSubmit={submit}
          onCancel={reset}
          onChangeFile={() => input.current?.click()}
        />
      )}
    </>
  );
}
