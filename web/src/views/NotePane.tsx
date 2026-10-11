import { useEffect, useState } from "preact/hooks";
import {
  ApiError,
  editNote,
  getPublication,
  getSession,
  publicationAction,
  regenerateNote,
  type PublishAction,
} from "../api";
import type { SessionDetail } from "../types";
import { NoteView } from "./NoteView";
import { PublicationChip } from "./PublicationChip";

const BUSY: Record<PublishAction | "save" | "regenerate", string> = {
  publish: "Opening a pull request…",
  merge: "Merging…",
  close: "Closing the pull request…",
  skip: "Skipping…",
  unskip: "Un-skipping…",
  save: "Saving…",
  regenerate: "Starting…",
};

/**
 * The finished note with what can be done to it: edit, regenerate, and (for the owner and
 * admins) publish to the vault repo as a pull request, then merge or close it.
 * `onChanged` gets the server's updated session.
 */
export function NotePane({
  detail,
  onChanged,
}: {
  detail: SessionDetail;
  onChanged: (d: SessionDetail) => void;
}) {
  const note = detail.final_note!;
  const pub = detail.publication;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<keyof typeof BUSY | null>(null);
  const [error, setError] = useState<string | null>(null);

  // An open PR may have been merged or closed on GitHub since: ask the server to check.
  useEffect(() => {
    if (pub?.state !== "open") return;
    let stopped = false;
    getPublication(detail.id).then(
      async (r) => {
        if (stopped || r.publication?.state === "open") return;
        const d = await getSession(detail.id);
        if (!stopped) onChanged(d);
      },
      () => undefined,
    );
    return () => {
      stopped = true;
    };
  }, [detail.id]);

  async function run(what: keyof typeof BUSY, fn: () => Promise<SessionDetail>): Promise<boolean> {
    setBusy(what);
    setError(null);
    try {
      onChanged(await fn());
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return false;
      setError(e instanceof ApiError ? e.message : "Something went wrong. Try again.");
      return false;
    } finally {
      setBusy(null);
    }
  }

  const act = (action: PublishAction) => run(action, () => publicationAction(detail.id, action));

  function merge() {
    if (confirm("Merge this note into the vault's main branch?")) void act("merge");
  }

  function regenerate() {
    const msg = "Write the note again from the transcript? The current note, edits included, is replaced.";
    if (confirm(msg)) void run("regenerate", () => regenerateNote(detail.id));
  }

  async function save() {
    if (draft === note.markdown) return setEditing(false);
    if (await run("save", () => editNote(detail.id, draft))) setEditing(false);
  }

  const state = pub?.state ?? null;
  const prOpen = state === "open" || state === "publishing";
  const disabled = busy !== null;

  return (
    <div class="note-pane">
      <div class="note-toolbar" role="toolbar" aria-label="Note">
        {detail.can_publish && <PublicationChip publication={pub} />}
        {pub?.pr_url && (
          <a class="meta" href={pub.pr_url} target="_blank" rel="noopener noreferrer">
            PR #{pub.pr_number}
          </a>
        )}
        {detail.can_publish && pub?.changed && state !== "open" && (
          <span class="meta warning-text">Edited since it was published</span>
        )}
        <span class="spacer" />
        {detail.can_publish && (
          <>
            {(state === null || state === "closed" || state === "failed" || state === "skipped") && (
              <button class="btn btn-sm btn-accent" disabled={disabled} onClick={() => void act("publish")}>
                Publish
              </button>
            )}
            {state === "merged" && pub?.changed && (
              <button class="btn btn-sm btn-accent" disabled={disabled} onClick={() => void act("publish")}>
                Publish Changes
              </button>
            )}
            {state === "open" && pub?.pr_url && (
              <a class="btn btn-sm btn-link" href={`${pub.pr_url}/files`} target="_blank" rel="noopener noreferrer">
                Review
              </a>
            )}
            {state === "open" && (
              <>
                <button class="btn btn-sm btn-accent" disabled={disabled} onClick={merge}>
                  Merge
                </button>
                <button class="btn btn-sm" disabled={disabled} onClick={() => void act("close")}>
                  Close PR
                </button>
              </>
            )}
            {state === "skipped" ? (
              <button class="btn btn-sm btn-quiet" disabled={disabled} onClick={() => void act("unskip")}>
                Unskip
              </button>
            ) : (
              state !== "merged" && (
                <button
                  class="btn btn-sm btn-quiet"
                  disabled={disabled}
                  title="Never publish this note"
                  onClick={() => void act("skip")}
                >
                  Skip
                </button>
              )
            )}
          </>
        )}
        {note.markdown !== null && !editing && (
          <button
            class="btn btn-sm btn-quiet"
            disabled={disabled}
            onClick={() => {
              setDraft(note.markdown ?? "");
              setError(null);
              setEditing(true);
            }}
          >
            Edit
          </button>
        )}
        {detail.can_regenerate && !editing && (
          <button
            class="btn btn-sm btn-quiet"
            disabled={disabled || prOpen}
            title={prOpen ? "Close the pull request first" : "Write the note again from the transcript"}
            onClick={regenerate}
          >
            Regenerate
          </button>
        )}
      </div>
      {busy && (
        <p class="meta" role="status">
          {BUSY[busy]}
        </p>
      )}
      {error && (
        <p class="error small" role="alert">
          {error}
        </p>
      )}
      {detail.can_publish && state === "failed" && pub?.last_error && !error && (
        <p class="error small" role="alert">
          Publishing failed: {pub.last_error}
        </p>
      )}
      {detail.status === "failed" && (
        <p class="error small">Writing a new note failed. This is the previous one.</p>
      )}
      {editing ? (
        <form
          class="note-editor"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <textarea
            class="field note-editor-field"
            aria-label="Note Markdown"
            spellcheck
            value={draft}
            disabled={busy === "save"}
            onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
          />
          <p class="meta">
            Keep the <code>session_id</code> line in the frontmatter.
            {state === "open" && " Saving also updates the open pull request."}
          </p>
          <div class="inline-row">
            <button class="btn btn-sm btn-accent" type="submit" disabled={disabled}>
              Save
            </button>
            <button class="btn btn-sm btn-quiet" type="button" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : note.markdown !== null ? (
        <NoteView markdown={note.markdown} />
      ) : (
        <p class="muted">The note “{note.title}” is no longer available on the server.</p>
      )}
    </div>
  );
}
