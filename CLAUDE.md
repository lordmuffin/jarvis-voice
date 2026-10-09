# jarvis-voice

## Layout

| Path | What |
|---|---|
| `protocol/v1/` | Wire protocol spec, JSON Schemas, fixtures (shared by all components) |
| `server/` | Jarvis Live backend (Python 3.12, uv, FastAPI), package `jarvis_live` |
| `macos/` | Jarvis Live macOS app (Swift) — scaffold only so far |
| `android/` | Android app |
| `design/` | Design system: `tokens.json` (colors, type, spacing, radius, sizes) and `README.md` usage rules, extracted from the Android app |
| `src/jarvis_voice/`, `tests/` | **Legacy** FastAPI service — bugfix only |

## Running tests

- Server: `cd server && uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
- Legacy Python: `pip install -r requirements.txt -r requirements-dev.txt && python -m pytest -q tests/`
- Android: `cd android && ./gradlew test`
- macOS: Swift tests (to be added with the app); must consume `protocol/v1/fixtures`.
- Workflow lint: `uvx --from actionlint-py actionlint .github/workflows/*.yml`

## Rules

- Commits and PR titles use Conventional Commits (`feat:`, `fix:`, `chore:`, with scope e.g. `feat(server):`). release-please derives versions and changelogs from them.
- `src/jarvis_voice/` is legacy — bugfix only. New work goes in `server/`.
- `protocol/` changes need fixtures + both server and Swift tests updated, in the same PR.
- Releases are per component, tagged `server-vX.Y.Z`, `macos-vX.Y.Z`, `android-vX.Y.Z`. Only `android-v*` tags build the APK.
