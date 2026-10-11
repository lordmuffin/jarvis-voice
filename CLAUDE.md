# jarvis-voice

## Layout

| Path | What |
|---|---|
| `protocol/v1/` | Wire protocol spec, JSON Schemas, fixtures (shared by all components) |
| `server/` | Jarvis Live backend (Python 3.12, uv, FastAPI), package `jarvis_live` |
| `server/deploy/k8s/` | Kubernetes manifests Flux applies to the homelab cluster: `base/` shared, `jarvis-live/` = live channel (branch `deploy/live`), `jarvis-live-lab/` = lab channel (branch `deploy/lab`). See `server/deploy/README.md` |
| `web/` | Web dashboard (viewer): Vite + TypeScript + Preact, served by the server at `/` |
| `macos/` | Jarvis Live macOS app (Swift) — scaffold only so far |
| `android/` | Android app |
| `design/` | Design system: `tokens.json` (colors, type, spacing, radius, sizes) and `README.md` usage rules, extracted from the Android app |
| `src/jarvis_voice/`, `tests/` | **Legacy** FastAPI service — bugfix only |

## Running tests

- Server: `cd server && uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
- Legacy Python: `pip install -r requirements.txt -r requirements-dev.txt && python -m pytest -q tests/`
- Web: `cd web && npm ci && npm test && npm run build && npx playwright test` (Playwright needs Postgres: Docker, or `JARVIS_LIVE_TEST_DATABASE_URL`)
- Android: `cd android && ./gradlew test`
- macOS: Swift tests (to be added with the app); must consume `protocol/v1/fixtures`.
- Workflow lint: `uvx --from actionlint-py actionlint .github/workflows/*.yml`

## Rules

- Commits and PR titles use Conventional Commits (`feat:`, `fix:`, `chore:`, with scope e.g. `feat(server):`). release-please derives versions and changelogs from them.
- `src/jarvis_voice/` is legacy — bugfix only. New work goes in `server/`.
- `protocol/` changes need fixtures + both server and Swift tests updated, in the same PR.
- Releases are per component, tagged `server-vX.Y.Z`, `macos-vX.Y.Z`, `android-vX.Y.Z`. Only `android-v*` tags build the APK.
- jarvis-live has two release channels. Every push to `main` deploys to **lab** (`live.lab.apj.dev`; the workflow moves `deploy/lab`). Merging the release PR promotes to **live**: the `server-v*` tag waits until lab has the commit, retags lab's image (no rebuild) and moves `deploy/live`. Ship manifest changes (new settings, volumes) in `server/deploy/k8s/base/`, in the same PR as the code that needs them. Lab must never get a vault write credential or publishing (Deploy CI checks). Never hand-edit the image line or the lab `newTag`, and never push to `deploy/live` or `deploy/lab`; redeploy or roll back either channel with the *Server Image* workflow's "Run workflow". The repo is public: environment-private values stay in np-home-homelab.
