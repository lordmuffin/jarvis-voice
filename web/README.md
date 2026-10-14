# Jarvis Live web dashboard

A read-only viewer for [`server/`](../server): watch a session live (transcript, notes,
actions, decisions, suggestions, related) or browse past ones. Vite + TypeScript + Preact +
`@preact/signals`. No external fonts or CDNs; everything is served by the jarvis-live process.

There is no browser-side recording: producers are the Mac app and, later, Android.

## Use

Sign in with **Sign In with Pocket-ID**: the browser goes to `/auth/login`, through Pocket-ID,
and back to the page it started on with an HttpOnly session cookie. The dashboard holds no
credential of its own; every request rides on that cookie, and WebSocket access uses
short-lived **viewer** tickets. Any 401 returns to the sign-in screen.

You see your own sessions. **Devices** lists your device tokens: add one there to set up the
Mac app (the token is shown once, with a copy button) and revoke tokens you no longer use.
Admins also get **Admin** (every user, with session counts and last sign-in; a name opens
that user's sessions) and an **All users** switch on the sessions list. Past sessions play
their recordings, one player per channel (Me = mic, Them = system audio).

The FastAPI app serves `web/dist` at `/` (SPA fallback after the API routes). Set
`JARVIS_LIVE_WEB_DIST_DIR` to point elsewhere; with no `index.html` there the dashboard is
simply not served.

## Develop

```bash
cd web
npm ci
npm run dev        # proxies /v1 and /auth to JARVIS_LIVE_URL (default http://127.0.0.1:8000)
npm test           # vitest: reducer driven by protocol/v1/fixtures/valid/*
npm run build      # typecheck + vite build -> dist/
npx playwright test
```

## Layout

| Path | What |
|---|---|
| `src/reducer.ts` | Pure `(state, event) => state` for every server event type |
| `src/api.ts`, `src/ws.ts` | REST client (cookie auth), viewer WebSocket with reconnect |
| `src/views/` | Sessions list, live and past session, devices, admin, shared panes |
| `src/jarvis/` | The Jarvis components over Instrument's rules: TranscriptLine, MarkerRow, NowDock/SuggestionCard, CopilotItem, RelatedNoteCard, ConnectionChip, SessionRow, SessionHeader, WorkItemCard/RunBlock, UploadForm, Timeline (the feed), FeedFilter, JumpPill. `dock.ts` is the Now dock's rules |
| `src/feed.ts`, `src/follow.ts`, `src/look.ts`, `src/dismissals.ts` | Feed order and "new" marks, auto-follow, "since you looked", per-session suggestion dismissals (localStorage) |
| `src/ds/components/` | Thin Preact wrappers over Instrument's classes (Shell, Button, Banner, Table, Dialog, …). Views use these and `src/jarvis/`, never raw `i-*` / `j-*` class strings |
| `src/ds/ds.css` | Layout glue and two specificity fixes, from `--inst-*` tokens only |
| `src/instrument/` | **Managed** copy of [Instrument](https://github.com/lordmuffin/instrument), the family design system. Never edit it: the *Instrument* CI check fails on hand edits; changes go upstream and arrive as a sync PR |
| `src/theme.ts` | System / Light / Dark choice, saved in `localStorage` (guarded; it can throw) |
| `tests/` | vitest: reducer (events from the protocol fixtures), API client, routes |
| `e2e/` | Playwright smoke against the real server (`server/tests/e2e_server.py`) |

Reducer rules: final segments replace overlapping drafts on the same channel; a `copilot`
snapshot replaces state only when its `version` is higher; the REST snapshot and the stream
may overlap freely.

Layout: two panes from 840 px, tabs below. Look and feel come from Instrument (`.inst` on the
app root, `--inst-*` tokens, light and dark both first-class). Colours follow
`prefers-color-scheme` until the header's System / Light / Dark control overrides it with
`data-theme` on `<html>`. No hard-coded colours, font stacks or inline styles in `src/` outside
`instrument/` (`tests/no-hardcoded-style.test.ts` enforces it). The artboards this is built from
are in [`docs/design/instrument/web/`](../docs/design/instrument/web).

## Playwright smoke

`playwright.config.ts` starts `server/tests/e2e_server.py` (the real app with `FakeSTT` /
`FakeLLM`, serving `web/dist`, plus a fake Pocket-ID on the next port whose page lists the test
users alice, bob, root (admin) and mallory). The tests sign in by clicking a user; one streams
a generated WAV as alice through `jarvis-live replay` and checks the live and past views
(recording included), others check that bob can't see alice's session, the Devices page, and
the admin views. `e2e/feed.spec.ts` drives the live feed from a scripted session (`e2e/live-fixture.ts`: mocked REST and a mocked viewer WebSocket). `e2e/screenshots.spec.ts` also writes every page (login, sessions, live, past, note editor, publishing, work items, devices, admin) in
light and dark to [`screenshots/`](screenshots). It needs Postgres: set `JARVIS_LIVE_TEST_DATABASE_URL`, or have Docker
available for a testcontainer. Run `npm run build` first.

## Image

The image build context is `server/`, so the web sources are passed as a named context:

```bash
docker build -t jarvis-live:web \
  --build-context webctx=web --build-arg WEB_SRC=webctx \
  --build-arg WEB_DIST_SOURCE=web-build -f server/Dockerfile server
```
