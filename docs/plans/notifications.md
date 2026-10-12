# Plan: session notifications (Gotify)

Status: **proposed** — not implemented yet. Scope: `server/` (jarvis-live), `web/`,
`server/deploy/k8s/`. No `protocol/` change (this is server-internal plus REST, not the
wire protocol). Legacy `src/jarvis_voice/` is not touched.

## 1. What exists today

Gotify is already partly wired, so this is a refactor + extension, not a greenfield build:

| Piece | Where | Today |
|---|---|---|
| `Notifier` protocol, `GotifyNotifier`, `NullNotifier` | `server/src/jarvis_live/notify/gotify.py` | `notify(title, message, priority, url)`. One POST, 10 s timeout, **no retries**, swallows every error. Adds the `[lab] ` title prefix and a click URL. |
| Config | `config.py` | `gotify_url`, `gotify_token_file`, `public_base_url`, `stt_outage_notify_s` |
| Token Secret | `deploy/k8s/base/onepassword-items.yaml`, `deployment.yaml` | `OnePasswordItem jarvis-live-gotify-token` → Secret, mounted at `/secrets/gotify/password`, `JARVIS_LIVE_GOTIFY_TOKEN_FILE` points there. Lab shares it via `../base`. |
| URL | `deploy/k8s/base/configmap.yaml` | `JARVIS_LIVE_GOTIFY_URL: http://gotify.gotify.svc.cluster.local` |
| Call sites | `finalize/finalizer.py` | "Jarvis: note ready" (prio 5) and "Jarvis: note failed" (prio 8), owner's sessions only, skipped on a user-asked regeneration |
| | `orchestrator.py` | "Jarvis: transcription down" (prio 8), once per STT outage, deployment-wide |
| Notifier only built with an LLM | `app.py` | `GotifyNotifier` is created inside the `if llm or litellm_base_url` branch |

There is **no Helm chart** in the repo; deployment is Kustomize (`server/deploy/k8s/`). The
"Helm values update" in the request becomes a Kustomize update.

There is an in-process `Bus` (`bus.py`), but it fans protocol messages out to a session's
WebSocket viewers. It is the wrong shape for this (per-session, drops when nobody listens,
carries wire-protocol models), so the event bus below is a separate, small component.

## 2. Every session state change found in the code

"Recording" in the request = a **session** here (`sessions` table, statuses
`live → finalizing → done | failed`). Audio streams in live over the WebSocket; the
Android/Mac "upload" of an offline recording replays the same stream protocol and ends it,
so the server sees an upload exactly like a live session.

| # | Change | Code point | Proposed event |
|---|---|---|---|
| 1 | Session row created (`live`) | `api/sessions.py: create_session` | — (nothing recorded yet; an abandoned create would be noise) |
| 2 | First producer connects, ingest state created | `ingest/hub.py: IngestHub.get` → `on_start` | `recording.started` (once per session — `on_start` fires again when a producer reconnects after a server restart) |
| 3 | Producer superseded / disconnects mid-session | `api/stream.py: _producer` | — (transient; the client reconnects and resumes) |
| 4 | Session ended: `live → finalizing`. Reasons: producer `end` message, `POST /v1/sessions/{id}/end`, idle watchdog, orphan watchdog | `ingest/hub.py: IngestHub.end` (`transitioned`) | `recording.stopped` (with `reason`: `client` / `idle` / `no_producer`) |
| 5 | Audio store closed + fsynced: every received frame is durable | `IngestHub.end` after `state.store.close()` | `recording.saved` — the server-side "upload complete" |
| 6 | Pipeline drained: every segment transcribed | `IngestHub.end` after `state.pipeline.finish()` | `transcription.completed` |
| 7 | A segment lost because no STT tier was up | `ingest/pipeline.py: _process` (`STTUnavailable`) | `transcription.failed` (once per session, first loss) |
| 8 | All STT tiers down during a live session (deployment-wide) | `orchestrator.py: _check_stt_outage` | `system.stt_outage` (no recording; owner only; keeps today's message) |
| 9 | Finalizer: note written, `finalizing → done` | `finalize/finalizer.py: _finalize` | `summary.completed` (`summary` = note summary, `regenerated` flag) |
| 10 | Finalizer: local-only or empty transcript → `done`, no note | `_finalize` early returns | — (`recording.saved` already told the user) |
| 11 | Finalizer raised, `finalizing → failed` | `Finalizer.run` except branch | `summary.failed` (`error`) |
| 12 | Regeneration requested (`done/failed → finalizing`) | `api/publishing.py: regenerate` | — (user-initiated; its result is #9/#11 with `regenerated=True`) |
| 13 | Startup recovery re-finalizes stuck sessions | `orchestrator.py: recover` | — (results are #9/#11, deduplicated, see §4) |
| 14 | Note published: PR opened (manual or auto-publish) | `publish/service.py: publish` | `vault_note.proposed` (default off) |
| 15 | PR merged — the note is now **in the vault** (dashboard merge, or picked up by `refresh`/reconcile) | `PublishService.merge`, `_settle` | `vault_note.written` |
| 16 | Publish failed (`GitHubError`), incl. silent auto-publish failure | `PublishService.publish` → `_fail`; `on_note_ready` | `vault_note.failed` (`error`) |
| 17 | PR closed / note skipped / unskipped / edited | `close`, `skip`, `unskip`, `edit_note` | — (user actions in the dashboard; they already see the result) |
| 18 | Audio purged by retention | `retention.py` | — |

Note on "vault note written": the finalizer writes the note to the **outbox** on the
server's disk; it only reaches the Obsidian vault (`lordmuffin/Notes`) when its PR is merged.
So `summary.completed` = note ready in Jarvis Live, `vault_note.written` = merged into the
vault. Lab has publishing switched off, so #14–16 never fire there.

## 3. Event model

New module `jarvis_live/events.py`:

```python
class EventType(StrEnum):
    RECORDING_STARTED = "recording.started"
    RECORDING_STOPPED = "recording.stopped"
    RECORDING_SAVED = "recording.saved"
    TRANSCRIPTION_COMPLETED = "transcription.completed"
    TRANSCRIPTION_FAILED = "transcription.failed"
    SUMMARY_COMPLETED = "summary.completed"
    SUMMARY_FAILED = "summary.failed"
    VAULT_NOTE_PROPOSED = "vault_note.proposed"
    VAULT_NOTE_WRITTEN = "vault_note.written"
    VAULT_NOTE_FAILED = "vault_note.failed"
    SYSTEM_STT_OUTAGE = "system.stt_outage"

@dataclass(frozen=True)
class SessionEvent:           # what emitters create: cheap, no I/O
    type: EventType
    session_id: UUID
    occurred_at: datetime
    reason: str | None = None       # recording.stopped
    summary: str | None = None      # summary.completed
    error: str | None = None        # *.failed
    pr_url: str | None = None       # vault_note.*
    regenerated: bool = False       # summary.*

@dataclass(frozen=True)
class RecordingInfo:          # filled in by the dispatcher from the DB
    session_id: UUID
    user_id: UUID
    device: str | None
    mode: str
    title: str | None               # session title, else note title, else None
    started_at: datetime
    duration_s: float | None        # ended_at - started_at (live: now - started_at)
    url: str | None                 # <public_base_url>/sessions/<id>; None if unset
    local_only: bool
```

Every event carries recording ID, title, duration and deep link via `RecordingInfo`, as
asked. Emitters only know the session id; enrichment happens off the hot path.

**Emitter / bus** (`jarvis_live/events.py`):

- `EventBus.emit(event)` is **synchronous and never blocks or raises**: `put_nowait` on a
  bounded `asyncio.Queue` (1000); on overflow, log a warning and drop.
- One dispatcher task (started in `app.py` lifespan, cancelled on shutdown) takes events,
  loads `RecordingInfo` with one query, and calls each subscriber
  (`async def handle(event, info)`), catching and logging per subscriber.
- Subscribers register with `bus.subscribe(handler)`. The notification service is the only
  subscriber now; the dashboard or a webhook could subscribe later.
- Emit points get the bus injected (`IngestHub`, `SessionPipeline`, `Orchestrator`,
  `Finalizer`, `PublishService`) — they stop holding a `Notifier` at all. Tests pass a
  recording fake bus.
- The bus and notifier are built **unconditionally** in `app.py`, not only when an LLM is
  configured, so recording events work on an LLM-less deployment.

## 4. Notification service

`jarvis_live/notify/service.py`, subscribed to the bus:

1. **Dedupe** once-per-session events (`recording.started`, `recording.saved`,
   `transcription.*`, `summary.*` unless `regenerated`) against a new
   `notification_log` table (`session_id, event_type, sent_at`, unique on the first two).
   This survives restarts: `on_start` re-firing after a reconnect, and `recover()`
   re-finalizing, don't double-notify.
2. **Recipient** = the session's user (`info.user_id`); `system.stt_outage` goes to the owner.
3. **Policy gates** (kept from today):
   - user-asked regenerations don't notify on success (`regenerated=True` and
     `summary.completed`); a failed regeneration does.
   - delivery is only to a user who has a destination — see §6, open question 1.
4. **Preferences**: `enabled` false → drop; otherwise use the user's `priority`.
5. **Render** (pure function, §5) → `Message(title, body, priority, url)`.
6. **Send** through every configured channel (`Notifier`s) concurrently, each wrapped so one
   channel failing doesn't affect another.

**Pluggable channel interface** (`notify/base.py`) replaces the current 4-arg protocol:

```python
@dataclass(frozen=True)
class Message:
    title: str
    body: str            # Markdown
    priority: int        # 0..10 (Gotify scale; other channels map it)
    url: str | None
    event_type: EventType

class Notifier(Protocol):
    name: str
    async def send(self, message: Message, recipient: User) -> None: ...  # must not raise
```

Adding ntfy/email later = one new class + registration in `app.py`; no emitter changes.

### Gotify sender

`notify/gotify.py` (`GotifyNotifier.send`):

- URL from `JARVIS_LIVE_GOTIFY_URL` (ConfigMap), app token read from
  `JARVIS_LIVE_GOTIFY_TOKEN_FILE` (the existing Secret mount) on each send, so a rotated
  1Password item is picked up without a restart. Never in code or the ConfigMap.
- Per-attempt timeout `gotify_timeout_s` (default 10 s); `gotify_retries` (default 3)
  with exponential backoff + jitter (0.5 s, 1 s, 2 s). Retry on connect/read errors,
  timeouts, 429 and 5xx; **not** on other 4xx (bad token won't fix itself) — log those once
  at `warning` with the status, never the token.
- Every failure is logged and swallowed. Because sends run in the dispatcher task, never in
  ingest/finalizer code, even the worst case (all retries timing out, ~40 s) delays only
  later notifications, never the pipeline.
- Keeps `[lab] ` prefix, Markdown display extra and click-through URL.
- Not configured (no URL or no token file) → the service registers no Gotify channel.

## 5. Message quality

Duration formatted as `29 min` (`<1 min` under a minute, `1 h 12 min` over an hour).
Untitled sessions fall back to `Untitled <mode> session, <HH:MM>` in the deployment timezone.

| Event | Title | Body |
|---|---|---|
| `recording.started` | `Recording started: <title>` | `<device> · <mode>` |
| `recording.stopped` | `Recording stopped: <title> (29 min)` | reason if not the client (`Ended after 10 min without audio.`) |
| `recording.saved` | `Recording saved: <title> (29 min)` | `Transcribing now.` / `Local-only: no summary will be written.` |
| `transcription.completed` | `Transcript ready: <title> (29 min)` | `<n> segments.` |
| `transcription.failed` | `Transcription gaps: <title>` | `Speech-to-text was unavailable; part of the audio was not transcribed.` |
| `summary.completed` | `Summary ready: <title> (29 min)` | **first line of the summary** |
| `summary.failed` | `Summary failed: <title> (29 min)` | error (trimmed to 300 chars) |
| `vault_note.proposed` | `Note proposed to vault: <title>` | PR link |
| `vault_note.written` | `Note in vault: <title>` | first summary line if available, PR link |
| `vault_note.failed` | `Vault publish failed: <title>` | error |
| `system.stt_outage` | `Jarvis: transcription down` | today's text |

The `url` (Gotify click action) is always the session page. "First line of the summary"
= first non-empty line of `FinalNote.summary`, Markdown list/heading marker stripped,
capped at 200 chars. For `vault_note.written` it is read from the session's latest note
(`latest_final_note`), best effort.

## 6. Per-user settings

**Storage** — new table (Alembic `0004_notification_preferences`):

```
notification_preferences(
  user_id    uuid  FK users.id ON DELETE CASCADE,
  event_type text  CHECK in EventType values,
  enabled    bool,
  priority   int   CHECK 0..10,
  PRIMARY KEY (user_id, event_type)
)
```

No row = default, so new event types roll out without a data migration. There is no tenant
concept in jarvis-live; **the user is the scope** (each user's sessions already belong to them).

**Defaults** (`notify/defaults.py`):

| Event | Enabled | Priority |
|---|---|---|
| `recording.started` | off | 3 |
| `recording.stopped` | off | 3 |
| `recording.saved` | **on** | 5 |
| `transcription.completed` | off | 4 |
| `transcription.failed` | **on** | 7 |
| `summary.completed` | **on** (see open question 2) | 5 |
| `summary.failed` | **on** | 8 |
| `vault_note.proposed` | off | 3 |
| `vault_note.written` | off | 4 |
| `vault_note.failed` | **on** | 7 |
| `system.stt_outage` | **on** (owner/admins only) | 8 |

**API** (`api/account.py`):

- `GET /v1/me/notifications` → `{channels: ["gotify"], events: [{type, label, group,
  enabled, priority, default_enabled, default_priority}]}` (merged with defaults; only
  events relevant to the caller, e.g. no `vault_note.*` if they can't publish).
- `PUT /v1/me/notifications` with `{events: [{type, enabled, priority}]}` → upsert; a value
  equal to the default deletes the row.
- `POST /v1/me/notifications/test` → sends a test message through the caller's channels
  (makes the lab check in §9 quick).

**Web** — new `/settings` route (`views/Settings.tsx`, link in the header next to
Devices): a table of events grouped Recording / Transcript / Summary / Vault / System, each
with a toggle and a priority select (Low 3 / Normal 5 / High 8, or 0–10), plus "Send test".
Follows `design/tokens.json`.

## 7. Files

New: `server/src/jarvis_live/events.py`, `notify/base.py`, `notify/service.py`,
`notify/render.py`, `notify/defaults.py`, `alembic/versions/0004_notification_preferences.py`,
`web/src/views/Settings.tsx`, tests below.

Changed: `notify/gotify.py` (send/retries), `config.py` (`gotify_timeout_s`,
`gotify_retries`), `app.py` (bus + service wiring), `ingest/hub.py`, `ingest/pipeline.py`,
`orchestrator.py`, `finalize/finalizer.py`, `publish/service.py`, `db/models.py`,
`api/account.py`, `web/src/{route,api,types,app}.ts(x)`, `tests/fakes.py`,
existing tests that assert on `FakeNotifier.sent`, `server/README.md`,
`server/deploy/k8s/base/configmap.yaml`.

## 8. Tests

Unit (`server/tests/`):
- `test_notify_render.py` — every event type → expected title/body/priority/url; duration
  formatting edges (0 s, 59 s, 29 min, 72 min); untitled fallback; summary first-line
  extraction (lists, headings, blank lines, long lines); `[lab]` prefix.
- `test_notify_preferences.py` — defaults when no rows; override on/off and priority;
  disabled → nothing sent; PUT to default deletes the row; users can't see/edit each
  other's settings; regeneration success suppressed, failure not; dedupe across a
  simulated restart.
- `test_events.py` — `emit` never blocks or raises (full queue, failing subscriber);
  enrichment fills title/duration/url.
- `test_gotify.py` (extended) — retries on 5xx/429/timeout then succeeds; no retry on
  401; gives up after N and logs; token file re-read; never raises.

Integration:
- `test_notifications_e2e.py` — a real HTTP **Gotify mock** (small Starlette app on a free
  port collecting `POST /message`, checking `X-Gotify-Key`) + the full app as in
  `test_a3_e2e.py`: create session → stream audio → end → finalize. Assert the mock
  received exactly the default set (`Recording saved…`, `Summary ready…`) with correct
  titles, priorities and click URLs; then disable `summary.completed` via the API, run a
  second session, assert only `Recording saved…`. Plus a failure path (LLM raises →
  `Summary failed…`) and a mock returning 503 twice then 200 (delivered once, pipeline
  timing unaffected).
- Web: Playwright spec for the settings page (toggle persists across reload).

Gate before pushing (CLAUDE.md): `uv run ruff check . && uv run ruff format --check . &&
uv run mypy src && uv run pytest -q`; `npm test && npm run build && npx playwright test`.

## 9. Deployment

- **Secret**: already exists — `OnePasswordItem jarvis-live-gotify-token` → Secret
  `jarvis-live-gotify-token`, key `password`, mounted at `/secrets/gotify`. No change
  needed; I'll tighten its comment to say it now carries every user-facing notification.
- **ConfigMap** (`base/configmap.yaml`): add `JARVIS_LIVE_GOTIFY_TIMEOUT_S: "10"` and
  `JARVIS_LIVE_GOTIFY_RETRIES: "3"` (explicit, so they're tunable without a rebuild).
- **Migration** `0004` runs at startup (`JARVIS_LIVE_AUTO_MIGRATE=true`), lab first.
- Lab (the "staging" channel, `live.lab.apj.dev`) gets it on merge to `main`; titles carry
  `[lab] `. Nothing to change in np-home-homelab unless lab should get its own Gotify app
  (open question 3).

**Done-when check on lab** (by you, after merge — I can't reach the cluster from here):
1. Sign in at `live.lab.apj.dev` → Settings → "Send test" arrives in Gotify as `[lab] …`.
2. Record a short session from the phone; expect `[lab] Recording saved: … (n min)` then
   `[lab] Summary ready: … (n min)` with the summary's first line; tapping opens the session.
3. Turn `summary.completed` off, record again → only `Recording saved`.
4. `vault_note.*` can only be verified on live (lab has publishing off by design).

## 10. Open questions (need your call before implementing)

1. **Who receives what.** There is one Gotify app token for the deployment, and it reaches
   *your* phone. Today only the owner's sessions notify, on purpose: other users' titles and
   summaries are not yours to read. Per-user settings only mean something for other users if
   they have their own destination. Options:
   - **(a, recommended for this PR)** Settings exist and are honoured for every user, but
     the deployment Gotify channel delivers only the owner's events (and `system.*`).
     Others' settings take effect once a per-user channel exists. Privacy invariant
     unchanged.
   - (b) Each user pastes their own Gotify URL + app token in Settings; stored encrypted in
     Postgres with a key from a new k8s Secret. Real per-user delivery, but adds secret
     storage and a new Secret to provision in np-home-homelab.
   - (c) Admins' sessions also notify the owner's Gotify (you are presumably the admin).
2. **`summary.completed` default.** You listed "failures and recording saved" as on. Today
   "note ready" is the one push you get per session; defaulting it off would silently drop
   that. I'd default it **on** — say if you want it off.
3. **Lab token.** Lab shares live's Gotify app token, so lab pushes land in the same Gotify
   app (distinguished only by `[lab]`). A separate `jarvis-live-lab` Gotify app (new
   1Password item + one lab patch in this repo) would let you mute lab alone. Want it?
4. **Granularity of the early events.** `recording.saved` and `transcription.completed` fire
   seconds-to-minutes apart at the end of a session (the store closes, then the STT queue
   drains). Fine to keep both, with only `saved` on by default?

## 11. Order of work (one PR, `feat(server): session notifications via Gotify`)

1. Events module + bus + fake; wire emit points (no behaviour change yet: a temporary
   subscriber reproduces today's three messages, existing tests stay green).
2. Notifier interface + Gotify retries/timeout; render module + unit tests.
3. Preferences table, migration, defaults, service with dedupe; switch over from the
   temporary subscriber; update existing notifier tests.
4. REST API + web Settings page + Playwright test.
5. Gotify-mock e2e; README + ConfigMap; full test gate; push.
