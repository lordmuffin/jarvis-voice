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
| Callback URL | `https://live.lab.apj.dev/auth/callback` (that is, `<public_base_url>/auth/callback`) |
| Public client | off (the server keeps a client secret) |
| PKCE | on |
| Allowed groups | optional, see `OIDC_ALLOWED_GROUP` below |

The server asks for the scopes `openid email profile groups`. The Android app needs nothing extra in Pocket-ID: it signs in through the server, which redirects back to the app with a one-time code.

### Settings

| Variable | |
|---|---|
| `JARVIS_LIVE_PUBLIC_BASE_URL` | e.g. `https://live.lab.apj.dev`. Used to build the callback URL. When it is `https://…`, cookies are `Secure`. |
| `JARVIS_LIVE_OIDC_ISSUER` | Pocket-ID's URL, e.g. `https://id.lab.apj.dev`. If this is unset, sign-in is off and only device tokens work. |
| `JARVIS_LIVE_OIDC_CLIENT_ID` | The client ID from Pocket-ID. |
| `JARVIS_LIVE_OIDC_CLIENT_SECRET_FILE` | A file holding the client secret, e.g. a mounted Kubernetes Secret. The server re-reads it on every sign-in, so you can rotate the secret without a restart. |
| `JARVIS_LIVE_OIDC_ADMIN_GROUP` | Members of this Pocket-ID group are admins. If unset, manage admins with `jarvis-live set-admin <email>`. |
| `JARVIS_LIVE_OIDC_ALLOWED_GROUP` | If set, only members of this group may sign in. |
| `JARVIS_LIVE_OWNER_EMAIL` | Your own (verified) email. Your first sign-in claims the owner account; see below. |
| `JARVIS_LIVE_LOGIN_SESSION_DAYS` | How long the web sign-in lasts. Default 30; each visit extends it. |

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
