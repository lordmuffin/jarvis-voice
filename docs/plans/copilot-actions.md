# Plan: copilot actions with explicit approval

Status: **approved 2026-10-30 with D1-D3 as recommended** (D4-D5 as recommended by default; D6 is a spike). Build in progress, see §7. Source: `Tasks.txt` item 4.

## Goal

The copilot already notices things mid-session. It should be able to **propose doing them**:
create a Work Tracker task, append to a vault note, schedule a Gotify reminder, comment on a
PR. Every proposal sits in a review queue and **does nothing until I approve it**. There is no
auto-run path, for any kind.

**Done when:** I finish a real session, approve a proposed task, and it appears in
`20 Areas/Personal/Work Tracker.md` with the session linked.

## 1. What the code does today (and what it forces)

1. **The vault is read-only to the server.** `vault/index.py` indexes a clone and never
   writes. The only vault write path is `publish/`: `GitHubClient` (bound to the single
   `publish_repo`, the Notes repo) creates a branch, `put_file`, opens a PR, and can merge.
   There are no direct pushes to `main`. So "append to a vault note" means *PR, then merge*, and
   the task is in `Work Tracker.md` on `main` only after that merge (see decision D2).
2. **"The existing capture API verbs" live in the legacy service**
   (`src/jarvis_voice/capture_api.py`, bugfix-only): `vault/note/append`, `notify/schedule`,
   `git/pr`. Those run on a desktop against a local `VAULT_ROOT` with an `X-Jarvis-Key`, keep
   state in `/tmp`, and shell out to `gh`. `jarvis_live` cannot reuse that code, and CLAUDE.md
   says new work goes in `server/`. There is **no task verb** and **no PR-comment verb**
   (`git/pr` only creates a PR). I reuse the *verb names and payload shapes* (`vault.append`
   {path, text}, `notify.schedule` {title, body, delay}), implemented on the server's own
   infrastructure. I do not call the legacy service. See D1.
3. **`notify/gotify.py` swallows every error** (`notify()` returns None). An executor cannot
   report a failed push through it. It needs a result-returning variant (§4.4).
4. **Gotify has no scheduling,** and the server has no scheduler (the legacy one is an
   in-memory dict in `/tmp`). A reminder needs a persisted schedule and a small fire loop.
5. **Name collisions to avoid.** `protocol.Action` / `CopilotDelta.actions_upsert` are
   *to-do bullets in the note*, not executable. Work items (`workitems/`) are *Claude Code
   prompts for repo changes*, with their own approve/dispatch gate. This plan's entity is called a
   **proposal** in code (`jarvis_live/actions/`, table `action_proposals`). A "Work Tracker task"
   is a personal/tracker item; a work item is a code change in an allowlisted repo. The copilot
   prompt must say which is which.
6. **The wire protocol is strict** (`extra="forbid"`; Mac and Android parse `copilot`). Adding
   proposals to the live WebSocket message is a `protocol/` change: schema, fixtures, server and
   Swift tests in the same PR. Work items avoided this by using REST. I propose the same for v1
   web, and a minimal protocol addition for macOS (D3).
7. **Work items only let the dashboard sign-in approve; a device token cannot** (the macOS
   app uses a device token). "One tap in macOS" conflicts with that rule (D3).
8. **Lab must never hold a vault write credential or publish** (Deploy CI enforces it).
   So on lab, vault and PR executors are *unavailable*, and the real "done when" test can only
   run on live (or in tests against `fake_github.py`). See §8.
9. **Work Tracker.md is a Kanban-plugin board, ~93 KB.** Tasks are
   `- [ ] text [[Link]] #priority/x 📅 YYYY-MM-DD` under `## Active / Backlog / Waiting /
   Needs Triage / Done`. The board says unclear captures go to **Needs Triage**.

## 2. Decisions I need from you

| # | Question | My recommendation |
|---|---|---|
| D1 | Reuse the legacy capture service, or reimplement the verbs server-side? | Reimplement on `PublishService`/`GitHubClient`/`Notifier`; keep the verb names and payload shapes. The legacy service has no auth model the server can use and no lab/live split. |
| D2 | A vault write is branch + PR. Should approval also **merge** it? Otherwise the task isn't on `main` until a second step, and "done when" isn't met. | Yes: approving a `task.create` / `vault.append` opens a PR **and squash-merges it**, only if the PR diff touches exactly the one approved path (checked before merge). The tap *is* the explicit consent. Setting `JARVIS_LIVE_ACTIONS_VAULT_MERGE` (default `true`); `false` stops at the open PR and reports "PR open". |
| D3 | May a **device token** (macOS) approve actions? Work items say no. | Yes for actions, but with safeguards: approval must carry the `payload_sha256` the client displayed (so it approves exactly what it saw), targets are allowlisted server-side, and every approval records `actor_kind=device`. Reason: vault append / reminder / PR comment are far lower-risk than dispatching code. If you'd rather keep the rule, macOS deep-links to the web review page and "one tap" becomes "tap, then tap". |
| D4 | Where does a proposed task land in Work Tracker? | `## Needs Triage` (the board's rule for unreviewed captures). Format: `- [ ] <text> [[<session note>]] [session](<web url>) 📅 <due> ^jv-<id8>`. |
| D5 | Where does the "Proposed actions" checklist go in the note? "Ends with" would mean after the transcript. | Immediately **before `## Transcript`**, so it stays visible. The checklist is a snapshot with a review link. Ticking boxes in Obsidian approves nothing. |
| D6 | PR comments need a repo client that isn't the Notes repo. | **Resolved (step 6):** the GitHub App (`dispatch/githubapp.py`) can comment: a per-comment token for one repo with only `pull_requests: write` + `metadata: read`. Only repos whose PRs live on GitHub qualify (dispatch allowlist minus `work_forgejo_repos`, whose token is read-only and whose GitHub copy is a mirror). Not yet exercised against real GitHub. Original question: I have **not** verified how the GitHub App token (`dispatch/githubapp.py`) or the Forgejo token could be reused for comments. First task in implementation is a spike to confirm. If it's not clean, `git.pr_comment` ships last or is cut from v1. |

## 3. Action schema

### 3.1 What the copilot emits

`CopilotDelta` gains `proposals: list[ProposalIn]` (tolerant like the rest: unknown keys
ignored, invalid entries dropped and counted, not fatal). One discriminated union on `kind`:

| kind | payload | maps to |
|---|---|---|
| `task.create` | `text`, `due?` (ISO date), `link?` (wikilink) | Work Tracker line, Needs Triage |
| `vault.append` | `path` (vault-relative), `text` | append to an **existing** note |
| `reminder.schedule` | `title`, `body?`, `at` (ISO datetime) | Gotify push at `at` |
| `git.pr_comment` | `repo` (allowlisted), `pr` (int), `body` | one PR comment |

Every proposal also carries `evidence`: the transcript line(s) (`start_ms` + quoted text) that
justify it. A proposal without evidence is dropped, so nothing exists without a transcript link.

The finalizer's full-transcript pass (`FinalNote`) may also emit `proposals`, merged and deduped
with the live ones. This covers sessions where the live copilot never ran, and the last minute
of a session. It matters for the "approve at finalize" flow.

### 3.2 Server-owned rules (`actions/state.py`, pure, unit-tested; same pattern as `CopilotState`)

- The server assigns ids (`p1`, `p2`…). The LLM never supplies identity.
- **Dedupe key** `sha256(session_id | kind | canonical payload)`, with a unique index. A restated
  proposal is a no-op. Re-proposing something already rejected, executed or expired is dropped
  (the model sees closed proposals as "do not propose again", like work items).
- **Validation before it can be shown:** `vault.append` path must exist in the index, not be
  excluded (`is_excluded`: no `40 Archives`, `.obsidian`…), and `.md`. `repo` must be on the
  allowlist. `reminder.at` must be in the future and at most 90 days out, in `settings.timezone`
  if naive. Text is length-capped and stripped of control characters; `task.create` text is
  collapsed to one line so a proposal cannot inject Markdown structure into the board.
- **Caps:** at most 3 new proposals per cycle, 15 open per session.
- **Eligibility:** not `local_only`; owner or admin sessions only (`may_publish_for`). Other
  users' sessions get no proposals, since the vault, Gotify and repos are the owner's.
- **Prompt rule (the main lever on precision):** propose only on explicit intent in the
  transcript ("remind me…", "add a task…", "note that in X"), never on mere topic mentions.
  Ideas that are code changes stay with the ideas pass / work items.

## 4. Lifecycle, approval and execution

### 4.1 Data (Alembic `0010_action_proposals`)

`action_proposals`: `id`, `session_id`, `user_id`, `ref`, `position`, `kind`, `payload` JSONB,
`payload_sha256`, `dedupe_key` (unique with `session_id`), `evidence` JSONB, `state`,
`proposed_by` (`copilot` | `finalizer`, plus model name), `approved_by`, `approved_at`,
`result` JSONB, `executed_at`, `version`, timestamps.

`action_proposal_events`: append-only, same shape as `work_item_events`: `id`, `proposal_id`,
`at`, `actor_user_id` (NULL = system/LLM), `actor_kind` (`web|device|system`), `event`,
`payload`. Events: `proposed`, `edited`, `approved`, `rejected`, `expired`, `exec_started`,
`exec_succeeded`, `exec_failed`, `retry_requested`, `reconciled`. Payload carries what was
approved (hash), the result (commit sha, PR URL, schedule id, comment URL) or the error text.
That answers "who proposed, who approved, when, what happened".

`scheduled_reminders`: `id`, `proposal_id` (unique), `fire_at`, `title`, `body`, `state`
(`pending|sent|failed|cancelled`), `sent_at`, `error`.

### 4.2 States

```
proposed ──approve──▶ approved ──▶ executing ──▶ succeeded
   │  ▲edit(resets hash)               └──────▶ failed ──explicit "retry"──▶ approved
   ├──reject──▶ rejected
   └──session ends + TTL, or removed by the model──▶ expired
```

- **Approve** is a compare-and-set (`UPDATE … WHERE state='proposed' AND payload_sha256=:seen`).
  Re-approving an approved/succeeded proposal returns its current state and writes nothing.
  Approving a proposal edited since the client rendered it is refused (hash mismatch): you only
  ever approve what you saw.
- **Edit** (text, due, `at`, body) is allowed in `proposed`, recomputes the hash and logs the
  before/after. Kind and target are not editable; reject and re-propose instead.
- Proposals survive the session ending; they expire after `actions_ttl_days` (default 14),
  with an `expired` event. They never execute on expiry.
- **There is no auto-approve code path.** No setting, no per-kind flag. A test asserts that no
  executor is reachable except from the approve endpoint.

### 4.3 Execution and idempotency

- Executed by `ActionService` under the **per-session lock** (the `PublishService.lock`
  pattern; single replica). Approving a batch executes in `position` order, **one at a time, and
  continues past a failure** so one bad PR comment doesn't block a task. Each result is recorded.
  Approval returns 202; the UI polls `version` like work items.
- **Exactly-once effects come from three layers:**
  1. State CAS: only one caller moves `approved → executing`.
  2. Deterministic external identity, derived from the proposal id: branch
     `jarvis-live/action-<id8>`, a block id `^jv-<id8>` on the Tracker line, an HTML comment
     `<!-- jarvis-action:<id> -->` in a PR comment, and a unique `proposal_id` on the reminder row.
  3. **Check-before-write in every executor:** vault actions read the file on `main` and the
     branch and skip if the marker is already there. The PR comment lists comments and skips if
     the marker is present. The reminder insert is `ON CONFLICT DO NOTHING`.
- **Crash during `executing`:** on startup (like `recover_unfinalized`) such rows get a
  read-only reconcile: marker found on `main`/PR/comment → `succeeded` (`reconciled` event);
  not found → `failed: "interrupted, outcome unknown"`. **Nothing is re-written automatically.**
- **Failure is reported, not retried.** A failed proposal shows the error and a **Retry** button.
  Retry is a new explicit approval (`retry_requested`, actor recorded). There is no backoff loop
  and no silent retry. The marker check makes a manual retry safe even if the first attempt
  actually landed.

### 4.4 Executors

- **`task.create` / `vault.append`:** read `Work Tracker.md` (or the target) from `main` via
  the contents API; insert after the last line of `## Needs Triage`'s list (or append for
  `vault.append`); create `jarvis-live/action-<id8>` from the current `main` head; `put_file`
  with the blob sha; open PR; if `actions_vault_merge`, verify the PR changes exactly that one path, then
  squash-merge. Because every action branches from a fresh `main`, ordered batch items can't
  conflict with each other; a conflict with a concurrent Obsidian-git push fails with a clear
  message and is retryable. `PUBLISH_ALL_GAP_S` spacing is reused for GitHub's secondary rate
  limit. **The session link:** wikilink to the session note's stem when `latest_final_note`
  exists, plus the web session URL (`session_url`) always, since a mid-session approval can
  precede the note.
  Requires `publish_enabled`. Unavailable on lab.
- **`reminder.schedule`:** insert a `scheduled_reminders` row. A `ReminderLoop` (like
  `prwatch.run_loop`) sends due rows through a new `Notifier.send()` that **returns/raises**
  instead of swallowing; success → `sent`; failure → `failed` and the proposal result is updated
  and surfaced. A missed fire (server down past `fire_at`) sends late and says so in the
  message, once. Owner only.
- **`git.pr_comment`:** per D6; allowlist only.
- The Gotify "note ready" path keeps the swallowing `notify()`; only reminders use `send()`.

### 4.5 Batch approve at finalize

`render_note` adds `## Proposed actions` before the transcript:

```
## Proposed actions
- [ ] Task: Call the vendor about the quote (due 2026-11-03) <!-- action:p2 -->
- [ ] Reminder: 2026-11-02 09:00, "Send invoice"
- [ ] Append to [[Sleep Research]]: "…"
[Review and approve](https://…/sessions/<id>/actions)
```

Statuses after the batch (✅ / ❌ with reason) live on the review page. The note is a snapshot;
regenerating it picks up statuses, the same contract as work items. The review page has
**Approve all pending** and per-item **Approve / Edit / Reject**. The notification "note ready"
gains the count ("3 actions waiting").

## 5. API and clients

REST, owner-scoped (404 for anyone else), under `/v1`: `GET /sessions/{id}/actions`,
`POST /sessions/{id}/actions/approve` (`[{id, payload_sha256}]`, empty = reject nothing), `POST …/{pid}/reject`,
`PATCH …/{pid}`, `POST …/{pid}/retry`, `GET …/{pid}/events`. A cross-session
`GET /v1/actions?state=proposed` for a "waiting on you" list.

- **Web:** a panel on the live session view and a review page, built from Instrument tokens and
  components only (design lint applies). Poll `version`, matching `workitems.ts`. If a component
  is missing, propose it to lordmuffin/instrument rather than build one locally.
- **macOS:** the Copilot panel needs to *see* live proposals, which means a `protocol/` change
  (**D3**): add a `proposals` list to `copilot` (kinds above, `payload_sha256`, state) with schema
  + valid/invalid fixtures + `JarvisLiveKit` and server tests in the same PR. Approval goes
  over REST with the device token. Android and Wear ignore the new field (verify their parsers
  tolerate it before merge).
- A `dispatch_refused`-style reason is shown whenever approval is blocked (no publish
  credential, outside allowlist, stale hash).

## 6. Eval: proposal precision (report only)

"Precision" = of the proposals the copilot makes, the share you would accept.

- **Offline harness** (`tests/evals/test_action_evals.py`, `--live`, so it needs the real
  endpoint). It replays recorded transcripts through the same cycle code as `harness.py` and
  collects proposals.
- **Labels are yours.** For each (session, kind, normalized text hash) the eval reads
  `tests/evals/action_labels.jsonl` (`accept | accept_edited | reject`). Unlabelled proposals
  are written to `server/.eval-actions-unlabelled.jsonl` for you to mark; reruns only ask about
  new ones. No LLM judge: it would measure the model agreeing with itself.
- **Report** (`server/.eval-actions-report.json` plus a printed table): proposals made, labelled
  count, **precision** (accept + accept_edited) / labelled, **strict precision** (accept only),
  per-kind precision, and proposals per session-hour. Recall is not measured (it needs you to
  list what was missed); I'll add an optional `missed` label type if you want it.
- **Production signal:** the same numbers from real review decisions, via `jarvis-live
  actions-report` over `action_proposals.state` (approved/rejected/expired, edit counts).
  This is the number that matters long term; the offline set is for model and prompt changes.
- **Not gated.** No threshold assertion, and it's excluded from `make ratchet` (the ratchet counts
  collected eval cases; I'll mark these as non-ratcheted rather than bump `.eval-baseline`
  without asking).
- **Caveat:** the existing `tests/evals/transcripts.py` cases are scripted. "Recorded sessions"
  means real ones, so I need you to point me at which recordings I may use (they hold private
  content; the repo is public, so labels and transcripts stay out of git, and the committed
  test uses a tiny synthetic fixture to exercise the harness).

## 7. Build order (TDD; one PR per step, Conventional Commits)

1. `feat(server)`: schema + `ProposalState` (pure): dedupe, validation, caps, hash. Tests first.
2. `feat(server)`: migration `0010`, `ActionService` (approve CAS, edit, reject, expire, events),
   REST. Fake executors. Tests include the audit trail and the no-auto-run test.
3. `feat(server)`: copilot + finalizer emit proposals (prompts, `CopilotDelta`/`FinalNote`,
   golden `final_note.md` update, `## Proposed actions` rendering).
4. **Done** (`actions/vault.py`). `feat(server)`: `task.create`/`vault.append` executor on `fake_github`: marker idempotency,
   crash-reconcile, conflict, merge-diff guard. **First time "done when" can be checked locally.**
5. **Done** (`actions/reminders.py`). `feat(server)`: `Notifier.send()`, `scheduled_reminders`, `ReminderLoop`.
6. **Done** (`actions/comments.py`). D6 spike, then `git.pr_comment`.
7. `feat(web)`: panel + review page (+ Playwright).
8. `feat(protocol)` + `feat(macos)`: `proposals` in `copilot`, fixtures, Swift tests, panel UI.
9. Eval harness + `actions-report`.

Steps 1–6 and 9 are server-only. Manifest changes (settings, the reminder loop) ship in
`server/deploy/k8s/base/` in the same PR as the code that needs them, per CLAUDE.md.

## 8. Verifying "done when"

- **In tests:** step 4 asserts the Tracker line (with marker, session wikilink and URL) lands under
  `## Needs Triage` in the file `fake_github` serves, exactly once across double-approve and
  retry.
- **For real:** only on **live** (lab has no vault credential, by design). Procedure: record a
  short real session on live, say "add a task to …", end it, approve in the web UI, and confirm the
  line on `main` and in Obsidian after git-sync. I'll write this as a checklist in the PR; I can't
  run it myself.
- Deploy CI gets a new check that lab still has no vault credential and that the new settings don't
  introduce one.

## 9. Risks

- **Merging on approval writes to your real vault.** Mitigations: one-path diff guard, deterministic
  markers, the audit trail, and the setting to stop at an open PR (D2).
- **Obsidian-git / Kanban plugin local edits** can conflict with a PR merged to `main`. This
  already exists for published notes; the failure is reported, not hidden.
- **A noisy model** erodes trust in the queue. Caps, explicit-intent prompting and the precision
  report are the controls. If precision is poor, the answer is a worse-but-quieter prompt, not auto-run.
- **Private transcripts** feeding evals (§6).

## 10. Out of scope

Auto-approval of any kind; executing from Markdown checkbox edits; calendar events and email
actions; code changes in repos (work items); regenerating the published note automatically after
execution; recall measurement.
