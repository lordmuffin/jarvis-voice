# Local-only audit (task 6): findings report

> **Update: implementation started on `feat/local-only-gate`.** Decisions: restricted uploads are
> allowed, tagged and filed apart (Q1); manual starts keep streaming by default, NetBird or not
> (Q2, so F1's default is deliberately unchanged); the watch stays ungated and documented (Q3);
> the session-driver extraction was approved (Q4) but is **not done yet**. See "Status" at the end.

Status: **findings only, no code changed.** Task 6 in `Tasks.txt` says report first and stop.
Method: static reading of `macos/`, `android/`, `server/src/jarvis_live/`, `protocol/v1/` and
`server/deploy/k8s/`. **I ran no packet capture and no app**; everything below is what the code
says, not what a wire trace showed. Section 7 lists what I could not verify.

## 1. Verdict

**The toggle is not airtight, and it is weaker than the UI implies.** The core claim holds in
the narrow case: while a Mac or Android session is *running* with local-only on, the audio,
drafts and markers go to disk and nowhere else. That part is sound. Everything around it is
not:

1. The *default* streams (F1).
2. The server's local-only guards are dead code (F2).
3. Once you upload a local-only session, the server has no memory it was sensitive, and it
   flows into the LLM, Gotify and a GitHub PR (F2, F3).
4. The watch has no local-only concept at all (F4).
5. There is no transport-level gate; local-only is a call-site convention (F5).

## 2. Findings

Severity is about leaking employer call content, which is the stated worry.

| # | Sev | Finding |
|---|---|---|
| F1 | High | **Ambiguous defaults to streaming.** `AutoLocalOnly.localOnly = manual ?? (enabled && automatic == true)` (`AwayMode.swift:71`). With no network check yet (`place == nil`, i.e. right after launch), `automatic` is nil, so it is **false: stream**. At `.home` or `.netbird` it is false: stream. With "Local only away from home" switched off it is purely manual and defaults to stream. Android's `AutoLocalOnly` is the same shape. This is the opposite of 6(e). |
| F2 | High | **Server `local_only` is never set, so every server-side guard is unreachable.** `grep` finds no writer of `sessions.local_only`: not `create_session`, not `upload_recording`, not the stream endpoint, not `SessionCreateRequest`. Its six readers can never fire: `orchestrator._run_copilot` and `_ideas_eligible`, `finalizer._finalize`, `api/publishing._orchestrator`, `sessions.can_regenerate`, `processing.derive`. A Mac local-only session never creates a server row (`AppModel.prepare` skips `createSession`), so the server cannot tell. When you press Upload, `AppModel.upload` and Android's `LiveUploader` create an ordinary session, and the **local-only property is gone**. |
| F3 | High | **What an uploaded or streamed session reaches** (all from server config, all on by default in `base/configmap.yaml` unless noted): STT tiers (homelab); LiteLLM at `litellm-proxy-service.open-webui…:4000` for the copilot, ideas pass and final note; Gotify push with the note's **title and summary** as the body; the note and **full transcript** written to the outbox and, if published, **pushed as a PR to `lordmuffin/Notes` on GitHub** (`PUBLISH_REPO` is set; `auto_publish` is a per-user flag, default off); work-item prompts built from transcript quotes (`WORK_ITEMS_ENABLED=true`), which `dispatch` would hand to Claude Code on Anthropic (`DISPATCH_ENABLED=false` in base today). None of these checks a sensitivity flag, because none exists. |
| F4 | High | **Wear OS has no local-only.** `wear/` and `wearcommon/` contain no `localOnly`/`awayMode`. A watch recording syncs on its own schedule (`SyncScheduler`: after a recording, when a phone or network appears) and uploads via `POST /v1/sessions/upload` (`RecordingUploader`). If a work call is ever recorded on the watch, it uploads automatically. |
| F5 | Med | **No transport gate.** Network access is spread over many construction sites: `URLSession.shared` and `.ephemeral` (`LiveAPIClient`, `LiveConnection`, `NetworkPlaceDetector.probeSession`, `ServerInfo.fetch`) and OkHttp on Android. "Local-only" is `connection == nil` plus `if !localOnly` in `AppModel.prepare` and `LiveSessionService.startSession`. A new feature that builds its own client leaks by default. `LiveAPIClient.createSession` and `upload()` check nothing about local-only; the guard is that the caller does not call them. |
| F6 | Med | **Background requests continue during a local-only session.** On every path change, `recheckNetwork()` runs `ServerInfo.fetch` (`GET /healthz`), even when "Local-only away from home" is off, and `NetworkPlaceDetector` probes `/healthz` too. No token, no content, but it tells the server this device is online, from what address, while a work call is in progress. The menu toggle is labelled "Local-only (no network)", which is not literally true. First-run speech-model install (`AssetInventory`) also contacts Apple; it carries no user content and fails the start in local-only if it cannot download. |
| F7 | Med | **At rest.** Mac local transcripts go to `~/Documents/Jarvis Live/` (`LocalMarkdown.directory`). If *iCloud Drive › Desktop & Documents* is on for this Mac, local-only transcripts sync to iCloud. I cannot see that setting. Raw PCM is under `~/Library/Application Support/JarvisVoice/sessions` (not iCloud-synced; Time Machine would include it). Android is better: `allowBackup="false"`, audio in app-private `filesDir`; the local note goes to a user-chosen SAF folder if set, which could be a sync provider. |
| F8 | Med | **Indicator gap on the Mac.** The always-visible menu-bar icon takes `channel: serverChannel` and no local-only input (`MenuBarIconSpec.make`), so a local-only recording shows the same **LIVE/LAB** badge as a streaming one. "Local-only" appears only in the menu text and as a chip in the Copilot panel, which may be closed. Android is fine: the foreground notification says local vs streaming (`live_notif_local` / `live_notif_streaming`). |
| F9 | Low | Mid-session toggling is safe: `Run.localOnly` is fixed at start and the toggle only affects the next session. Android falls back to local on `IOException` at start, which is the safe direction, and tells the user. |
| F10 | Info | The toggle governs Live sessions only. Android also has dictation, Kai chat, voice chat, agent tasks and network TTS (`network_security_config` allows cleartext to three LAN IPs for LiteLLM and Kokoro). I did not trace those; they are not governed by this toggle. |

## 3. Every byte that leaves the client in local-only mode

Scope: a Live session running with local-only on, and the app around it.

| Path | Mac | Android | Leaves the device? |
|---|---|---|---|
| Audio frames | `ChannelPipeline` → `Outbox.append` (disk) when `connection == nil` | `outbox.append` | **No** |
| Partial/final text | on-device `SpeechAnalyzer`; `sendDraft` only `if let connection` | on-device Whisper; `connection?` | **No** |
| Markers | `sendMarker` only if `connection` | same | **No** |
| Copilot requests, vault lookups | server-side, session never reaches the server | same | **No** (while local) |
| Session title | `title` is a server-create field; not created | same | **No** |
| Telemetry / crash SDKs | none found: no Sparkle, Sentry, Crashlytics, Firebase, analytics in Swift sources, Gradle files or manifest | same | **No** from the app. OS-level crash reporting and Apple diagnostics are outside the app (not verified) |
| Gotify bodies | server-originated only | same | No (nothing to send) |
| `GET /healthz` (no token, no content) | yes, on path changes (F6) | yes, `isReachable`/`environment` probe | **Yes**, metadata only |
| Speech-model asset download | first run only, Apple | n/a (model bundled via `download-models.sh`) | **Yes**, Apple, no user content |
| Explicit Upload | user action | user action (`LiveUploadWorker.enqueue`) | **Yes**, everything, by design |

`LiveUploadWorker.enqueueInterrupted` skips local-only sessions (`meta.serverId != null`
only), so Android does not auto-upload them. Good.

## 4. Proposed enforcement (6b) and default policy (6e)

Not implemented. For approval.

**One gate per client, not a check per call site.**
- *Mac / `JarvisLiveKit`*: a single `NetworkGate` (actor) is the only way to get a
  `URLSession`. It installs a `URLProtocol` on every configuration it hands out and **denies
  all requests** while `state == .sessionLocal`. Outside a local session it allows only
  `GET /healthz`, calendar polling (task 5), and requests carrying an `UploadIntent` that the UI
  creates from the user's click on Upload. `URLSession.shared` is banned.
- *Android*: the same shape as one OkHttp client factory with an `Interceptor`; `HttpURLConnection`
  and `OkHttpClient(` banned elsewhere.
- *Static check*: a CI grep (next to `make lint-design`) fails on `URLSession(`/`.shared`/`OkHttpClient(`
  outside the factory, so a new feature cannot bypass the gate by forgetting to ask.
- While a local session runs, **zero** requests, including `/healthz` (fixes F6). The
  "no network" label then becomes true.

**Persist sensitivity so it survives upload (fixes F2/F3).** Replace the dead `local_only`
reads with one server field, `sessions.restricted` (bool), set by the client at create/upload.
When true, the server skips: ideas/work items, dispatch, GitHub publish and auto-publish,
Gotify bodies (title only), and `can_publish`. Whether it still runs the copilot and final note
on the homelab is **Q1**. The calendar `work` and `unknown` classes from the meeting-mode plan
set it.

**Default (6e).** `AutoLocalOnly`: before the first network check (`place == nil`) → local-only.
A calendar-started session with class `work` or `unknown` → local-only and not overridable from
that prompt. A manual start at `.home`/`.netbird` stays as today unless you want otherwise (**Q2**).

## 5. Proposed test (6c) and visibility (6d)

- **No-egress test, Swift.** Feed a PCM fixture through `ChannelPipeline` → `Outbox`, stop,
  write the local file, trigger the path-change recheck, with a stub `URLProtocol` that
  **fails the test on any request**. This needs a seam: `AppModel.start()` is `@MainActor` and
  owns real mic capture, so I would extract the session driver behind a `FrameSource` protocol
  (**Q4**; small refactor of `AppModel`). Allowed set is explicit and empty during the session,
  `GET /healthz` only when idle.
- **Same on Android** as a JVM test of the extracted driver plus an interceptor that fails.
- **Server:** a `restricted` session never reaches the LLM fake, Gotify fake, GitHub fake or
  dispatch fake; each is a separate test.
- **Gate unit tests:** denied while local; `UploadIntent` required for upload; lint check test.
- **Visibility:** the Mac menu-bar badge shows **LOCAL** (distinct, not LIVE/LAB) while a
  local session runs. The badge is Instrument-styled, so if the design system lacks a token or
  variant I propose it to `lordmuffin/instrument` rather than hard-coding one. Android: the
  notification is already correct; add the same local/streaming state chip to the Record
  screen so it never relies on the notification shade.

## 6. How this lands on the approved meeting-mode plan

Task 5 consumes three things from here: the `restricted` flag, the `NetworkGate` (the
calendar watcher must pause during a local session and its `/upcoming` poll must be on the
allow-list), and the forced-local-only start path. Its stand-in classifier needs no change.
I would still ship this task first, as you said.

## 7. Not verified

- Any actual traffic. A `tcpdump`/Little Snitch run during a local-only session is the right
  confirmation, and I recommend doing it once after the gate lands.
- iCloud *Desktop & Documents* state, macOS/Apple crash reporting and analytics opt-ins.
- What LiteLLM's `fast` and `heavy` aliases route to. The base URL is in-cluster, but if either
  alias resolves to a cloud provider, streamed content leaves the homelab. The proxy's config is
  not in this repo.
- Android dictation, chat, agent and TTS paths (F10), and the Wear OS flow beyond reading it.
- Whether `auto_publish` is on for your account.

## 8. Questions (need your call)

1. **May a work-classified recording ever be uploaded?** (a) Never: it stays on the Mac and the
   server never sees it. (b) Yes on explicit Upload, to the homelab only, with publish, work
   items, dispatch and Gotify bodies off. I recommend (b), but whether the homelab counts as
   "streaming to infra" under your employer's data policy is your call, not mine.
2. **Manual starts at home.** Keep streaming by default when the network check says home, or make
   every manual start local-only unless you flip it? The second is safest and changes daily use.
3. **Watch.** Leave Wear OS ungated and documented as outside local-only, or pause watch auto-sync
   behind a setting?
4. **OK to extract a session driver out of `AppModel`** (and the Android service) to make the
   no-egress test possible?
5. **Check these yourself:** iCloud Desktop & Documents on the Mac (if on, I would move local
   transcripts to Application Support), and which backends LiteLLM's `fast`/`heavy` use.

## 9. Status

Done, with what verified it:

| Piece | State | Verified |
|---|---|---|
| `sessions.restricted` (migration 0010), `restricted` on create and upload, `SessionOut` | done | server: 554 passed, ruff, mypy |
| Restricted notes tagged (`restricted: true`, tag) and filed in `Restricted/` | done | `test_restricted.py` |
| No vault PR (manual, publish-all, auto-publish), no work items, Gotify says only "A restricted note is ready." | done | `test_restricted.py` |
| `protocol/v1` `restricted` field, fixtures, Swift and Kotlin models | done | server fixture tests pass; **Swift and Kotlin not compiled here** |
| Mac `EgressGate` (API client, WebSocket, health checks), begin/end around local sessions, `LOCAL` menu-bar badge, restricted upload | written | **not compiled or run** (no Swift toolchain on this machine) |
| Android `EgressGate` OkHttp interceptor, held by `LiveSessionService`, restricted upload | written | **not compiled or run** (the build needs the sherpa-onnx download from `download-models.sh`) |
| `scripts/lint/check_egress.py` + `make lint-egress` | done | 19 lint tests pass, real tree clean |
| Web: "Restricted" in the session header, type and fixtures | done | tsc, build, new tests pass. 37 tests in `dismissals`, `ds`, `theme` fail identically on an untouched checkout (Node 26 `localStorage`) |

Not done, and why:
- **Session-driver extraction and the full no-egress test (6c).** The gate has unit tests on each
  client, but there is no test that drives a whole local session. That needs the `AppModel` and
  Android service refactor, which I will not write blind with no way to compile it here.
- **Android and Swift were never built.** Run `swift test` in `macos/JarvisLiveKit`, `make test`
  in `macos/JarvisVoiceMac`, and `./gradlew test` before trusting them.
- **No `UploadIntent`.** Outside a local session the gate allows everything; during one it denies
  everything. The explicit-upload token from section 4 was not needed for that and is dropped.
- **Android watch** (`RecordingUploader`) is outside the gate by decision; Android chat, TTS and
  model downloads are outside the Live toggle (F10).
- **Hands-on check** B25 in `macos/JarvisVoiceMac/TESTING.md` is NOT RUN.
- **Everything recorded local-only uploads as restricted**, including the automatic away-from-home
  case, because the client cannot tell a work call from a café. The owner can lift it per session:
  `POST /v1/sessions/{id}/unrestrict` (the note moves out of `Restricted/`, loses its tag, and can
  be published; refused with 409 while the note is being written; 404 for anyone but the owner),
  and a "Remove restriction" button on the session page. Lifting is one way: there is no endpoint
  to restrict a session again.
