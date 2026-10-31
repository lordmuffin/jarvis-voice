# Plan: Live observability (latency metrics, SLOs, alerts, per-session timing)

Status: **approved 2026-10-30 (all five recommendations), implemented on `feat/observability`; see "As built" at the end.**
Branch: `feat/observability`. Budget source: vault note `10 Projects/Jarvis/2026-10-09 - Handoff - Jarvis Live.md`, "Latency budget" table (it is not in this repo).

## Goal

Know Live is getting slow before it shows up mid-meeting, and have numbers for tuning STT tiers.

## What I read, and what it means for the design

| Budget row (handoff) | Where it happens in code | Consequence |
|---|---|---|
| Draft transcript < 0.5 s, **on device** | Client sends `draft_segment` (`final=false`); `api/stream.py:210-214` relays it, stores only `final=true` | The server can't measure the on-device latency. It can measure **arrival cadence** and **arrival lag** (see "Honest limits"). |
| Authoritative line 2–15 s = VAD close (≤15 s) + Whisper (~1 s) | `SessionPipeline._enqueue` → `_process` → STT → dup-guard hold → `_emit` (`ingest/pipeline.py`) | The segment length is the user's speech, not a fault. Measure from **segment cut** to **persisted+published**, and split out STT request time and dup-guard hold. |
| Copilot refresh ~25–35 s (poll 1 s, min-segments or `copilot_max_wait_s=25`, then LLM) | `CopilotLoop.tick/_cycle` (`copilot/loop.py`) | Two numbers: LLM round trip (`complete_json`), and **staleness** = first unseen final → snapshot published. |
| Final note ≤ 2 min after stop | `Orchestrator._end` → ideas flush → `Finalizer.run`; `Job.started` already exists (`_track`) | Time from session end to note written, by job source (`finalize`/`regenerate`/`reprocess`). |
| STT tier | `TierPool.transcribe` returns `(text, tier)`; falls through on error and flips `_healthy` | Count by tier served, fall-throughs, per-tier request time, health gauge. |

Existing alerts I will **not** touch or duplicate: STT-outage (`Orchestrator._check_stt_outage`, once per outage after `stt_outage_notify_s`=120 s) and finalizer-failed (`Finalizer.run` → `_notify_owner`, "Jarvis: note failed"). The new SLO monitor suppresses itself while `current_tier() is None` and only looks at *completed* finalizer runs.

Nothing in the repo uses Prometheus today. `server/pyproject.toml` has no client lib. The Dockerfile runs a single uvicorn process and the handoff pins a single replica, so the default `prometheus_client` registry is correct (no multiprocess mode).

## Metrics (all `jarvis_live_*`)

Labels are drawn only from small closed sets: `channel` (mic|system), `tier` (names from `WHISPER_TIERS`, so config-bounded), `outcome` (ok|error|timeout), `source` (finalize|regenerate|reprocess). **Never** `session_id`, user, or title. The live/lab split comes from Prometheus target labels (namespace), not from a metric label.

| Metric | Type | Labels | Measured |
|---|---|---|---|
| `draft_first_seconds` | histogram | – | first audio frame → first partial draft received, per session |
| `draft_interval_seconds` | histogram | channel | gap between successive partial drafts (cadence) |
| `draft_lag_seconds` | histogram | channel | `position_ms` of ingest − draft `end_ms` at arrival (proxy; see limits) |
| `segment_seconds` | histogram | channel | speech segment length, so a long segment isn't mistaken for slowness |
| `stt_request_seconds` | histogram | tier, outcome | one Whisper HTTP call (`TierPool._request`) |
| `stt_served_total` | counter | tier | segments finally transcribed by this tier ("how often each tier is hit") |
| `stt_fallthrough_total` | counter | tier | tier tried and failed, next one used |
| `stt_tier_healthy` | gauge | tier | probe state; plus `stt_current_tier_info{tier}` 1-hot |
| `authoritative_seconds` | histogram | channel | segment cut → `_emit` done (queue + STT + hold + DB commit) |
| `dup_hold_seconds` | histogram | – | mic segment held for the system channel (`duplicate_hold_ms` is 20 s, so this can dominate) |
| `lag_ms_max` | gauge | – | max `lag_ms()` over live sessions (the value already sent in `Status`) |
| `copilot_roundtrip_seconds` | histogram | outcome | `llm.complete_json` |
| `copilot_staleness_seconds` | histogram | – | first unseen final → snapshot published |
| `finalize_seconds` | histogram | source, outcome | session end → note written |
| `live_sessions` | gauge | – | for the dashboard |
| `slo_breach` | gauge | slo (+ tier where relevant) | 1 while the monitor considers it breached (state only; see Alerting) |

Bucket sets are chosen around the budget edges (for example authoritative: 0.5, 1, 2, 3, 5, 8, 12, 15, 20, 30, 60 s; finalize up to 600 s). Series count is at most 385 per instance with the 3 real tiers (counted from the registry). The cluster's Prometheus remote-writes to Mimir, which has a per-user series limit (comment at `kube-prometheus-stack/release.yaml:~149`), so I'll check the headroom before merging.

### Keeping it off the hot audio path

- **No instrumentation in `SessionPipeline.feed()` or the audio branch of the WebSocket loop** (`stream.py` `ing.pipeline.feed`). That path stays untouched.
- Everything is recorded after the work it measures, in places that already `await` network or DB: `_process`, `_emit`, `TierPool.transcribe`, `CopilotLoop._cycle`, `Finalizer.run`, and the *text-message* branch of the WS loop (draft frames, a few per second, not audio).
- Cost per observation is one `time.monotonic()` and a counter/histogram update (microseconds, no I/O). A segment's cut timestamp is a single float stored beside the segment at `_enqueue`.
- A test enforces it: `feed()` is called 10,000 times with metrics enabled and disabled, and the test fails if the per-call delta exceeds a small bound (same style as the macOS meter benchmark in `docs/plans/recording-ux.md`). A second test asserts `feed()` and the audio branch import/touch nothing from `jarvis_live.metrics`.
- Metric helpers never raise into callers (like `GotifyNotifier`).

## Exposure

- Add `prometheus_client` to `server/pyproject.toml` (**needs your OK**: the one new runtime dependency; the alternative is a ~80-line hand-rolled text exposition, which I don't recommend).
- `GET /metrics` on the app port. Auth middleware currently exempts only `healthz`/`readyz` (`app.py:~69`); `/metrics` must be added to that allow-list **and** never routed by either channel's Ingress. Scrape is in-cluster via Service :8080. I'll confirm the live/lab Ingresses route only the intended paths and whether the namespaces have a default-deny NetworkPolicy that blocks the Prometheus scrape (unchecked so far).
- Manifests in `server/deploy/k8s/base/` (per CLAUDE.md, same PR as the code): add the Service port name and settings below to the ConfigMap. The `PodMonitor` (needs `release: kube-prometheus-stack`) lives in np-home-homelab.

## SLOs (proposed numbers: please correct)

The handoff gives targets, not objectives. I'm proposing thresholds that make the budget operational. Everything here is a config setting, so tuning needs no deploy.

| SLO | SLI | Proposed threshold | Basis |
|---|---|---|---|
| Authoritative text | `authoritative_seconds` (cut→emitted), rolling p95 | ≤ 5 s | Handoff says Whisper ~1 s and 2–15 s overall including the ≤15 s segment; 5 s leaves room for queue + dup hold. **Weakest guess: dup hold can legitimately reach 20 s, so mic is measured excluding hold.** |
| STT tier health | `stt_request_seconds{tier}` rolling p95 | ≤ 4 s per tier | ~1 s expected, 4× headroom; this is the signal the throttled-tier test trips |
| Copilot freshness | `copilot_staleness_seconds` rolling p95 | ≤ 35 s | Handoff 25–35 s |
| Copilot round trip | `copilot_roundtrip_seconds` rolling p95 | ≤ 8 s | The 8 s eval-harness budget in `docs/plans/copilot-to-claude-code.md:33` |
| Final note | `finalize_seconds` (completed runs only) | ≤ 120 s | Handoff ≤ 2 min; failures stay the existing finalizer-failed alert |
| Draft | `draft_lag_seconds`, `draft_interval_seconds` | **dashboard only, no alert** | On-device budget not measurable server-side |

**Sustained** = the SLI breaches over a rolling window (default 5 min) with at least N samples (default 8), evaluated only while a live session exists.

## Alerting (reuses the Gotify notifier)

- New `jarvis_live/slo.py`: `SloMonitor`, evaluated from the existing `Orchestrator.check_once` watchdog (every 10 s, already injectable `Clock`), so it's testable with the fake clock and uses the existing `Notifier`. The rolling windows are small in-memory deques fed alongside the metric observations, because a Prometheus histogram can't give windowed quantiles in-process.
- State machine per `(slo[, tier])`, same shape as `_outage_since/_outage_notified`: OK → BREACHED sends **one** notification (priority 5, title prefixed "Jarvis: slow …", `[LAB]` prefix already comes from the notifier) → re-arms only after the SLI has been healthy for a full clear window (default 10 min). **No recovery push**, so a breach is exactly one alert; the `slo_breach` gauge shows state on the dashboard.
- Suppressions: no live session; `current_tier() is None` (outage alert owns that); the tier under test has fewer than N samples.
- Why not Alertmanager rules: the request is for Gotify via the existing notifier, and shipping PrometheusRules in parallel would double-page. If you'd rather have Alertmanager, say so and the SLO-as-recording-rules variant is a small change.
- Settings (all in config + `base/configmap.yaml`): `slo_enabled`, window, min samples, clear window, one threshold per SLO above.

## Per-session timing summary in the final note

- In-memory `SessionTimings` per session (tiny aggregates, **not labels**): segment count, tier counts, authoritative p50/p95/max, STT p95 per tier, fall-throughs, max `lag_ms`, copilot cycles ok/failed, round-trip p95/max, staleness p95, draft first-partial and cadence, finalize duration.
- Restart-safety (decision for you): in-memory aggregates are lost if the pod restarts mid-session. Recommended: add nullable `stt_ms` and `latency_ms` to `segments` (one alembic migration; `_emit` already commits a row, so no extra write), and write one `CopilotItem(kind="timing")` per copilot cycle (existing table, no migration; `_restore` filters by kind so it's unaffected). The summary is then computed from the DB and survives restarts and `regenerate`. Cheaper alternative: in-memory only, summary marked "partial (restart)" when a restart is detected.
- Rendered by `finalize/render.py` as a "Session timing" section at the end of the note: a compact table plus a one-line **verdict** that names breaches in plain words ("authoritative p95 9.1 s > 5 s; tier `fast` served 82%, 3 fall-throughs"). Numbers only, no transcript content. Tests: render golden, note still parses via `note_items.py`, vault-writer/web viewer unaffected.

## Dashboard (np-home-homelab)

The cluster **is reachable** (kubectl works; `jarvis-live` and `jarvis-live-lab` namespaces exist, lab pod Running). Pattern found in the repo: a ConfigMap labelled `grafana_dashboard: "1"` **in the `observability` namespace** (sidecar doesn't search other namespaces), as in `apps/ai/mcp-status/dashboard-configmap.yaml`; scraping via a `PodMonitor` with `release: kube-prometheus-stack` as in `apps/observability/flux-alerts/podmonitor.yaml`.

- Dashboard JSON authored and committed **in this repo** too (`server/deploy/grafana/jarvis-live.json`), so the deliverable doesn't depend on the cluster. Panels: SLO status strip, authoritative p50/p95 vs budget line, STT tier share (stacked) + per-tier request p95 + fall-throughs, copilot round trip and staleness, finalize duration, draft cadence/lag, live sessions. Variable: namespace (live/lab).
- homelab change on a branch from `forgejo/main` in a fresh worktree (the working copy at `~/git/np-home-homelab` is on `fix/homarr-cnpg-role` with untracked files; I won't touch it), PodMonitor for both namespaces plus the dashboard ConfigMap, opened as a PR on Forgejo (source of truth, per memory). I won't `kubectl apply`: Flux reconciles it.
- This depends on the server release, so the PodMonitor would scrape nothing until lab runs the new image. The ordering is server PR → lab deploy → homelab PR.

## Honest limits

- "Time to first partial": the partial is produced on device, so the true < 0.5 s number can't be measured here. Server-side I record first-partial-since-first-audio, cadence and ingest-position lag. If you want the real figure, `draft_segment` needs a client timestamp, which is a `protocol/v1` change (fixtures + server + Swift in one PR, per CLAUDE.md). I'm leaving it out of scope unless you say otherwise.
- Authoritative-line SLO excludes segment length by design; a user speaking for 15 s will always have a 15 s line.
- SLO numbers are my proposals from the handoff's budget; the throttled-tier alert in particular depends on the 4 s tier threshold.
- I haven't read the remainder of `replay.py`; if it doesn't send drafts, I'll add an opt-in synthetic-draft mode so a replay produces the draft metrics.

## Delivery order (TDD.md red→green→refactor per step; `make test` before done)

1. **Metrics module + `/metrics`** (`jarvis_live/metrics.py`, no-raise helpers, bounded labels). Tests: registry contents, label-set guard test (fails if any metric declares a label outside the allow-list), `/metrics` auth/exposure, hot-path benchmark and no-import test.
2. **Instrument the path**: tier pool, pipeline cut→emit and hold, drafts in `stream.py`, copilot cycle/staleness, finalize duration, session gauge. Each with a fake-clock/fake-STT test.
3. **SLO monitor + Gotify alert**, wired into `check_once`. Tests: breach fires once, stays silent while breached, silent on short window / few samples / outage, re-arms after clear.
4. **Per-session timing** (migration if approved) **+ note section**.
5. **Dashboard JSON + manifests** (`base/` in this repo), then the homelab PR.
6. **Docs**: `server/deploy/README.md` metrics/SLO section, `TESTING.md` entry.

Each of 1–4 is a `feat(server):` commit; 5 is `feat(server):` too (deploy assets), and the homelab change gets its own PR.

## Done-when check (acceptance test)

An integration test (in `make test`, no cluster needed): app with fake Whisper tier servers (ASGI transport), replay of an existing fixture WAV via `ReplayProducer` at speed, fake Notifier and fake clock.

1. **Full metric set**: scrape `/metrics`; assert every metric above has a non-zero count (labels where applicable) and that no series carries a `session_id`.
2. **Throttle**: make one tier slow (delay above the threshold) and continue replaying past the window; assert the Notifier received **exactly one** SLO notification for that tier, then keep replaying and assert still one; assert none of the existing STT-outage/finalizer-failed titles fired.
3. Final note contains the "Session timing" section with the tier breach in its verdict.

Then a manual run against lab after the lab deploy: replay a WAV, confirm the dashboard populates, throttle via a Spark profile flip or a deliberately slow tier entry, and confirm one push. I'll only report that as verified if I actually do it.

## Decisions I need from you

1. OK to add `prometheus_client`?
2. SLO thresholds above (especially authoritative 5 s and tier 4 s)?
3. Per-session timing: DB-backed (migration, recommended) or in-memory only?
4. In-process Gotify alerts (as scoped) vs Alertmanager rules?
5. Out of scope: client timestamp on drafts (protocol change). Agree?

## As built (differences from the plan above)

- **Metrics are on their own port (`JARVIS_LIVE_METRICS_PORT`, 9100), not `GET /metrics` on the app port.** Both channels' Ingress routes `/` (Prefix) to port 8080, so a route there would have been public. The Service exposes `metrics`; the Ingress does not.
- **The note shows "Stop to note rendered", not the finalize duration**: a note cannot contain the time it took to write itself. The exact `finalize_seconds` stays on the dashboard. Draft first-partial/cadence and fall-through counts are in the metrics, not in the note (they are not persisted); the note carries tier mix, per-tier STT p95, line latency, copilot cycles/round trip/staleness.
- **Alert de-duplication is by cause**: a breached tier SLO silences the transcript SLO's push (its gauge still shows), and a slow copilot round trip silences copilot staleness. Without this, one throttled tier produced two pushes.
- **Finalize SLO** uses the median of the last runs over six hours with at least two, i.e. two slow notes in a row, because there are too few events for a rolling p95.
- **Test seams added**: `watchdog_interval_s` setting; `jarvis-live replay --drafts` (synthetic partial drafts).
- Verified here: `server/` ruff, mypy and the full pytest suite (596 passed, Postgres included); the acceptance test fails if the tier is not throttled; every dashboard PromQL expression parses against the cluster's Prometheus; homelab `tests/tdd`, kubeconform (0 invalid), `make lint`.
- **Not verified**: anything on a running lab (nothing is pushed or deployed), so the PodMonitor scrape and the populated dashboard are untested live. `make test` at the jarvis-voice root could not complete on this machine (legacy tests need `faster_whisper`, `lint-android` needs a system `pytest`); the homelab `make test` unit step fails on `jev` for a missing `prometheus_client` in the shared venv, unrelated to this change.
