import { Banner } from "../ds/components";

/** What a live session should know about: the last server error, and Whisper being away. */
export function SessionWarnings({
  sttOffline,
  error,
}: {
  sttOffline: boolean;
  error: { code: string; message: string } | null;
}) {
  return (
    <>
      {sttOffline && (
        <Banner variant="warning">
          Transcription offline. The audio is kept and will be transcribed when Whisper is back.
        </Banner>
      )}
      {error && !(sttOffline && error.code === "stt_unavailable") && (
        <Banner variant="error">{error.message}</Banner>
      )}
    </>
  );
}
