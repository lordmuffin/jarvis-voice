# Local-only audit: findings

Status: **findings only. No code has been changed.** Branch `feat/local-only-audit`.
Method: static read of `macos/`, `android/` (live path, upload workers, watch sync, legacy network clients), `server/src/jarvis_live/{ingest,api/stream.py,notify,finalize,vault,llm}`, `protocol/v1/`, CI workflows. **Nothing was run.** No packet capture, no macOS or Android runtime was available here, so every claim below is from reading code and should be confirmed with the no-egress test (step c) and a real `tcpdump`/Little Snitch pass on a Mac.

## Revision: intended purpose (from the owner, after the first draft)

**Local-only is a fallback for when the server is unavailable. It is not a privacy barrier against the server.** Full processing (upload, transcription, LLM) must always remain possible. Read the findings below with that in mind:

| Finding | Status under the revised intent |
|---|---|
| F1 health probes during local-only | **Withdrawn as a leak.** Probing the server is how the app notices it is back. Still worth fixing the *label* ("no network" is false) and skipping probes only if you want that. |
| F2 default streams unless away | **By design, not a bug.** "Streaming when the server is reachable" is correct. The null-place case (first launch) is only a problem if the server is down: see F8. |
| F3 indicator says LIVE in a local-only session | **Still real.** You must be able to tell which mode a running session is in. |
| F4 transcripts in iCloud / synced Vault folder | **Downgraded.** Syncing your own notes to your own vault is arguably wanted. Keep as an FYI; no change unless you say so. |
| F5 Android ungated paths, watch always uploads | **Withdrawn.** Uploading is the normal path. |
| F6 server `local_only` is dead | **Mostly moot.** Uploaded sessions *should* get full processing, so leaving it unset is correct. The dead branches can be deleted or left. |
| §3 allow-list, hard gate, no-egress test | **Dropped as framed.** A "fail on any outbound request" test no longer matches the goal. |

### F8. NEW, REAL (reliability): the Mac does not fall back when the server is down
This is the case local-only exists for, and the two platforms differ:
- **Android** falls back automatically: if `createSession` fails with an IOException it sets `localOnly = true`, records locally and shows a notice (`LiveSessionService.kt:185-189`).
- **Mac does not.** `prepare()` calls `createSession` for a streaming start and lets the error propagate (`AppModel.swift:266-276`); `start()` catches it, shows the error and returns to idle (`:218-223`). If the server is down and the toggle still says streaming (first launch before any check, hand-set streaming, or a probe that raced), **the session does not start and the meeting is not recorded.** That is the worst outcome for a tool whose job is not to lose the recording.
- Mid-session loss is handled on both: audio keeps going to the outbox and replays on reconnect.

### Revised recommendations
1. **Mac: fall back to local-only when `createSession` fails** (network error, timeout, 5xx; not 401/403, which should still surface). Show the same notice Android does. Test first: stub `createSession` to fail, assert the session starts local and writes the file.
2. **Make the mode unmistakable (F3).** Menu-bar icon, panel and Android notification/chip state the mode in one word, driven from the running session's actual mode (`Run.localOnly`), not the stale `sessionIsLocalOnly`. Also show when a streaming session has dropped to buffering ("Streaming, offline: buffering") so "streaming" never hides an outage.
3. **Auto-upload when the server returns.** Android already resumes interrupted streaming sessions; local-only ones wait for a manual tap, and the Mac only has the manual menu item. Decide whether a fallback-created local session should upload by itself once the server is reachable (it was meant for processing, not for keeping local).
4. Fix the "Local-only (no network)" label to say what it does ("Record on this Mac; upload later").

### Implemented (uncommitted, on this branch)
Not compiled or run locally: this machine has no Swift toolchain, and the Android build can't resolve the sherpa-onnx dependency offline. CI is the first real verification.
- **Mac fallback.** `StartFallback.shouldFallBack` (JarvisLiveKit, tested in `StartFallbackTests`): network errors and 5xx fall back to local recording; 400/401/403/404/422 still surface. `AppModel.prepare` uses it. A fallback recording needs no on-device drafts, so missing speech assets no longer block it.
- **Auto-upload (Mac).** Fallback files carry `upload: auto` in their front matter. When `/healthz` answers, idle, they upload one at a time and stop at the first failure.
- **Auto-upload (Android).** `SessionMeta.fellBack` / `autoUploads` (tested in `SessionMetaTest`); the worker is enqueued at stop and on app open.
- **Indicator.** Mac menu-bar badge reads `LOCAL` (filled) during a local session instead of LIVE/LAB, with an accessibility label saying "local only, not streaming"; the panel drops the server badge for local sessions and explains a fallback. The mode now comes from the running session before capture starts, not from the previous one. Android's notification now says "Recording locally (not streaming)" or "Recording, server offline: buffering" and updates when the connection changes.
- **Labels.** "Local-only (no network)" is now "Local-only (record here, upload later)" on both platforms. `TESTING.md` B7 rewritten, B25 added.

### Fixed: Mac upload no longer marks a session uploaded when it isn't
Cause: `AppModel.upload` ignored how `connection.end()` returned (it also returns on its 30 s timeout or when the server is gone), marked the file `uploaded`, and had already moved the audio to the server's id, so a retry would have created a second server session and failed with "audio missing".
- The server session id is written to the file (`server_session:`) before any audio moves. A retry reuses it (`UploadHandoff.relocate` finds the audio already under that id), and the connection resumes from the server's acks.
- The file is marked uploaded only when the connection state is `.ended`. A timeout or drop throws "interrupted" and leaves it pending; a server refusal (`.failed`) surfaces its reason.
- Tests: `UploadHandoffTests`, `LocalMarkdownTests.testServerSessionIsRecordedForResume`. Manual: `TESTING.md` B26.
- Not covered: if the server deleted the half-uploaded session, the retry reports the refusal each time rather than starting a fresh session.

---

## Verdict (original, before the revision above)

**The toggle is not airtight.** The core streaming path is sound: in a Mac local-only session no `LiveConnection` object exists, so no audio, draft, or marker is sent. But:

1. **Local-only sessions on the Mac still contact the homelab server** (health probes, repeated, including mid-session). The menu label "Local-only (no network)" is false, and the manual test that would catch it (`TESTING.md` B7) has never been run.
2. **The default is the wrong way round for your policy.** A fresh install, a Mac at home, or a Mac on NetBird all start sessions in *streaming* mode. "Local-only" is derived from *can I reach the server*, not from *may this call be streamed*.
3. **The indicator cannot be trusted.** The menu-bar badge shows `LIVE` during a local-only recording. Local-only appears only in the dropdown and the panel chip.
4. **Local transcripts are written where sync can carry them off the Mac or phone** (iCloud Documents; the Android Vault folder).
5. **Android has no hard gate and several ungated network paths**, including watch recordings that always upload.
6. **The server's own `local_only` safeguard is dead code.** Nothing ever sets it true.

There is no gate at the transport layer anywhere. Protection today is "the connection object is nil", checked at each call site.

---

## 1. Findings (ranked)

### F1. REAL LEAK (metadata): Mac contacts the server during local-only sessions
`AppModel.init` starts an `NWPathMonitor` (`AppModel.swift:106-109`) whose every callback runs `recheckNetwork()` (`:158`). That calls `refreshServerInfo()` **unconditionally** (`:159`), which does `GET <server>/healthz` (`ServerChannel.swift:50-56`). It runs at launch, on every Wi-Fi/VPN change, on settings edits, **and while a local-only session is recording**. When "Local-only away from home" is enabled and credentials are set, `NetworkPlaceDetector.detect` adds a second `/healthz` probe (`NetworkPlaceDetector.swift:19`).
- Leaves the Mac: DNS lookup and TLS SNI for the server host, source IP, timing. No transcript, audio, or title. The response (`environment`, `version`) feeds the LIVE/LAB badge.
- Why it matters: this is a request "from this Mac to the server" in a mode the UI calls "no network", and it discloses when and from where you were in a meeting. `TESTING.md` B7 ("No request from this Mac reaches the server") would fail if run. It is marked NOT RUN.
- Cause: `LiveAPIClient` (`.shared`, `LiveAPIClient.swift:16`), `LiveConnection` (`:82`), `NetworkPlaceDetector.probeSession`, and `ServerInfo.fetch` each create their own `URLSession`. There is no single choke point.

### F2. REAL (policy): default resolves to streaming; ambiguity is not local-only
`AutoLocalOnly.localOnly = manual ?? (enabled && automatic == true)` (`AwayMode.swift:71`; identical on Android, `AwayMode.kt:59`).
- `automatic` is `nil` until the first network check completes, so `nil == true` is false: **before the first check, a session streams.** `AutoLocalOnly()` defaults `place = nil`, so every fresh install starts in this state. The check is also skipped entirely when there are no credentials, or the away feature is off (`AppModel.swift:164`), which leaves `place` nil.
- `.home` and `.netbird` both give `automatic == false`: **on the home network or NetBird, every call streams to the homelab by default.** The ⌃⌥J hotkey starts a session in whatever state that resolves to, with no confirmation.
- A hand flip to "streaming" persists in `UserDefaults` (`Settings.swift:43`) across sessions and launches until the network place changes. It is a sticky global, not "per session".
- Android: `LiveUiState.localOnly` defaults `false` (`LiveSessionService.kt:52`); `renderAwayMode` sets it to `!isConfigured || away.localOnly` (`LiveActivity.kt:262`), the same null-means-stream rule.
- Fail-safe direction does exist in two places: Android falls back to local when `createSession` fails with an IOException (`LiveSessionService.kt:185-189`); the Mac refuses to start streaming without credentials (`AppModel.swift:267`). Neither covers the "unknown state" case above.

### F3. REAL (visibility): the always-visible indicator can say LIVE during a local-only session
- Mac menu-bar item: `MenuBarIconSpec.make` takes `isRecording`, `isDictating`, `elapsedSeconds`, `channel` and **no local-only input** (`PresentationLogic.swift:36`). During a local-only recording it draws the recording capsule plus the server-channel badge, `LIVE` or `LAB` (`:50`, `MenuBarIcon.swift:49-57`). The badge describes which server you *would* talk to, not whether you are talking to it.
- Panel: the `REC` pill (`CopilotPanelView.swift:~165`) is the same for both modes; the only difference is a plain `Local-only` chip (`:188`) next to a `LIVE`/`LAB` badge in the title strip (`:61`). Two contradictory words on one screen.
- `sessionIsLocalOnly` is set only *after* `begin()` succeeds (`AppModel.swift:214`) and is stale across sessions, so `link` reports the **previous** session's mode during `.starting` (`:119`).
- Android: has a notification line ("Recording locally" / "Recording and streaming", `LiveSessionService.kt:458`) and a connection chip (`▫ Local-only`, `inst_text`, neutral). Better than the Mac, but the chip is neutral-coloured and sits among other chips; no always-on status bar or lock-screen distinction beyond the notification text.

### F4. REAL (leak vector): local transcripts land in sync-able locations
- Mac: `~/Documents/Jarvis Live/*.md` (`LocalMarkdown.swift:20`). If "Desktop & Documents" is on in iCloud Drive, the transcript is uploaded to Apple. Also covered by Time Machine and Spotlight. The app is not sandboxed (`project.yml`: `ENABLE_APP_SANDBOX: NO`).
- Mac: raw audio of every local-only session is kept unencrypted under `~/Library/Application Support/JarvisVoice/sessions/<id>/` (`Outbox.swift:~33`) until uploaded. There is no retention or delete path for sessions that are never uploaded.
- Android: the local note is written to the user's **Vault Folder** (SAF tree) if set (`LocalMarkdown.kt:71-85`), otherwise `Documents/JarvisVoice/Live/` on shared storage (`PersistentStorage.kt:15-27`). That vault is the Obsidian vault, which is git-synced to Forgejo on the homelab. "Nothing uploads" breaks the moment the vault syncs. `allowBackup="false"` is set (good).

### F5. REAL (ungated on Android): paths the toggle does not govern
Local-only is a parameter of one service (`LiveSessionService.startSession`). Everything else on the phone ignores it:
- **Watch recordings always upload.** `WatchUploadWorker.kt:40` and `WearGraph.kt:31` call `upload(..., source="watch")`. There is no local-only concept in `wear/` or `wearcommon/` at all. (Content of a watch recording goes to `POST /v1/sessions/upload`, i.e. to the same server and LLM pipeline.)
- **`LiveActivity.onResume`** calls `refreshServerChannel()`, `refreshRecent()` (`GET /v1/sessions`), `enqueueInterrupted` and `checkNetwork()` (`LiveActivity.kt:141-148, 219-228`) whenever the app is configured, local-only or not.
- **`NotifyWorker`** polls every 15 min (`NotifyWorker.kt:87-95`) against hard-coded cleartext `http://192.168.1.155:8881` with an API key header (`NotificationRepository.kt`). `network_security_config.xml` permits cleartext to three LAN IPs. LAN only, no session content, but it is a standing outbound call unrelated to the toggle.
- **Voice chat / dictation**: `NetworkTtsRepository` posts text to a Kokoro server; `AgentTaskRepository` posts to the vault API; `AndroidSpeechEngine.createRecognizer` uses `createSpeechRecognizer` (**the platform recogniser, which can send audio to Google**) whenever on-device recognition is not available (`AndroidSpeechEngine.kt:47-52`). These are the legacy overlay features, not the Live path, but they are in the same APK and no policy covers them.
- ML Kit GenAI (`AiCore`) and WorkManager model downloads (`ModelDownloadWorker`) talk to Google and model hosts when triggered. No telemetry SDK (Firebase, Crashlytics, Sentry) was found in the Gradle files or sources on either platform.

### F6. Server `local_only` is dead
`Session.local_only` (`db/models.py:136`) is read in `processing.py`, `orchestrator.py:134,169`, `publishing.py:71`, `finalizer.py:127`, `sessions.py`, to skip the LLM. **No production code ever writes it `True`** (grep: only test fixtures do). `POST /v1/sessions` (`sessions.py:~190`) and `/upload` never set it. The protocol schema `session_create_request` has no such field (`protocol/v1/`). So the server-side "never sent to an LLM" guarantee is unreachable, and an uploaded local-only session is processed like any other (see A4).

### F7. Smaller points
- Menu string `Local-only (no network)` (`MenuBarContent.swift:37`, Android `strings_live.xml:8`) overstates; see F1.
- `LocalTranscriber.ensureAssets` calls `AssetInventory...downloadAndInstall()` at session start and on a background Task for dictation (`LocalTranscriber.swift:45-63`, `DictationController.swift:235`). First run only, to Apple. A local-only start **throws** if STT assets are unavailable (`AppModel.swift:308`), so it fails closed, but that download is an egress.
- Existing tests would not catch a leak: `macos-ci.yml` runs `swift test` for `JarvisLiveKit` only, **never `make test` for the app** (the `RecordingUX` plan already notes this). `JarvisVoiceMac/Tests` has no network tests. Existing stubs (`StubURLProtocol`, `HealthStub`) are per-test and allow anything not stubbed to fail silently.
- `RelatedLink.url` restricts clickable links to `obsidian://` (`PresentationLogic.swift:167`), so a malicious server event cannot open a web URL. Good.
- Dictation (Mac) is local: `LocalTranscriber` only, no `LiveConnection` (`DictationController.swift:10`). Verified by reading; not by capture.
- Server sets no cookies or telemetry for producers; the `title` field exists on `POST /v1/sessions` (`protocol.py:28`) but the Mac never sends one. Android sends the user-typed title only when streaming, or at upload time for a local-only session.

---

## 2. Byte-by-byte trace: what leaves the client in Mac local-only mode

| Data | Leaves? | Evidence |
|---|---|---|
| Audio frames | No. Appended to the on-disk outbox only | `connection == nil` → `outbox.append` (`AppModel.swift:334-338`) |
| Partial and final drafts | No | `if emission.sendToServer, let connection` (`:324`); `connection` nil |
| Markers | No | `if let connection = run.connection` (`:254`) |
| Session title | No (Mac never sets one) | `SessionCreateRequest(mode:channels:)` only (`:271`) |
| Copilot requests | No: copilot is server-side and runs only for a connected producer | `stream.py`, `copilot/loop.py`; panel text `CopilotPanelView.swift:480` |
| Vault lookups | No: `vault/index.py` is a server-side SQLite FTS | `vault/index.py` |
| Gotify bodies | No: pushed by the server after a finalized session (`finalizer.py:334`, title + LLM summary) | n/a for local-only unless uploaded (A4) |
| Telemetry / crash logs | None found: no SDK, no `URLSession` upload of logs, no `os_log` to remote | grep of `macos/`; `DebugLog` on Android writes a local file only |
| **`GET /healthz`** | **YES, repeatedly. See F1** | `AppModel.swift:106-183` |
| Apple speech-asset download | First run only | `LocalTranscriber.swift:45-63` |
| iCloud / Time Machine copy of transcript | Possible | F4 |

## 3. Allowed outbound calls (proposed allow-list, with reasons)

This is the set I propose the hard gate enforce. Everything else is denied in local-only mode. **None of these is currently gated.**

| # | Call | When allowed | Reason | Content |
|---|---|---|---|---|
| A1 | `GET <server>/healthz` (placement probe) | Only when *not* in an active local-only session, or never, if you prefer: in local-only the probe should be skipped and `place` kept as last known | Decide home/NetBird/away; the LIVE/LAB badge | None |
| A2 | `POST /v1/sessions`, `POST /v1/sessions/{id}/ticket`, `WS /v1/sessions/{id}/stream` | **Only in a session started in streaming mode** | The product | Audio, drafts, markers, title |
| A3 | Apple speech-asset download (`AssetInventory`) | Allowed but should be a separate, named exception | On-device STT needs the model once | None (inbound) |
| A4 | Explicit **Upload for Full Processing** (Mac menu, Android Pending-sessions tap) | Only on a user click, per session, with a confirmation that names the server and says the audio will reach the LLM | Deliberate hand-off of a local-only recording | Full audio + title; the resulting session is **not** marked `local_only` (F6) |
| A5 | Android `GET /v1/me`, `GET /v1/sessions`, `/healthz` for the Recent list and chip | Not in local-only | UI convenience | Bearer token, metadata |

Calls I think should be **denied or moved behind the same switch**: watch uploads (F5), `NotifyWorker` polling, Kokoro TTS/agent-task calls, platform `SpeechRecognizer` fallback. The final policy for these is your call: see the open questions.

## 4. Recommended plan (not started; waiting on your go-ahead)

Per your instruction I stopped after the audit. Proposed order, TDD per `TDD.md`:

**Test first (c).** A no-egress harness in `JarvisLiveKit` (so it runs in the existing `macos-ci.yml` `swift test`; the app target is not run in CI today): a `URLProtocol` registered on every session that **fails the test on any request not on the allow-list** and records all requests, plus a loopback `TestWebSocketServer` for the streaming-mode counter-test. Run a full local-only session (start → frames → drafts → marker → stop) and assert zero requests. Write it red first: it fails today on `/healthz` (F1). Android gets the equivalent with an OkHttp `Interceptor`/`MockWebServer` in `testDebugUnitTest`, which already runs in `build.yml`.

**Enforce (b).** One `EgressPolicy` object owns the only `URLSession` factory. All transports (`LiveAPIClient`, `LiveConnection`, probe, `ServerInfo`) take their session from it, and it consults the per-session mode, not each call site. A lint rule (SwiftLint custom rule / Gradle check, same mechanism as the design lint) bans `URLSession(`/`URLSession.shared`/`OkHttpClient(` outside it. Android: one `OkHttpClient` provider with an interceptor, applied to the Live, upload, watch, TTS and notify clients.

**Default (e).** Session mode becomes an explicit value fixed at start (`enum SessionMode { localOnly, streaming(reason) }`), stored on the `Run`, and the only thing the UI reads. Resolution: `streaming` only if the user explicitly chose it for this session *and* credentials and a fresh placement check agree; `place == nil`, stale check, unknown network, no credentials, or probe failure → `localOnly`. Remove the sticky `manual` persistence for "streaming".

**Visible (d).** Replace the `LIVE`/`LAB` badge during a recording with a mode glyph: local-only draws a distinct shape (e.g. a lock/house capsule, filled), streaming draws the channel badge; accessibility label states the mode in words. Panel title strip and the macOS/Android notification say the same single word. Derive from `Run.localOnly`, not from `sessionIsLocalOnly`.

**Server (F6).** Either write `local_only` on the Upload path (`mode`/query flag) or delete the dead branches; decide before touching `protocol/` (needs fixtures + server and Swift tests in the same PR, per `CLAUDE.md`).

**Done when:** no-egress test green in CI on macOS and Android, and §3 is the live allow-list in code.

## 5. Open questions (original; superseded by the revision at the top, which answers 1, 2 and 4)

1. **Should "Upload for Full Processing" mark the server session `local_only` so it still never reaches an LLM**, or is upload an explicit opt-in to LLM processing? (§3 A4.)
2. **Watch recordings and the legacy Android chat/TTS/notify features: in scope for the gate, or out of scope?** They are independent of the Live toggle today.
3. **Where should local transcripts live?** Moving the Mac file out of iCloud-eligible `~/Documents` and Android out of the synced Vault folder is a behaviour change; confirm it before I do it.
4. **Is the intended default "streaming unless away"** (current design) **or "local-only unless I opt in per session"** (what your policy wording implies)? I read it as the latter and the plan assumes it.
5. Do you want a Mac-side check of F1 (and F4's iCloud state) with `tcpdump`/`nettop` before the fix, to confirm against a real build? I could not run one from here.
