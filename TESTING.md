# Testing

How this repo follows [`TDD.md`](TDD.md). Component commands are in [`CLAUDE.md`](CLAUDE.md); this page is
what is tested, how to run it, and where the gaps are.

## Commands

| Command | What | Needs | Runtime |
|---|---|---|---|
| `make test` | Fast suite: lint (ruff, mypy, tsc, actionlint), server unit tests, legacy tests, web unit tests, deploy render + kubeconform + conftest | `make deps` once; network for tool/schema downloads; **no Docker** | ~30 s |
| `make test-all` | `make test` + server integration (Postgres) with coverage, legacy and web coverage, Playwright, Android unit tests, Swift tests, ratchets | Docker **or** `JARVIS_LIVE_TEST_DATABASE_URL`; JDK + `ANDROID_HOME`; macOS for Swift | ~3 min |
| `make lint` | Formatters and linters only | | ~10 s |
| `make ratchet` | Compare coverage/eval numbers with the baselines (`RATCHET_FLAGS=--update` raises them) | coverage reports from `test-all` | instant |

CI: `.github/workflows/tdd.yml` runs `make test` on PRs and `make test-all` on `main` and nightly. The per-component,
path-filtered workflows (`server-ci`, `web-ci`, `python-ci`, `macos-ci`, `deploy-ci`, `build`) are unchanged.

## What is tested, by tier

1. **Lint / static:** ruff + strict mypy (server), `tsc --noEmit` (web), actionlint (workflows).
2. **Unit and smoke:** pytest for `server/` and the legacy service (every module imports, the CLI answers `--help`), Vitest for `web/`.
   Component tests render views into a DOM with `happy-dom`, opted in per file (`// @vitest-environment happy-dom`;
   the rest stay on `node`). First user: `web/tests/workitems-view.test.ts` (the work item review cards).
   Server tests that need Postgres are marked `integration` automatically (`server/tests/conftest.py`), so `-m "not integration"` is the Docker-free fast suite.
3. **Integration / e2e:** server tests against Postgres (testcontainers), Playwright against the real app serving `web/dist`.
4. **Deploy manifests (GitOps):** both overlays (`jarvis-live`, `jarvis-live-lab`) are rendered with kustomize, validated with
   `kubeconform -strict` (CRD schemas from the datree catalog), and checked by conftest policies in `server/deploy/policy/`:
   no `:latest`/untagged images, requests and limits, no plaintext `Secret`, `prune: disabled` on PVCs and Namespaces, pinned
   `HelmRelease` charts, Traefik `websecure` + TLS + `traefik-traefik` Ingresses, and the channel rules (lab: no vault write
   credential, no publishing, own namespace/database/host). The policies have their own tests (`*_test.rego`, `conftest verify`).
   `deploy-ci.yml` still runs its original shell checks alongside.
5. **Evals:** `server/tests/evals` (copilot evals). Offline cases run always; `--live` cases need a real endpoint.
6. **Not here:** `flux-local` (the Flux Kustomizations live in np-home-homelab) and prompt/skill linting (this repo has no skills).
   Android (`./gradlew testDebugUnitTest`) and Swift (`swift test`) run in `make test-all`/CI and are skipped locally without a JDK + Android SDK / macOS.

## Baselines (recorded 2026-10-12)

| Ratchet | Baseline | File |
|---|---|---|
| Server branch coverage | 92.5 % (raised from 88.9 on 2026-10-12: see below) | `server/.coverage-baseline` |
| Legacy service branch coverage | 23.6 % | `.coverage-baseline` |
| Web line coverage | 36.5 % (raised from 26.9 on 2026-10-12) | `web/.coverage-baseline` |
| Server eval cases (collected) | 7 (4 offline pass, 3 `--live`) | `server/.eval-baseline` |

CI fails if a number drops. Raise baselines when coverage rises; never lower one without approval.

Server coverage counts greenlets (`concurrency = ["thread", "greenlet"]` in `server/pyproject.toml`). The API tests
drive the app in a uvicorn thread where SQLAlchemy's async layer switches greenlets. Before that setting, coverage lost
track of code reached only through the server: `publish/service.py` read 72 % and `workitems/service.py` 60 % although
the tests run nearly all of it. The first `make test-all` on `main` after the work items merge failed the ratchet
(88.1 < 88.9) for that reason. Measured properly the total is 92.7 %; the baseline is 92.5 to leave room for run-to-run
noise.

Counts at baseline: server 310 passed / 4 skipped (the `--live` tests) of which 214 run without Docker; legacy 28; web unit 40;
Playwright 7; policy tests 28; rendered resources 13 (live) and 12 (lab), all valid.

## Waivers

- **Policy waivers (`server/deploy/policy/exceptions.yaml`):** none. Both overlays pass every baseline policy as shipped.
- **Secrets:** the manifests contain no `Secret` objects, only `OnePasswordItem` references, so the "SOPS-encrypted" rule is
  satisfied by there being none to encrypt. Nothing was decrypted or read.
- **Known flaky test (not waived, not changed):** `server/tests/test_copilot_loop.py::test_never_concurrent_and_cancels_on_session_end`
  asserts `rig.llm.active == 1` after a 10 ms real sleep against a 50 ms fake LLM delay. It failed once in one full run under coverage
  load (passed in 5 isolated runs and 3 other full runs). Proposed fix, in its own RED step: drive it with the rig's fake clock instead
  of `asyncio.sleep`. Tagged `TODO(tdd)`.
- **Web unit coverage** counts every `.tsx` view. Most are exercised only by the Playwright flows, which do not feed coverage,
  so the web baseline is low. The work item views have component tests; the rest do not yet.
- **Not run in this setup session:** Android unit tests and Swift tests (no Android SDK / not macOS). The `make test-android` command
  mirrors `build.yml`, but it was not executed here.

## Top 5 untested risk areas

1. **Legacy `src/jarvis_voice/vault.py` + `vault_tools.py` (11 % / 16 %).** They read and write the notes vault, so path escape or data loss
   is the risk. First test: a characterization test (`test_current_behavior_...`) that a path outside the vault root is or isn't rejected.
2. **Legacy `capture_api.py` (29 %).** An authenticated HTTP API that accepts uploads. Only auth is covered. First test: pin each route's
   status with a missing, wrong and correct key, then oversize and wrong-type uploads.
3. **`server/src/jarvis_live/publish/service.py` (83 %, 51 missed lines) and `api/publishing.py`.** This path writes to the GitHub vault with
   the credential lab must never hold. First test: using `fake_github`, assert behavior when the branch already exists, the token is
   rejected (403), and the merge conflicts.
4. **Web views (`web/src/views/*.tsx`, 0 % unit).** Device-token-shown-once and revoke, admin tables, publishing UI have only the e2e flows.
   First test: Vitest + `@testing-library/preact` for `Devices.tsx` (token shown once, revoke removes the row).
5. **Deploy hardening and data deletion.** Pod `securityContext` (non-root, read-only root fs), the vault-clone CronJob and PVC reclaim
   behavior are not asserted. First test: a conftest rule requiring `runAsNonRoot` and `allowPrivilegeEscalation: false` on every container.

## Legacy code

`src/jarvis_voice/` is bugfix-only. Before changing any untested unit there, first write characterization tests that pin its
current behavior (name them `test_current_behavior_...` and list them here), then follow the RED/GREEN loop (TDD.md §7 Phase 3).
