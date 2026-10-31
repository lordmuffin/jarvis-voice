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
import {
  Banner,
  Bar,
  Block,
  Button,
  ButtonLink,
  Code,
  Dialog,
  Form,
  Reason,
  Section,
  Spacer,
  Text,
  Textarea,
} from "../ds/components";
import type { SessionDetail } from "../types";
import { NoteView } from "./NoteView";
import { actionsChecklist, noteBody } from "../copy";
import { CopyButton } from "../jarvis/CopyButton";
import { PublicationChip } from "../jarvis/PublicationChip";

const BUSY_WHY = "Another action is running.";
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

  const [confirming, setConfirming] = useState<"merge" | "regenerate" | null>(null);
  const merge = () => setConfirming("merge");
  const regenerate = () => setConfirming("regenerate");

  async function save() {
    if (draft === note.markdown) return setEditing(false);
    if (await run("save", () => editNote(detail.id, draft))) setEditing(false);
  }

  const state = pub?.state ?? null;
  const prOpen = state === "open" || state === "publishing";
  const disabled = busy !== null;

  return (
    <Section>
      <Block>
      <Bar role="toolbar" aria-label="Note">
        {detail.can_publish && <PublicationChip publication={pub} />}
        {pub?.pr_url && (
          <Text size="meta">
            <a href={pub.pr_url} target="_blank" rel="noopener noreferrer">
              PR #{pub.pr_number}
            </a>
          </Text>
        )}
        {detail.can_publish && pub?.changed && state !== "open" && (
          <Reason>⚑ Edited since it was published.</Reason>
        )}
        <Spacer />
        {detail.can_publish && (
          <>
            {(state === null || state === "closed" || state === "failed" || state === "skipped") && (
              <Button variant="primary" size="sm" disabled={disabled} title={disabled ? BUSY_WHY : undefined} onClick={() => void act("publish")}>
                Publish
              </Button>
            )}
            {state === "merged" && pub?.changed && (
              <Button variant="primary" size="sm" disabled={disabled} title={disabled ? BUSY_WHY : undefined} onClick={() => void act("publish")}>
                Publish Changes
              </Button>
            )}
            {state === "open" && pub?.pr_url && (
              <ButtonLink size="sm" href={`${pub.pr_url}/files`} target="_blank" rel="noopener noreferrer">
                Review
              </ButtonLink>
            )}
            {state === "open" && (
              <>
                <Button variant="primary" size="sm" disabled={disabled} title={disabled ? BUSY_WHY : undefined} onClick={merge}>
                  Merge
                </Button>
                <Button size="sm" disabled={disabled} title={disabled ? BUSY_WHY : undefined} onClick={() => void act("close")}>
                  Close PR
                </Button>
              </>
            )}
            {state === "skipped" ? (
              <Button variant="quiet" size="sm" disabled={disabled} title={disabled ? BUSY_WHY : undefined} onClick={() => void act("unskip")}>
                Unskip
              </Button>
            ) : (
              state !== "merged" && (
                <Button
                  variant="quiet"
                  size="sm"
                  disabled={disabled}
                  title={disabled ? BUSY_WHY : "Never publish this note"}
                  onClick={() => void act("skip")}
                >
                  Skip
                </Button>
              )
            )}
          </>
        )}
        {note.markdown !== null && !editing && (
          <CopyButton label="Copy Summary" text={() => noteBody(note.markdown ?? "")} />
        )}
        {detail.copilot.actions.length > 0 && !editing && (
          <CopyButton label="Copy Actions" text={() => actionsChecklist(detail.copilot.actions)} />
        )}
        {note.markdown !== null && !editing && (
          <Button
            variant="quiet"
            size="sm"
            disabled={disabled}
            title={disabled ? BUSY_WHY : undefined}
            onClick={() => {
              setDraft(note.markdown ?? "");
              setError(null);
              setEditing(true);
            }}
          >
            Edit
          </Button>
        )}
        {detail.can_regenerate && !editing && (
          <Button
            variant="quiet"
            size="sm"
            disabled={disabled || prOpen}
            title={
              prOpen
                ? "Close the pull request first."
                : disabled
                  ? BUSY_WHY
                  : "Write the note again from the transcript"
            }
            aria-describedby={prOpen ? "regenerate-why" : undefined}
            onClick={regenerate}
          >
            Regenerate
          </Button>
        )}
        {detail.can_regenerate && !editing && prOpen && (
          <Reason id="regenerate-why">⚑ Close the pull request first.</Reason>
        )}
      </Bar>
      </Block>
      {busy && (
        <Text as="p" size="meta" tone="muted" role="status">
          {BUSY[busy]}
        </Text>
      )}
      {error && <Banner variant="error">{error}</Banner>}
      {detail.can_publish && state === "failed" && pub?.last_error && !error && (
        <Banner variant="error">Publishing failed: {pub.last_error}</Banner>
      )}
      {detail.status === "failed" && (
        <Banner variant="warning">Writing a new note failed. This is the previous one.</Banner>
      )}
      {editing ? (
        <Form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <Textarea
            tall
            mono
            aria-label="Note Markdown"
            spellcheck
            value={draft}
            disabled={busy === "save"}
            title={busy === "save" ? BUSY_WHY : undefined}
            onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
          />
          <Text as="p" size="meta" tone="muted">
            Keep the <Code>session_id</Code> line in the frontmatter.
            {state === "open" && " Saving also updates the open pull request."}
          </Text>
          <Bar>
            <Button variant="primary" size="sm" type="submit" disabled={disabled} title={disabled ? BUSY_WHY : undefined}>
              Save
            </Button>
            <Button variant="quiet" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </Bar>
        </Form>
      ) : note.markdown !== null ? (
        <NoteView markdown={note.markdown} />
      ) : (
        <Text as="p" tone="muted">
          The note “{note.title}” is no longer available on the server.
        </Text>
      )}
      {confirming === "merge" && (
        <Dialog
          title={pub?.pr_number ? `Merge PR #${pub.pr_number}?` : "Merge this note?"}
          onClose={() => setConfirming(null)}
          actions={
            <>
              <Button onClick={() => setConfirming(null)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={() => {
                  setConfirming(null);
                  void act("merge");
                }}
              >
                Merge
              </Button>
            </>
          }
        >
          <Text tone="muted">Merge this note into the vault's main branch?</Text>
        </Dialog>
      )}
      {confirming === "regenerate" && (
        <Dialog
          eyebrow="⚑ Replaces your edits"
          title="Regenerate the note?"
          onClose={() => setConfirming(null)}
          actions={
            <>
              <Button onClick={() => setConfirming(null)}>Cancel</Button>
              <Button
                variant="danger"
                onClick={() => {
                  setConfirming(null);
                  void run("regenerate", () => regenerateNote(detail.id));
                }}
              >
                Regenerate
              </Button>
            </>
          }
        >
          <Text tone="muted">
            Write the note again from the transcript? The current note, edits included, is replaced.
          </Text>
        </Dialog>
      )}
    </Section>
  );
}
