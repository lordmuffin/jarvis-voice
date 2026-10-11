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
| Callback URL | `https://live.lab.apj.dev/auth/callback` (that is, `<public_base_url>/auth/callback`). The lab channel adds `https://live-next.lab.apj.dev/auth/callback` to the same client. |
| Public client | off (the server keeps a client secret) |
| PKCE | on |
| Allowed groups | optional, see `OIDC_ALLOWED_GROUPS` below |

The server asks for the scopes `openid email profile groups`. The Android app needs nothing extra in Pocket-ID: it signs in through the server, which redirects back to the app with a one-time code.

### Settings

| Variable | |
|---|---|
| `JARVIS_LIVE_PUBLIC_BASE_URL` | e.g. `https://live.lab.apj.dev`. Used to build the callback URL. When it is `https://…`, cookies are `Secure`. |
| `JARVIS_LIVE_OIDC_ISSUER` | Pocket-ID's URL, e.g. `https://id.lab.apj.dev`. If this is unset, sign-in is off and only device tokens work. |
| `JARVIS_LIVE_OIDC_CLIENT_ID` | The client ID from Pocket-ID. |
| `JARVIS_LIVE_OIDC_CLIENT_SECRET_FILE` | A file holding the client secret, e.g. a mounted Kubernetes Secret. The server re-reads it on every sign-in, so you can rotate the secret without a restart. |
| `JARVIS_LIVE_OIDC_ADMIN_GROUP` | Members of this Pocket-ID group are admins. If unset, manage admins with `jarvis-live set-admin <email>`. |
| `JARVIS_LIVE_OIDC_ALLOWED_GROUPS` | Comma-separated, e.g. `admins,family`. If set, only members of one of these groups may sign in. |
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

## Notes: edit, regenerate, publish to the vault

Each finished session's note sits in the outbox. From the session page in the dashboard you can:

- **Edit** the note's Markdown (`PUT /v1/sessions/{id}/note`). Keep the frontmatter line `session_id: <id>`: the finalizer recognises its own file by it.
- **Regenerate** it from the transcript (`POST /v1/sessions/{id}/regenerate`). The session goes back to `finalizing` while the LLM runs, and the new note replaces the old one. This is refused while the note's pull request is open.

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
| `JARVIS_LIVE_PUBLISH_SUBDIR` | Default `00 Inbox/Live Sessions`. |
| `JARVIS_LIVE_PUBLISH_BRANCH_PREFIX` | Default `jarvis-live/`. This prefix is reserved for this app, which resets a leftover branch of the same name. |
| `JARVIS_LIVE_PUBLISH_MERGE_METHOD` | `squash` (default), `merge` or `rebase`. |
| `JARVIS_LIVE_PUBLISH_RECONCILE_INTERVAL_S` | Default 300; `0` turns the background check off. |
| `JARVIS_LIVE_PUBLISH_COMMITTER_NAME` / `_EMAIL` | Default `jarvis-live` / `jarvis-live@apj.dev`. |
