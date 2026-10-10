# Jarvis Live web dashboard

A read-only viewer for [`server/`](../server): watch a session live (transcript, notes,
actions, decisions, suggestions, related) or browse past ones. Vite + TypeScript + Preact +
`@preact/signals`. No external fonts or CDNs; everything is served by the jarvis-live process.

There is no browser-side recording: producers are the Mac app and, later, Android.

## Use

Sign in with a device token (`jarvis-live create-device --name browser`). Any of your device
tokens sees every session, whichever device recorded it. The token is kept in `localStorage`
and sent as a bearer token; WebSocket access uses short-lived **viewer** tickets.

The FastAPI app serves `web/dist` at `/` (SPA fallback after the API routes). Set
`JARVIS_LIVE_WEB_DIST_DIR` to point elsewhere; with no `index.html` there the dashboard is
simply not served.

## Develop

```bash
cd web
npm ci
npm run dev        # proxies /v1 to JARVIS_LIVE_URL (default http://127.0.0.1:8000)
npm test           # vitest: reducer driven by protocol/v1/fixtures/valid/*
npm run build      # typecheck + vite build -> dist/
npx playwright test
```

## Layout

| Path | What |
|---|---|
| `src/reducer.ts` | Pure `(state, event) => state` for every server event type |
| `src/api.ts`, `src/ws.ts` | REST client, token storage, viewer WebSocket with reconnect |
| `src/views/` | Sessions list, live session, past session, shared panes |
| `tests/` | vitest; events built from the protocol fixtures |
| `e2e/` | Playwright smoke against the real server (`server/tests/e2e_server.py`) |

Reducer rules: final segments replace overlapping drafts on the same channel; a `copilot`
snapshot replaces state only when its `version` is higher; the REST snapshot and the stream
may overlap freely.

Layout: two panes from 840 px, tabs below; colours follow `prefers-color-scheme` using the
tokens in [`design/`](../design).

## Playwright smoke

`playwright.config.ts` starts `server/tests/e2e_server.py` (the real app with `FakeSTT` /
`FakeLLM`, serving `web/dist`), then the test streams a generated WAV through
`jarvis-live replay` and checks the live and past views. It needs Postgres: set
`JARVIS_LIVE_TEST_DATABASE_URL`, or have Docker available for a testcontainer. Run
`npm run build` first.

## Image

The image build context is `server/`, so the web sources are passed as a named context:

```bash
docker build -t jarvis-live:web \
  --build-context webctx=web --build-arg WEB_SRC=webctx \
  --build-arg WEB_DIST_SOURCE=web-build -f server/Dockerfile server
```
