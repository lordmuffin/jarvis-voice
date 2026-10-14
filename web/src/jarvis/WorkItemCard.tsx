import { useState } from "preact/hooks";
import {
  ApiError,
  cancelDispatch,
  continueDispatch,
  dispatchWorkItem,
  editWorkItem,
  workItemAction,
  workItemCopied,
  workItemPrompt,
  type WorkItemAction,
} from "../api";
import {
  Banner,
  Bar,
  Button,
  Choice,
  Code,
  CodeBlock,
  Field,
  Form,
  Input,
  Pill,
  type PillTone,
  Quote,
  RunBox,
  RunHead,
  Select,
  SourceLink,
  Stack,
  Text,
  Textarea,
  WorkCard,
} from "../ds/components";
import { clock } from "../format";
import { navigate } from "../store";
import type { WorkItem, WorkItemEdit, WorkItemPrompt, WorkRepo } from "../types";
import {
  DISPATCH_LABEL,
  STATE_LABEL,
  copyText,
  parseCriteria,
  runActive,
  transcriptHref,
} from "../workitems";
import { Link } from "../views/Link";

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

const STATE_TONE: Record<WorkItem["state"], PillTone> = {
  draft: "neutral",
  approved: "live",
  discarded: "neutral",
  merged: "done",
};

type Busy = WorkItemAction | "save" | "copy" | "prompt" | "run" | "cancel" | "steer" | null;

export function WorkItemCard({
  item,
  repos,
  dispatchEnabled = false,
  showSession,
  picked,
  onPick,
  onChanged,
}: {
  item: WorkItem;
  repos: WorkRepo[];
  dispatchEnabled?: boolean;
  showSession: boolean;
  picked: boolean;
  onPick?: (on: boolean) => void;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState<WorkItemPrompt | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // The owner's open/close of the reply box, for one run; otherwise it is open while that run
  // waits on the owner.
  const [steering, setSteering] = useState<{ run: string; open: boolean } | null>(null);

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

  async function act(action: WorkItemAction) {
    if (action === "discard" && !confirm(`Discard “${item.title}”?`)) return;
    if (await run(action, () => workItemAction(item.id, action), "That didn't work.")) {
      setPrompt(null);
      onChanged();
    }
  }

  async function runIt() {
    const repo = item.repo ?? "its repository";
    if (!confirm(`Run “${item.title}” as Claude Code on ${repo}? It opens a draft PR.`)) return;
    if (await run("run", () => dispatchWorkItem(item.id), "Couldn't start the run.")) onChanged();
  }

  async function steer(guidance: string) {
    if (await run("steer", () => continueDispatch(item.id, guidance), "Couldn't start the follow-up run.")) {
      onChanged();
    }
  }

  async function cancelRun() {
    if (await run("cancel", () => cancelDispatch(item.id), "Couldn't cancel the run.")) onChanged();
  }

  async function togglePrompt() {
    if (prompt) return setPrompt(null);
    await run("prompt", async () => setPrompt(await workItemPrompt(item.id)), "Couldn't load the prompt.");
  }

  async function copy() {
    await run(
      "copy",
      async () => {
        const p = await workItemPrompt(item.id);
        if (!(await copyText(p.markdown))) throw new Error("copy failed");
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
        await workItemCopied(item.id);
      },
      "Couldn't copy the prompt.",
    );
  }

  const open = item.state === "draft" || item.state === "approved";
  const canSteer = dispatchEnabled && item.state === "approved" && !!item.dispatch && !runActive(item);
  const chosen = steering && steering.run === item.dispatch?.id ? steering.open : null;
  const steerOpen = canSteer && (chosen ?? item.dispatch?.state === "needs_input");
  const setSteer = (open: boolean) => item.dispatch && setSteering({ run: item.dispatch.id, open });
  return (
    <WorkCard state={item.state} data-testid="work-item" data-state={item.state}>
      <div class="d-wi-head">
        <Pill tone={STATE_TONE[item.state]}>{STATE_LABEL[item.state]}</Pill>
        <h3 data-testid="work-item-title">{item.title}</h3>
        {onPick && (
          <Choice
            data-testid="work-item-pick"
            checked={picked}
            aria-label={`Select “${item.title}” to merge`}
            label="Merge"
            onChange={(e) => onPick((e.target as HTMLInputElement).checked)}
          />
        )}
      </div>
      {showSession && (
        <Text size="meta" tone="muted">
          <Link href={`/sessions/${encodeURIComponent(item.session_id)}`}>
            {item.session_title ?? "Untitled session"}
          </Link>
        </Text>
      )}

      {editing ? (
        <WorkItemEditor
          item={item}
          repos={repos}
          busy={busy === "save"}
          onCancel={() => setEditing(false)}
          onSave={async (fields) => {
            if (await run("save", () => editWorkItem(item.id, fields), "Couldn't save.")) {
              setEditing(false);
              setPrompt(null);
              onChanged();
            }
          }}
        />
      ) : (
        <>
          <dl>
            <dt>Repository</dt>
            <dd class={item.repo ? "d-dl-mono" : "d-none"}>{item.repo ?? "no repository yet"}</dd>
            {item.intent && (
              <>
                <dt>Intent</dt>
                <dd class="d-pre">{item.intent}</dd>
              </>
            )}
            <dt>Acceptance criteria</dt>
            <dd>
              {item.acceptance_criteria.length > 0 ? (
                <ul data-testid="work-item-criteria">
                  {item.acceptance_criteria.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              ) : (
                <span class="d-tone-muted">—</span>
              )}
            </dd>
            {item.done_when && (
              <>
                <dt>Done when</dt>
                <dd>{item.done_when}</dd>
              </>
            )}
            {item.sources.length > 0 && (
              <>
                <dt>Source</dt>
                <dd data-testid="work-item-sources" aria-label="Where it was said">
                  {item.sources.map((s) =>
                    showSession ? (
                      <SourceLink
                        key={s.segment_id}
                        href={transcriptHref(item.session_id, s.start_ms)}
                        title={s.text}
                        onClick={(e) => {
                          if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                          e.preventDefault();
                          navigate(transcriptHref(item.session_id, s.start_ms));
                        }}
                      >
                        ▶ {clock(s.start_ms)}
                      </SourceLink>
                    ) : (
                      // On the session's own page: a hash change scrolls its transcript there.
                      <SourceLink key={s.segment_id} href={`#t=${s.start_ms}`} title={s.text}>
                        ▶ {clock(s.start_ms)}
                      </SourceLink>
                    ),
                  )}
                </dd>
              </>
            )}
          </dl>
          {item.state === "draft" && item.problems.length > 0 && (
            <Text as="p" size="caption" tone="muted" data-testid="work-item-problems">
              To approve: {item.problems.join("; ")}.
            </Text>
          )}
        </>
      )}

      {item.dispatch && <RunBlock run={item.dispatch} />}
      {steerOpen && item.dispatch && !editing && (
        <SteerForm
          run={item.dispatch}
          busy={busy === "steer"}
          onSend={(guidance) => void steer(guidance)}
          onCancel={() => setSteer(false)}
        />
      )}

      {!editing && (
        <Bar>
          {dispatchEnabled && item.state === "approved" && !runActive(item) && (
            <Button variant="primary" size="sm" disabled={busy !== null} onClick={() => void runIt()}>
              {busy === "run" ? "Starting…" : item.dispatch ? "▶ Run Again" : "▶ Run in Claude Code"}
            </Button>
          )}
          {canSteer && !steerOpen && (
            <Button variant="primary" size="sm" disabled={busy !== null} onClick={() => setSteer(true)}>
              ↪ Steer Next Run
            </Button>
          )}
          {runActive(item) && (
            <Button size="sm" disabled={busy !== null} onClick={() => void cancelRun()}>
              {busy === "cancel" ? "Cancelling…" : "■ Cancel Run"}
            </Button>
          )}
          {item.state === "approved" && (
            <Button variant="primary" size="sm" disabled={busy !== null} onClick={() => void copy()}>
              {copied ? "✓ Copied" : busy === "copy" ? "Copying…" : "⎘ Copy Prompt"}
            </Button>
          )}
          {item.state === "draft" && (
            <Button
              variant="primary"
              size="sm"
              disabled={busy !== null || item.problems.length > 0}
              title={item.problems.length ? item.problems.join("; ") : undefined}
              onClick={() => void act("approve")}
            >
              {busy === "approve" ? "Approving…" : "✓ Approve"}
            </Button>
          )}
          {open && (
            <>
              <Button size="sm" disabled={busy !== null} onClick={() => void togglePrompt()}>
                {prompt ? "Hide Prompt" : item.state === "approved" ? "Show Prompt" : "Preview Prompt"}
              </Button>
              <Button size="sm" disabled={busy !== null} onClick={() => setEditing(true)}>
                Edit
              </Button>
            </>
          )}
          {item.state === "approved" && (
            <Button variant="quiet" size="sm" disabled={busy !== null} onClick={() => void act("unapprove")}>
              Unapprove
            </Button>
          )}
          {open && (
            <Button variant="quiet" size="sm" disabled={busy !== null} onClick={() => void act("discard")}>
              ✕ Discard
            </Button>
          )}
          {item.state === "discarded" && (
            <Button size="sm" disabled={busy !== null} onClick={() => void act("restore")}>
              ↩ Restore
            </Button>
          )}
        </Bar>
      )}

      {error && <Banner variant="error">{error}</Banner>}
      {prompt && (
        <Stack>
          <Text size="meta" tone="muted">
            {prompt.frozen
              ? `Approved prompt · sha256 ${prompt.sha256?.slice(0, 12)}`
              : "Preview. Approving freezes the prompt exactly as shown."}
          </Text>
          <CodeBlock data-testid="work-item-prompt">{prompt.markdown}</CodeBlock>
        </Stack>
      )}
    </WorkCard>
  );
}

/** The latest run: where it stands, its PR, or why it ended. */
export function RunBlock({ run }: { run: NonNullable<WorkItem["dispatch"]> }) {
  const bad = ["failed", "timed_out", "rate_limited"].includes(run.state);
  return (
    <RunBox data-testid="dispatch">
      <RunHead>
        <span>
          Claude Code{run.attempt > 1 ? ` · attempt ${run.attempt}` : ""} ·{" "}
          <strong>{DISPATCH_LABEL[run.state]}</strong>
          {run.pr_url && (
            <>
              {" · "}
              <a href={run.pr_url} target="_blank" rel="noreferrer">
                Draft PR #{run.pr_number}
              </a>
            </>
          )}
        </span>
      </RunHead>
      {run.guidance && (
        <Text as="p" size="caption">
          <Text tone="muted">You: </Text>
          {run.guidance}
        </Text>
      )}
      {run.summary && (
        <Text as="p" size="caption">
          {run.summary}
        </Text>
      )}
      {run.questions && (
        <Quote data-testid="dispatch-questions">
          <Text size="meta" tone="muted">
            Claude asks
          </Text>
          <Text as="p" size="caption">
            {run.questions}
          </Text>
        </Quote>
      )}
      {bad && run.error && (
        <Text as="p" size="caption" tone="error">
          {run.error}
        </Text>
      )}
    </RunBox>
  );
}

/** The owner's reply to a finished run: a follow-up run continues its branch with it. */
function SteerForm({
  run,
  busy,
  onSend,
  onCancel,
}: {
  run: NonNullable<WorkItem["dispatch"]>;
  busy: boolean;
  onSend: (guidance: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const guidance = text.trim();
  const id = `steer-${run.id}`;
  return (
    <Form
      data-testid="steer"
      onSubmit={(e) => {
        e.preventDefault();
        if (guidance) onSend(guidance);
      }}
    >
      <Field label={run.state === "needs_input" ? "Answer Claude" : "Steer the next run"} htmlFor={id}>
        <Textarea
          id={id}
          rows={3}
          maxLength={4000}
          placeholder={
            run.state === "needs_input"
              ? "Answer its questions, or tell it what to do instead…"
              : "What should it fix, try or leave alone next time?"
          }
          value={text}
          onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
        />
      </Field>
      <Text as="p" size="caption" tone="muted">
        A follow-up run continues on <Code>{run.branch}</Code>
        {run.pr_number ? ` and draft PR #${run.pr_number}` : ""}, with your reply added to the approved prompt.
      </Text>
      <Bar>
        <Button variant="primary" size="sm" type="submit" disabled={busy || !guidance}>
          {busy ? "Starting…" : "↪ Continue"}
        </Button>
        <Button variant="quiet" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </Bar>
    </Form>
  );
}

function WorkItemEditor({
  item,
  repos,
  busy,
  onSave,
  onCancel,
}: {
  item: WorkItem;
  repos: WorkRepo[];
  busy: boolean;
  onSave: (fields: WorkItemEdit) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [repo, setRepo] = useState(item.repo ?? "");
  const [intent, setIntent] = useState(item.intent);
  const [criteria, setCriteria] = useState(item.acceptance_criteria.join("\n"));
  const [doneWhen, setDoneWhen] = useState(item.done_when ?? "");

  function save(e: Event) {
    e.preventDefault();
    const fields: WorkItemEdit = {};
    if (title.trim() !== item.title) fields.title = title;
    if ((repo || null) !== item.repo) fields.repo = repo || null;
    if (intent.trim() !== item.intent) fields.intent = intent;
    const parsed = parseCriteria(criteria);
    if (parsed.join("\n") !== item.acceptance_criteria.join("\n")) fields.acceptance_criteria = parsed;
    if ((doneWhen.trim() || null) !== item.done_when) fields.done_when = doneWhen.trim() || null;
    if (Object.keys(fields).length === 0) return onCancel();
    onSave(fields);
  }

  const id = (f: string) => `wi-${item.id}-${f}`;
  return (
    <Form onSubmit={save}>
      <Field label="Title" htmlFor={id("title")}>
        <Input id={id("title")} value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
      </Field>
      <Field label="Repository" htmlFor={id("repo")}>
        <Select id={id("repo")} value={repo} onChange={(e) => setRepo((e.target as HTMLSelectElement).value)}>
          <option value="">Choose a repository…</option>
          {repos.map((r) => (
            <option key={r.name} value={r.name}>
              {r.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Intent" htmlFor={id("intent")}>
        <Textarea
          id={id("intent")}
          rows={4}
          value={intent}
          onInput={(e) => setIntent((e.target as HTMLTextAreaElement).value)}
        />
      </Field>
      <Field label="Acceptance criteria (one per line)" htmlFor={id("criteria")}>
        <Textarea
          id={id("criteria")}
          rows={4}
          value={criteria}
          onInput={(e) => setCriteria((e.target as HTMLTextAreaElement).value)}
        />
      </Field>
      <Field label="Done when" htmlFor={id("done")}>
        <Input id={id("done")} value={doneWhen} onInput={(e) => setDoneWhen((e.target as HTMLInputElement).value)} />
      </Field>
      <Bar>
        <Button variant="primary" size="sm" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button variant="quiet" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </Bar>
      {item.state === "approved" && (
        <Text as="p" size="caption" tone="muted">
          Saving puts it back to draft: approve it again to freeze the new prompt.
        </Text>
      )}
    </Form>
  );
}
