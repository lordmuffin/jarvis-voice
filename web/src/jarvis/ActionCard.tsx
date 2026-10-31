import { useState } from "preact/hooks";
import { ApiError, actionEvents, approveActions, editAction, rejectAction, retryAction } from "../api";
import {
  KIND_LABEL,
  STATE_LABEL,
  describeAction,
  editFields,
  eventText,
  outcomeNote,
  resultLine,
} from "../actions";
import {
  Banner,
  Bar,
  Button,
  Field,
  Form,
  Input,
  Pill,
  type PillTone,
  Quote,
  SourceLink,
  Stack,
  Text,
  Textarea,
  WorkCard,
} from "../ds/components";
import { clock, when } from "../format";
import type { ActionEvent, ActionItem } from "../types";

const BUSY_WHY = "Another action is running.";

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

const STATE_TONE: Record<ActionItem["state"], PillTone> = {
  proposed: "neutral",
  approved: "progress",
  executing: "progress",
  succeeded: "done",
  failed: "failed",
  rejected: "neutral",
  expired: "neutral",
};

/** WorkCard's chrome keys off a work item's state: borrow the closest one. */
const CARD_STATE: Record<ActionItem["state"], "draft" | "approved" | "discarded" | "done"> = {
  proposed: "draft",
  approved: "approved",
  executing: "approved",
  succeeded: "done",
  failed: "draft",
  rejected: "discarded",
  expired: "discarded",
};

type Busy = "approve" | "reject" | "retry" | "save" | "history" | null;

/**
 * One proposed action. Approving is one tap and is bound to the content shown here (its
 * `payload_sha256`), so an action edited elsewhere in the meantime is refused, not approved
 * blind.
 */
export function ActionCard({ item, onChanged }: { item: ActionItem; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<ActionEvent[] | null>(null);

  async function run(kind: Busy, fn: () => Promise<unknown>, fallback: string) {
    setBusy(kind);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(errorText(e, fallback));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function approve() {
    let note: string | null = null;
    const ok = await run(
      "approve",
      async () => {
        const res = await approveActions(item.session_id, [{ id: item.id, payload_sha256: item.payload_sha256 }]);
        note = outcomeNote(res.results[0]?.outcome ?? "not_found");
      },
      "Couldn't approve that.",
    );
    if (ok) {
      if (note) setError(note);
      onChanged();
    }
  }

  async function toggleHistory() {
    if (events) return setEvents(null);
    await run("history", async () => setEvents((await actionEvents(item.id)).events), "Couldn't load the history.");
  }

  const describe = describeAction(item);
  const result = resultLine(item);
  const proposed = item.state === "proposed";
  const why = busy !== null ? BUSY_WHY : undefined;
  return (
    <WorkCard state={CARD_STATE[item.state]} data-testid="action" data-state={item.state}>
      <div class="d-wi-head">
        <Pill tone={STATE_TONE[item.state]}>{STATE_LABEL[item.state]}</Pill>
        <Pill tag>{KIND_LABEL[item.kind]}</Pill>
        <h3 data-testid="action-title">{describe.title}</h3>
      </div>
      {editing ? (
        <ActionEditor
          item={item}
          busy={busy === "save"}
          onCancel={() => setEditing(false)}
          onSave={async (changes) => {
            if (await run("save", () => editAction(item.id, changes), "Couldn't save.")) {
              setEditing(false);
              onChanged();
            }
          }}
        />
      ) : (
        <>
          {describe.detail && (
            <Text as="p" size="caption" tone="muted">
              {describe.detail}
            </Text>
          )}
          {item.evidence.length > 0 && (
            <Quote data-testid="action-evidence">
              {item.evidence.map((e) => (
                <SourceLink key={e.start_ms} href={`#t=${e.start_ms}`} title={e.text}>
                  ▶ {clock(e.start_ms)}
                </SourceLink>
              ))}
            </Quote>
          )}
          <Text size="meta" tone="muted">
            Proposed by {item.proposed_by} · {when(item.created_at)}
          </Text>
        </>
      )}
      {result && (
        <Text as="p" size="caption" tone={result.tone} data-testid="action-result">
          {result.text}
          {result.href && (
            <>
              {" · "}
              <a href={result.href} target="_blank" rel="noreferrer">
                {result.linkText}
              </a>
            </>
          )}
        </Text>
      )}
      {!editing && (
        <Bar>
          {proposed && (
            <Button variant="primary" size="sm" disabled={busy !== null} title={why} onClick={() => void approve()}>
              {busy === "approve" ? "Approving…" : "✓ Approve"}
            </Button>
          )}
          {proposed && (
            <Button size="sm" disabled={busy !== null} title={why} onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
          {proposed && (
            <Button
              variant="quiet"
              size="sm"
              disabled={busy !== null}
              title={why}
              onClick={async () => {
                if (await run("reject", () => rejectAction(item.id), "Couldn't reject that.")) onChanged();
              }}
            >
              ✕ Reject
            </Button>
          )}
          {item.state === "failed" && (
            <Button
              variant="primary"
              size="sm"
              disabled={busy !== null}
              title={why}
              onClick={async () => {
                if (await run("retry", () => retryAction(item.id), "Couldn't retry that.")) onChanged();
              }}
            >
              ↻ Retry
            </Button>
          )}
          <Button variant="quiet" size="sm" disabled={busy === "history"} onClick={() => void toggleHistory()}>
            {events ? "Hide History" : "History"}
          </Button>
        </Bar>
      )}
      {error && <Banner variant="error">{error}</Banner>}
      {events && (
        <ul data-testid="action-history">
          {events.map((e, i) => (
            <li key={i}>
              {eventText(e)}{" "}
              <Text size="meta" tone="muted">
                {when(e.at)}
              </Text>
            </li>
          ))}
        </ul>
      )}
    </WorkCard>
  );
}

function ActionEditor({
  item,
  busy,
  onSave,
  onCancel,
}: {
  item: ActionItem;
  busy: boolean;
  onSave: (changes: Record<string, string | null>) => void;
  onCancel: () => void;
}) {
  const fields = editFields(item.kind);
  const initial = (key: string) => (item.payload[key] == null ? "" : String(item.payload[key]));
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, initial(f.key)])),
  );

  function save(e: Event) {
    e.preventDefault();
    const changes: Record<string, string | null> = {};
    for (const f of fields) {
      const v = values[f.key]!.trim();
      if (v !== initial(f.key)) changes[f.key] = v || null;
    }
    if (Object.keys(changes).length === 0) return onCancel();
    onSave(changes);
  }

  const id = (k: string) => `action-${item.id}-${k}`;
  return (
    <Form onSubmit={save}>
      <Stack>
        {fields.map((f) => (
          <Field key={f.key} label={f.label} htmlFor={id(f.key)}>
            {f.multiline ? (
              <Textarea
                id={id(f.key)}
                rows={3}
                value={values[f.key]}
                onInput={(e) => setValues({ ...values, [f.key]: (e.target as HTMLTextAreaElement).value })}
              />
            ) : (
              <Input
                id={id(f.key)}
                value={values[f.key]}
                onInput={(e) => setValues({ ...values, [f.key]: (e.target as HTMLInputElement).value })}
              />
            )}
          </Field>
        ))}
      </Stack>
      <Text as="p" size="caption" tone="muted">
        Editing changes what you approve. To change the time or the target, reject this and ask again.
      </Text>
      <Bar>
        <Button variant="primary" size="sm" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button variant="quiet" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </Bar>
    </Form>
  );
}
