# jarvis-voice

## Layout

| Path | What |
|---|---|
| `protocol/v1/` | Wire protocol spec, JSON Schemas, fixtures (shared by all components) |
| `server/` | Jarvis Live backend (Python 3.12, uv, FastAPI), package `jarvis_live` |
| `server/deploy/k8s/` | Kubernetes manifests Flux applies to the homelab cluster: `base/` shared, `jarvis-live/` = live channel (branch `deploy/live`), `jarvis-live-lab/` = lab channel (branch `deploy/lab`). See `server/deploy/README.md` |
| `web/` | Web dashboard (viewer): Vite + TypeScript + Preact, served by the server at `/` |
| `macos/` | Jarvis Live macOS app (Swift, 0.12.0): `JarvisVoiceMac`, a SwiftUI menu-bar app with a live-session recorder (mic + system audio), a Copilot panel, a dictation HUD and a settings window, on the `JarvisLiveKit` Swift package. A working app, styled with Instrument (`Color.inst.*`, `InstType`), see `macos/README.md` |
| `android/` | Android app (`app/`), the Wear OS companion (`wear/`, sync code shared in `wearcommon/`: see `android/README.md`) |
| `design/` | Android-era token extraction (`tokens.json`, usage rules). The design system's source of truth is [lordmuffin/instrument](https://github.com/lordmuffin/instrument); its pinned release is vendored (managed, never edit) into `web/src/instrument/` and `macos/JarvisVoiceMac/Sources/Instrument/`, artboards in `docs/design/instrument/` |
| `src/jarvis_voice/`, `tests/` | **Legacy** FastAPI service — bugfix only |

## Running tests

- Server: `cd server && uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
- Legacy Python: `pip install -r requirements.txt -r requirements-dev.txt && python -m pytest -q tests/`
- Web: `cd web && npm ci && npm test && npm run build && npx playwright test` (Playwright needs Postgres: Docker, or `JARVIS_LIVE_TEST_DATABASE_URL`)
- Android: `cd android && ./gradlew test` (includes the Paparazzi screenshot checks; after an intended UI change run `./gradlew recordPaparazziDebug` and commit the images)
- macOS: `cd macos/JarvisLiveKit && swift test` (consumes `protocol/v1/fixtures`); the app itself: see `macos/JarvisVoiceMac/TESTING.md`.
- Workflow lint: `uvx --from actionlint-py actionlint .github/workflows/*.yml`

## Rules

- UI uses design-system tokens and components only. Never hard-code colours, sizes or fonts. If a component is missing, propose it for lordmuffin/instrument instead of building a local one. Enforced by the *Design lint* workflow (`make lint-design`: stylelint in `web/`, `scripts/lint/check_android_design.py`, SwiftLint in `macos/`); a justified exception needs an inline ignore with a reason (`design-lint:ignore` / `swiftlint:disable`).
- Follow `TDD.md` for every change. Run `make test` before declaring any task done (`make test-all` adds Postgres integration, coverage/eval ratchets, Playwright, Android and Swift; see `TESTING.md`).
- Commits and PR titles use Conventional Commits (`feat:`, `fix:`, `chore:`, with scope e.g. `feat(server):`). release-please derives versions and changelogs from them.
- `src/jarvis_voice/` is legacy — bugfix only. New work goes in `server/`.
- `protocol/` changes need fixtures + both server and Swift tests updated, in the same PR.
- Releases are per component, tagged `server-vX.Y.Z`, `macos-vX.Y.Z`, `android-vX.Y.Z`. Only `android-v*` tags build the APK.
- `web` is a release component linked to `server` (release-please `linked-versions`): the dashboard ships inside the server image, so a `feat`/`fix` under `web/` releases both at one version, and the `server-v*` tag promotes it to live. `web-v*` tags trigger nothing.
- jarvis-live has two release channels. Every push to `main` deploys to **lab** (`live.lab.apj.dev`; the workflow moves `deploy/lab`). Merging the release PR promotes to **live**: the `server-v*` tag waits until lab has the commit, retags lab's image (no rebuild) and moves `deploy/live`. Ship manifest changes (new settings, volumes) in `server/deploy/k8s/base/`, in the same PR as the code that needs them. Lab must never get a vault write credential or publishing (Deploy CI checks). Never hand-edit the image line or the lab `newTag`, and never push to `deploy/live` or `deploy/lab`; redeploy or roll back either channel with the *Server Image* workflow's "Run workflow". The repo is public: environment-private values stay in np-home-homelab.
