# Recording UX: live waveform, one-page history, transcript follow

Status: **draft for review**. Nothing is implemented yet.

## What exists today

| | Mac (`macos/`) | Web (`web/`) | Android (`android/app`) |
|---|---|---|---|
| Role | Producer: mic + system audio | **Viewer only**: no mic code, and `POST /v1/sessions` returns 403 for a non-device caller (`server/.../api/sessions.py:189`) | Producer: mic |
| "Recording screen" | Menu-bar menu plus the Copilot `NSPanel` (`UI/CopilotPanel.swift`) | Sessions list + `LiveSession` view | `LiveActivity` (setup form → timer, Stop, `ProgressBar` level) |
| Level / VAD | None | None | RMS level polled every 250 ms into a `ProgressBar`. `VadSegmenter` (RMS ≥ 200) feeds Whisper only |
| States | `Phase {idle, starting, recording, stopping}`; no pause | `StatusChip`: Live / Offline / Finalizing / Done / Failed | `Phase {IDLE, STARTING, RECORDING, STOPPING}`; no pause |
| History | None. `LiveAPIClient` has no list call | `Sessions.tsx`, `GET /v1/sessions` | Separate `SessionsActivity` (REST) plus local pending uploads |
| Transcript scroll | One `ScrollView`, no auto-scroll | Follows when within 80 px of the bottom, but sets `scrollTop` on `.transcript`, which is **not the scrolling element** (`.pane-body` is), so it's effectively broken | `scrollToPosition(last)` on every update, even while you're reading |
| Tests | swift-testing in `JarvisLiveKit` (CI runs it). App XCTest exists but **CI never runs `make test`**. No UI tests | vitest (node env) + Playwright smoke (real server, Postgres) | JUnit only. No Robolectric, no instrumented tests |

Protocol: VAD runs **server-side** (webrtcvad in `ingest/segmenter.py`) but nothing about it goes on the wire. No message carries pause, level or speech activity.

## Design

### 1. Live waveform + always-visible state

**Shared model, ported to each platform with the same tests:**

- **`LevelMeter`**: RMS per 20 ms window → dBFS → 0..1. A ring of the last ~3 s is drawn as scrolling bars.
- **`VoiceActivity`**: energy VAD with an adaptive noise floor (slow-rising minimum tracker). Speech means level ≥ floor + 9 dB. Hysteresis: 2 windows to open, 300 ms hangover to close. This drives *display only*; segmentation stays on the server.
- **`RecordingIndicator`**: a pure function of capture, link, pause, local-only and outbox state. It returns one of:
  - **Recording**: audio is reaching durable storage (acked, or buffered in the outbox / written locally). Sub-label: "· local only" or "· offline, buffering".
  - **Paused**
  - **Not recording**
  - **Not saving**: the mic is live but nothing persists, e.g. outbox write failure, capture failure, or the mic delivering digital silence (Mac `noAudio`, Android equivalent).
  - Every state is a dot plus a word: red = recording, amber = paused, grey = not recording, error-red with ✕ = not saving.

**Drawing:**

- **Bars:** `jv_waveform` while speech is detected; `jv_surface2` / `jv_text2` for silence and background noise. Below the bars, a "Voice" / "Quiet" label, so colour isn't the only signal.
- **Reduced motion:** the bars hold still and only the label and level number change.
- **Tokens:** add `jv_waveform` and the stop colours to `Theme.swift`, `styles.css` and `colors.xml`.

**Pause (new end-to-end):**

- Clients stop sending audio frames and keep the socket open. The `t_ms` timeline jumps forward on resume.
- To stop the server treating a pause as a dropout, add client→server `pause {}` / `resume {}` messages (schemas, fixtures, pydantic, Swift, Kotlin; one PR, per CLAUDE.md). The server then:
  - suspends the 600 s idle watchdog;
  - reports `paused: true` on `SessionOut` and on the `status` event, so viewers show **Paused** rather than "Offline".

**Per platform:**

- **Mac:**
  - **Metering:** `vDSP_rmsqv` on the existing 4096-frame tap buffer (≈4 windows of 1024 per callback). The audio thread writes into an `OSAllocatedUnfairLock`-guarded ring and allocates nothing.
  - **Drawing:** a `TimelineView(.animation(minimumInterval: 1/30))` + `Canvas` reads the ring. It runs only while its window is visible and pauses when the session isn't recording, so no `@Observable` churn hits the rest of the UI.
  - **CPU budget:** < 1% of one core added. A benchmark test in `JarvisLiveKit` asserts the meter processes 60 s of 48 kHz audio in < 20 ms. You do the Instruments check by hand (no Mac here).
  - **Menu-bar icon:** gets the same four states.
- **Android:**
  - **Data path:** `AudioCapture` emits a level + VAD value every 20 ms sub-window to a dedicated `StateFlow<FloatArray>` ring (not through `LiveUiState`, which carries the transcript).
  - **Drawing:** a new `LiveWaveformView` (custom View, `onDraw` only on new data). The fake-animation `AudioWaveformView` stays for the overlay pill.
- **Web:**
  - **No mic**, so no client-side waveform.
  - **Server activity events:** I propose a server→client `activity {channel, level, speech}` event at 5 Hz. It is computed from the server's webrtcvad segmenter, so viewers see real speech activity on a live session.
  - **State chip:** gains Paused, and "Not saving" when `streaming=false` while live (today's "Offline").

### 2. One-page history + new-recording bubble

The layout is the same on every client:

- **History** is the scrolling main content, with title, date, duration and status chip. It reuses `SessionRow` styling: square `jv_surface` rows with a 4px gap.
- **Active recording** is a **docked card**:
  - Top on Mac/web, bottom sheet on Android.
  - `jv_surface2`, with a 1.5px `jv_stop_ring` border while recording.
  - Contents: the state indicator, timer, waveform, Pause/Stop and an expandable live transcript.
  - It never appears inside the history list. The session joins the list (status Finalizing) after Stop.
- **New recording**: a round `radius-full` accent FAB, bottom-right.
  - Tap starts with the last-used mode, local-only and title settings.
  - Long-press (Android) or a ▾ on the FAB (Mac) opens those options.
  - The FAB is hidden while a session is active; the card has Stop.

**Per platform:**

- **Mac:**
  - A new **Recordings** window (`Window` scene), opened from the menu and on session start in place of auto-opening the Copilot panel. The panel stays available ("Show Copilot Panel").
  - `LiveAPIClient` gains `listSessions(limit:offset:)`.
  - Local sessions not yet uploaded are merged into the list as "Local · not uploaded" rows.
- **Android:**
  - The Record tab (`LiveActivity`) becomes this page.
  - The `SessionsActivity` list moves in as the main content, together with pending uploads.
  - The setup form becomes the FAB long-press sheet.
  - `SessionsActivity` stays only as a redirect target for existing intents.
- **Web:**
  - The Sessions page already is the history.
  - A **live session in progress is pinned above the list** as the docked card: viewer waveform, state and auto-following transcript. Clicking it opens the full session.
  - **The "new recording" FAB can't work on web**: no capture, and the server refuses non-device session creation. See open question 1.

### 3. Transcript follow

The same pure state machine runs on all three clients (`scrollFollow.ts`, `ScrollFollow.swift` in JarvisLiveKit, `ScrollFollow.kt`):

- **Follow:** state `following | detached(unseen: n)`. Inputs: `contentAppended`, `userScrolled(distanceFromBottom)` and `jumpToLatest`. The threshold is 48 px/pt/dp.
- **Programmatic scrolls** are tagged so they never count as the user scrolling away.
- **Draft rewrites:** a draft line growing or being replaced by its final segment keeps the bottom pinned while following. It does not bump `unseen`.
- **Jump to latest:** while detached, a "↓ Jump to latest · n new" pill floats above the bottom edge. Tapping it scrolls and resumes following, and reaching the bottom by hand resumes too.

**Per platform:**

- **Web:** fix the scroll-container bug by making `.transcript` its own scroller inside the pane.
- **Mac:** `ScrollViewReader` / `scrollPosition(id:)` with an `onScrollGeometryChange` distance-from-bottom. This is used in both the docked card and the Copilot panel.
- **Android:** a `RecyclerView.OnScrollListener` (`SCROLL_STATE_DRAGGING` marks user scrolls). It replaces the unconditional `scrollToPosition`.

## Tests

| | Unit | UI / e2e | Screenshots |
|---|---|---|---|
| Web | vitest: `scrollFollow`, `recordingIndicator`, `VoiceActivity` | Playwright: replayed WAV session pinned on the Sessions page; scroll up → jump pill → click → following | `page.screenshot` in the e2e, uploaded as a CI artifact |
| Mac | swift-testing: ScrollFollow, LevelMeter/VAD (synthetic tone + noise), RecordingIndicator, meter benchmark, pause/resume fixtures | New XCUITest target. The app launches with `-JarvisFakeAudio` (a WAV-backed capture source, so no mic/TCC prompt) and the test asserts the state label, FAB → Recording, Pause → Paused, jump pill. Add `make test` + UI tests to `macos-ci.yml` | `ImageRenderer` snapshots of the page in fixed states, as a CI artifact |
| Android | JUnit: `ScrollFollow`, `LevelMeter`/`VoiceActivity`, `RecordingIndicator` | Robolectric + Espresso test of the Record page (FAB → card, scroll → jump pill) running inside `testDebugUnitTest` | Roborazzi from the same test, as a CI artifact |
| Server | pytest: pause/resume suspends the watchdog, `paused` in `SessionOut`/`status`, `activity` events emitted | n/a | n/a |

The PR includes all screenshots, committed under `docs/plans/recording-ux/` and linked in the description.

## Constraints I can't meet from this container

- **No Swift toolchain, no Android SDK** (and `dl.google.com` is blocked). So:
  - Mac and Android code is written blind and verified only by CI (`macos-26`, `build.yml`).
  - Expect a few fix-up pushes there.
  - Mac and Android screenshots come from CI artifacts.
- **Docker isn't running** here yet. If I can't start it, the web Playwright run happens in CI too.
- **Mac UI tests on GitHub's macOS runners** are the riskiest piece (window-server/accessibility automation). If XCUITest won't run there, I fall back to the `ImageRenderer` snapshots plus a hosted-view test, and say so in the PR.

## Out of scope / can't do yet

- **Web:** no recording (no "new recording" button, no local waveform) unless we take option B below.
- **Wear (`android/wear`):** a legacy WAV-upload recorder with no streaming, no transcript and no tests. Not touched.
- Waveform VAD is a display cue. It doesn't change what the server segments or stores.

## Open questions

1. **Web recording.**
   - (A, recommended for this PR) Web stays a viewer. It gets the pinned live card, the server `activity` waveform, the states and transcript follow, but no "new recording" button.
   - (B) Add browser recording: `getUserMedia` + an AudioWorklet resampling to 16 kHz PCM, a producer ticket, and a server change to let signed-in users (not just devices) create sessions. B is roughly as large as the rest combined; I'd do it as a follow-up PR.
2. **Pause.** Do pause/resume as a protocol + server change (recommended, so the server and viewers know), or client-only (simpler, but the web shows "Offline" and the server ends the session after 10 min)?
3. **"Not saving".** Is my definition right: audio isn't reaching the server *or* local disk, including a silent or failed mic? Or did you mean "local only / not uploading to the server"? I've made that a sub-label of Recording.
4. **One PR or four?** One PR as asked (protocol + server, web, Mac, Android as separate commits), or split so each lands green on its own?
