# Jarvis Live server

FastAPI backend for Jarvis Live: it ingests audio from the apps, transcribes it, runs the live copilot and writes a final note. The server also serves the web dashboard at `/`.

Settings are environment variables prefixed `JARVIS_LIVE_`; see `src/jarvis_live/config.py`. To run the checks:

```sh
uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

## Users and sign-in

Every session, device and note belongs to a user. Users sign in with **Pocket-ID** (OpenID Connect).

- **Seeing sessions:** each user sees only their own sessions. Admins can also list everyone's: the dashboard has an "All users" toggle.
- **Web dashboard:** after Pocket-ID, it keeps a 30-day session cookie.
- **Android app:** "Sign in with Pocket-ID" in Settings gives the phone its own device token.
- **macOS app and scripts:** they use a device token created on the dashboard's **Devices** page. You can also create one with `jarvis-live create-device --name … --user <email>`.
- **Revoking tokens:** a user can revoke any of their device tokens on the Devices page.

### Pocket-ID client

In Pocket-ID, create an OIDC client:

| Setting | Value |
|---|---|
| Callback URL | `https://live.apj.dev/auth/callback` (that is, `<public_base_url>/auth/callback`). The lab channel adds `https://live.lab.apj.dev/auth/callback` to the same client. |
| Public client | off (the server keeps a client secret) |
| PKCE | on |
| Allowed groups | optional, see `OIDC_ALLOWED_GROUPS` below |

The server asks for the scopes `openid email profile groups`. The Android app needs nothing extra in Pocket-ID: it signs in through the server, which redirects back to the app with a one-time code.

### Settings

| Variable | |
|---|---|
| `JARVIS_LIVE_PUBLIC_BASE_URL` | e.g. `https://live.apj.dev`. Used to build the callback URL. When it is `https://…`, cookies are `Secure`. |
| `JARVIS_LIVE_OIDC_ISSUER` | Pocket-ID's URL, e.g. `https://pocket-id.apj.dev`. If this is unset, sign-in is off and only device tokens work. |
| `JARVIS_LIVE_OIDC_CLIENT_ID` | The client ID from Pocket-ID. |
| `JARVIS_LIVE_OIDC_CLIENT_SECRET_FILE` | A file holding the client secret, e.g. a mounted Kubernetes Secret. The server re-reads it on every sign-in, so you can rotate the secret without a restart. |
| `JARVIS_LIVE_OIDC_ADMIN_GROUP` | Members of this Pocket-ID group are admins. If unset, manage admins with `jarvis-live set-admin <email>`. |
| `JARVIS_LIVE_OIDC_ALLOWED_GROUPS` | Comma-separated, e.g. `admins,family`. If set, only members of one of these groups may sign in. |
| `JARVIS_LIVE_APPS_LIST_PATH` | JSON file listing the family's apps behind the dashboard's ▦ switcher: np-home-homelab's shared `family-apps` list, `[{"id"?, "name", "live_url"?, "lab_url"?}]` (the older `{"apps": [...]}` wrapper still reads; a missing `id` is the slugged name). Read once at startup; unset or unreadable means no switcher. Links use the `live_url` on LIVE and the `lab_url` on LAB. |
| `JARVIS_LIVE_OWNER_EMAIL` | Your own (verified) email. Your first sign-in claims the owner account; see below. |
| `JARVIS_LIVE_LOGIN_SESSION_DAYS` | How long the web sign-in lasts. Default 30; each visit extends it. |
| `JARVIS_LIVE_ENVIRONMENT` | Release channel: `live` (default) or `lab`. Anything but `live` shows in `/healthz`, as a badge in the dashboard header and as a `[lab]` prefix on Gotify titles. See `deploy/README.md`. |

### The owner account and existing data

Migration `0002` handles data from before users existed:

- It creates an **owner** account.
- It gives the owner every existing device and session.
- Existing device tokens on the phone and the Mac keep working unchanged.

There are two ways to claim the owner account:

- Set `JARVIS_LIVE_OWNER_EMAIL` before you first sign in. Your first Pocket-ID sign-in with that email then *is* the owner.
- If you already signed in and got a separate, empty account, run `jarvis-live claim-owner <email>`. It moves that account's devices and sessions to the owner and removes the empty account.

The owner is special in three ways:

- The owner is always an admin.
- The owner's final notes stay at the root of the outbox folder.
- Only the owner's sessions use the Obsidian vault for related notes and send Gotify notifications. Both belong to the owner.

Every other user's notes go to `<outbox>/<username>/`.

### CLI

```sh
jarvis-live list-users
jarvis-live set-admin <email|id> [--off]
jarvis-live create-device --name "Kitchen Mac" [--user <email|id>]   # default: the owner
jarvis-live claim-owner <email>
```

### Recordings

`GET /v1/sessions/{id}/audio/{mic|system}.wav` serves a session's recording as WAV and supports `Range` requests. Retention removes recordings after `JARVIS_LIVE_AUDIO_RETENTION_DAYS` (default 90); once a recording is removed, this endpoint returns 404.

### Uploading a recording

A recording made somewhere else (a phone voice memo, a meeting recorded in another app) can be uploaded and processed like a live session: transcribed, run past the copilot, and written up as a note. On the dashboard, use **Upload recording** on the Sessions page. It asks for a title (default: the file name) and the mode, then opens the new session so you can watch the transcript arrive.

- **API.** `POST /v1/sessions/upload?filename=…&title=…&mode=solo|meeting&source=watch|phone|web&client_id=…` with the file as the raw request body. It works with a device token or the dashboard cookie. It answers `202 {"id": …}` once the file is decoded, and the work goes on in the background. The 202 is the acknowledgement that the audio is stored: a client that keeps its own copy (the Wear OS app) deletes it only after this.
- **`source` and `client_id`.** `source` tags where the recording was made; it is returned as `source` on the session (null when not given) and written to the note's frontmatter (`source: watch`). `client_id` is the uploader's own id for the recording, unique per user: uploading again with the same one returns the existing session's id instead of creating a second, so a client that never saw the response can retry safely.
- **Formats.** Anything ffmpeg can decode: WAV, MP3, M4A/MP4/MOV, OGG/Opus, WebM/MKV, FLAC, AAC, AMR, CAF, AIFF, WMA. Video files contribute their audio. The image ships ffmpeg; to run the tests locally you need it on your `PATH`. ffmpeg may only read the uploaded file itself: playlist and concat formats are refused, so an upload can't make the server open URLs or other files.
- **Processing.** The file is decoded to the stream codec (16 kHz mono) and fed through the same ingest path as a live producer, on the mic channel. The session stays `live` while it is transcribed, then ends, and the finalizer writes its note.
- **Errors.** You get 415 if the file holds no readable audio, 413 if it is over the size limit, and 422 if the body is empty. None of these create a session.
- **Restarts.** An upload is not resumed after a server restart. The idle watchdog ends the session, and its note covers what was transcribed before the restart. Upload the file again to get all of it.

| Variable | |
|---|---|
| `JARVIS_LIVE_UPLOAD_MAX_MB` | Largest upload accepted. Default 500. The dashboard uploads only the audio track of an MP4/MOV video (fragmented MP4 from OBS included), so long videos fit. |
| `JARVIS_LIVE_FFMPEG_PATH` | Default `ffmpeg`. |

## Notes: edit, regenerate, publish to the vault

Each finished session's note sits in the outbox. From the session page in the dashboard you can:

- **Edit** the note's Markdown (`PUT /v1/sessions/{id}/note`). Keep the frontmatter line `session_id: <id>`: the finalizer recognises its own file by it.
- **Regenerate** it from the transcript (`POST /v1/sessions/{id}/regenerate`). The session goes back to `finalizing` while the LLM runs, and the new note replaces the old one. This is refused while the note's pull request is open.
- **Transcribe & summarize** (`POST /v1/sessions/{id}/process`, also on Android). The stored recording is transcribed again with the same VAD, STT and guards as a live session, then the note is regenerated. If any segment can't be transcribed, the old transcript and note are kept and `processing.transcription` reads `failed`. A session with no stored audio only gets its note rewritten. This never starts a second job: if one is already running, the 202 response has `started: false` and that job's state.

**Processing state.** `GET /v1/sessions/{id}` includes `processing: {transcription, summary, started_at, job_source}`. Each phase is `idle`, `queued`, `running` or `failed`. `GET .../processing` returns only this object, so clients can poll it. The state comes from the jobs running in this process (single replica), checked in this order:

| Job in flight | transcription | summary | `job_source` |
|---|---|---|---|
| orchestrator re-transcription (`/process`) | running | queued | `reprocess` |
| orchestrator note run (end of session, `/regenerate`, `/process` after its transcript) | idle | running | `finalize` / `regenerate` / `reprocess` |
| hub draining an ending session's last segments | running | queued | `finalize` |
| uploaded file being fed and transcribed | running | queued | `upload` |
| producer streaming | running | idle | `live` |
| none | idle (`failed` after a failed re-transcription) | idle (`failed` if the session failed) | null |

**Publishing** works like capture's vault-writer. Each session gets one branch and one pull request in the vault repo, against its base branch, and nothing is pushed straight to `main`. The server does this through the GitHub REST API, so it needs no git binary and no clone.

- **Who.** Only the owner and admins can publish, and only notes from their own sessions. Other users' notes stay in the outbox.
- **Where.** A note at `<outbox>/<path>` goes to `<PUBLISH_SUBDIR>/<path>` on the branch `<PUBLISH_BRANCH_PREFIX><date>-<session id prefix>`. The first publish fixes the file's path in the repo, so a later republish updates the same file.
- **Actions.** Publish (opens the PR), Merge (`PUBLISH_MERGE_METHOD`, only if the PR head is still what was pushed), Close, and Skip. Skip keeps the note out of publishing and closes an open PR. An edit made while the PR is open is pushed to its branch.
- **Auto-publish.** A toggle on the Publishing page (`PATCH /v1/me {"auto_publish": true}`). With it on, each new note opens its PR as soon as it is written. A skipped or already-published note is not re-proposed when it is regenerated.
- **Merges on GitHub.** A PR merged or closed on GitHub is picked up every `PUBLISH_RECONCILE_INTERVAL_S` seconds, when its session is opened, or with *Sync now*.
- **The Publishing page** (`GET /v1/publishing`) lists notes ready to publish, open PRs, and recent results. *Publish all* publishes every ready note, one PR each.

| Variable | |
|---|---|
| `JARVIS_LIVE_PUBLISH_REPO` | `owner/name` of the vault repo, e.g. `lordmuffin/Notes`. If unset, publishing is off. |
| `JARVIS_LIVE_GITHUB_TOKEN_FILE` | A file holding a fine-grained PAT with **Contents** and **Pull requests** read+write on that repo. Contents alone lets the push through and then fails at the PR. The server re-reads the file on every call. |
| `JARVIS_LIVE_PUBLISH_BASE_BRANCH` | Default `main`. |
| `JARVIS_LIVE_PUBLISH_SUBDIR` | Default `00 Inbox/AI Output`, the vault folder for AI-made notes. Notes follow the vault's AI Output template (`type: ai-output`, `YYYY-MM-DD - Title.md`). |
| `JARVIS_LIVE_PUBLISH_BRANCH_PREFIX` | Default `jarvis-live/`. This prefix is reserved for this app, which resets a leftover branch of the same name. |
| `JARVIS_LIVE_PUBLISH_MERGE_METHOD` | `squash` (default), `merge` or `rebase`. |
| `JARVIS_LIVE_PUBLISH_RECONCILE_INTERVAL_S` | Default 300; `0` turns the background check off. |
| `JARVIS_LIVE_PUBLISH_COMMITTER_NAME` / `_EMAIL` | Default `jarvis-live` / `jarvis-live@apj.dev`. |

## Work items: ideas as Claude Code prompts

When you think out loud in a session, the copilot picks out the ideas that are work: changes to one of your repositories. It groups related ideas from across the session into draft **work items**. Each one has a title, a target repo, the intent, acceptance criteria, a done-when line, and links back to where you said it. Each work item renders to a Claude Code prompt from [`prompts/templates/work-item.md`](../prompts/templates/work-item.md). The plan, including the dispatch phase still to come, is [`docs/plans/copilot-to-claude-code.md`](../docs/plans/copilot-to-claude-code.md).

- **Whose sessions.** Only the owner's and admins' sessions are mined, and never a local-only one. Only the session's owner sees its work items.
- **How they are found.** An *ideas pass* runs next to the live copilot, on its own cadence: after `IDEAS_MIN_NEW_SEGMENTS` new final segments, or `IDEAS_INTERVAL_S` after the first new one. It sends the LLM the transcript since the last pass, plus `IDEAS_OVERLAP_S` of overlap, along with the open work items. The LLM attaches new lines to existing items or proposes new ones, and the server decides what is kept (`workitems/state.py`):
  - refs are server-issued;
  - fields you edited are locked;
  - discarded, approved and merged items stay closed;
  - repos must be on the allowlist;
  - every item cites the transcript.
- **Session end.** One more pass runs before the note is written. The note gets a **Work items** section with links back to the dashboard. Approved items also carry their prompt in a collapsed callout.
- **Reviewing.** Work items appear in the session page (live and past) and on the **Work Items** page. There you can edit, approve, merge drafts of one session, discard and restore.
  - **Approving** needs the dashboard sign-in, not a device token. It freezes the prompt: what you copy is byte-for-byte what was approved, with its sha256 in the audit log.
  - **Editing** an approved item puts it back to draft.
- **Closing.** An approved item goes to `done` when a PR merges on Forgejo on the branch its prompt names, on a branch one of its runs used, or with its `Jarvis-Work-Item: <id>` line in the description. Prompts approved before that line existed match by branch only. Only repos in `JARVIS_LIVE_WORK_FORGEJO_REPOS` are looked up. **Mark Done** closes any open item by hand; **Reopen** undoes it, and the same PR does not close the item again. A PR closed without merging changes nothing. (`merged` is unrelated: a draft folded into another.)
- **Audit log.** Every change, LLM or human, is written to `work_item_events`.
- **Dispatch** (`POST /v1/work-items/{id}/dispatch`): see below. The gate refuses (409, logged) unless the item is approved, its prompt is unchanged since, it was approved by you from the dashboard, and its repo is on both the work and the dispatch allowlists.

| Variable | |
|---|---|
| `JARVIS_LIVE_WORK_ITEMS_ENABLED` | Default off. Needs an LLM. The template is checked at startup, so a broken one fails fast. |
| `JARVIS_LIVE_WORK_REPOS` | The only repos a work item may target, one `owner/name  description` per line. The description helps the LLM pick. |
| `JARVIS_LIVE_IDEAS_MODEL` | Empty means the copilot model. Compare models with `tests/evals/test_ideas_evals.py` (`--live`). |
| `JARVIS_LIVE_IDEAS_MIN_NEW_SEGMENTS` / `_INTERVAL_S` / `_OVERLAP_S` | Defaults 4 / 120 / 60. |
| `JARVIS_LIVE_FORGEJO_URL`, `JARVIS_LIVE_FORGEJO_TOKEN_FILE` | The Forgejo that holds work repos, and a `read:repository` token, to close items whose PR merged there. Unset: only Mark Done closes items. |
| `JARVIS_LIVE_WORK_FORGEJO_REPOS` | Which work repos to look up: `work-repo  forgejo-owner/name` per line. A repo not listed (it lives on GitHub) closes only by Mark Done. |
| `JARVIS_LIVE_WORK_PR_POLL_S` | Seconds between those lookups. Default 300; 0 disables. |
| `JARVIS_LIVE_WORK_TEMPLATE` | Overrides the template path. Default: `/app/prompts/templates/work-item.md` in the image (copied from `prompts/` at build time), else the repo's copy. |

## Dispatch: approved work items as headless Claude Code

**Run in Claude Code** on an approved work item queues a run. Each run is one Kubernetes Job in the app's namespace, with one git worktree, and opens a **draft PR** on the item's repo. The design and its safety reasoning are in [`docs/plans/copilot-to-claude-code.md`](../docs/plans/copilot-to-claude-code.md) §3.

- **The Job:** three steps from the runner image (`dispatch-runner/`).
  - `prepare` holds the GitHub token. It fetches a shared per-repo mirror and creates the item's worktree.
  - `claude` holds your Claude subscription token. It runs `claude -p` with Sonnet in the worktree.
  - `push` holds the GitHub token. It applies Claude's patch to a fresh clone, pushes the branch and opens the PR.

  Claude never holds a GitHub token, and nothing it leaves in its worktree runs with one.
- **Tokens:** each run mints a GitHub App token for its one repo: Contents and Pull requests write, never Workflows. It is refreshed every 30 minutes while the run lasts.
- **One run at a time** (`DISPATCH_MAX_CONCURRENT`): the runs share your subscription's usage limits.
- **Gating a queued run:** it starts only if its item still has the prompt you approved. One active run per item; a retry gets its own branch (`…-2`).
- **Unattended:** the runner tells Claude that nobody can answer mid-run. It must not stop to ask: it notes its assumptions and keeps going, and commits as it goes (it has a git author but no GitHub credential). Only when it truly cannot finish does it end with `NEEDS INPUT:` and its questions. That run ends **needs your input**, with whatever work it did pushed to the draft PR. The Gotify push links to the Work Items page.
- **Steering** (`POST /v1/work-items/{id}/dispatch/continue`, `{"guidance": "…"}`): **Answer Claude** (open when a run asks) or **↪ Steer Next Run** on any finished run queues a follow-up through the same gate. It continues on the last run's branch and draft PR, not a new one. Its prompt is the approved prompt plus the last run's report and questions, then your reply, which wins where they conflict. **Run Again** still starts over on a fresh branch.
- **Status:** the card shows queued, running, the PR, needs your input (with Claude's questions), no changes, failed, timed out (1 h deadline) or the usage limit, plus your reply on a follow-up. You get a Gotify push when a run ends. **Cancel** deletes the Job.
- **Audit:** every step is in `work_item_events`: requested (with the prompt, and the guidance on a follow-up), started (job, image, model, the run it continues), finished (outcome, PR, questions, error) and cancelled.
- **Misconfiguration:** dispatch stays off, with an error in the log listing what is missing. The server still starts.

| Variable | |
|---|---|
| `JARVIS_LIVE_DISPATCH_ENABLED` | Default off. Needs work items, a runner image and a GitHub App. |
| `JARVIS_LIVE_DISPATCH_REPOS` | Repos a run may target, same format as `WORK_REPOS`. Empty means the work repos. Lab: the sandbox only. |
| `JARVIS_LIVE_GITHUB_APP_ID` / `_INSTALLATION_ID` / `_KEY_FILE` | The GitHub App that mints run tokens. |
| `JARVIS_LIVE_DISPATCH_MODEL` | Default `claude-sonnet-5-5`. |
| `JARVIS_LIVE_DISPATCH_IMAGE` | Overrides the runner image. Default: `ghcr.io/lordmuffin/jarvis-dispatch-runner:sha-<7>` of this server's commit (`JARVIS_LIVE_GIT_SHA`, baked in at build). |
| `JARVIS_LIVE_DISPATCH_MAX_CONCURRENT` / `_DEADLINE_S` / `_POLL_S` | Defaults 1 / 3600 / 15. |
| `JARVIS_LIVE_DISPATCH_OAUTH_SECRET` / `_MIRROR_PVC` / `_POSTGRES_IMAGE` / `_NODE_SELECTOR` | Defaults `claude-oauth` / `jarvis-dispatch-mirrors` / `postgres:16.4-alpine` / `kubernetes.io/arch=amd64`. |
