# Plan: driving mode (hands-free capture in the car)

Status: **proposed, awaiting approval.** Nothing is implemented. Scope: `android/app` plus one small `server/` change.
**No `protocol/` change.** Legacy `src/jarvis_voice/` is not touched.

## Decisions (2026-10-30, from review)

- **Mute releases the mic** (not zero-fill). Supersedes §5.2.
- **No Bluetooth**: phone built-in mic only; the opt-in car mic and SCO are dropped from §4.
- **Scope cut to a trigger**: build a launcher shortcut and a Quick Settings tile that start a solo Live session (a second
  trigger marks instead of stopping). Done in this branch: `DrivingTrigger`, `DrivingActivity`, `DrivingTileService`,
  `shortcuts.xml`. Auto (Phase 3), the big-button screen, media-button mapping and earcons are **not** in this cut.
- **Built since (uncompiled, see PR notes)**: interruption detection (`SilenceDetector` + Android 10+ `AudioRecordingCallback`,
  markers "mic interrupted"/"mic back", notification text) and `AudioRecord` re-open with backoff (`RecoveryPolicy`, 60 s) with
  `t_ms` kept real across the gap. Zero frames are still stored during a call (keeps seq contiguous).
- **Built (all four follow-ups)**: mute releases the mic (frames of silence are paced by the clock, so seq, `t_ms` and the
  server idle timer carry on; unmute re-opens it); durable markers (`markers.jsonl`, resent on every connect, idempotent
  server insert via migration 0010); server `producer_gone_grace_s` (45 min) for a producer that dropped without `end`; and
  driving sessions force the built-in mic. Server: 548 tests pass. Android: **uncompiled**, no JDK 17/SDK on the dev box.
- **Still open**: the client fallback for gaps longer than the grace window (replay into a new session), media-button and
  earcon controls, a Mute button in the app UI (today: notification action only), Auto (Phase 3).

Goal: think out loud in the car without touching the phone, and have it land in the vault exactly like a Live session
(`POST /v1/sessions` → stream → finalizer → note).

## 1. What exists today (read before deciding)

| Piece | Finding |
|---|---|
| `car/JarvisCarAppService` + `JarvisCarScreen` | **A Car App Library screen already exists**, but it drives the *dictation* path: it sends `ACTION_CAR_START/STOP/MUTE` to `VoiceOverlayService`. That path types text into a field; it never reaches the vault. "Mute" is only a flag that blocks the next `CAR_START`; it does not mute a recording in progress. Manifest problems: category `IOT` (§3), `HostValidator.ALLOW_ALL_HOSTS_VALIDATOR`, and a `distractionOptimized` meta-data that is for Automotive OS activities, not for a projected service. |
| `VoiceOverlayService` | Cannot carry driving mode. `ACTION_SCREEN_OFF` while listening **stops the recording** (line ~110), which is the normal state of a phone in a cup holder. It also refuses to start while a Live session owns the mic (`MIC_BUSY`). |
| `LiveSessionService` | **This is the carrier.** Already a microphone FGS with a partial wake lock, `ACTION_START/STOP/MARKER`, a notification with Marker and Stop actions (service PendingIntents, so they work from the lock screen), `START_NOT_STICKY`, and a clean kill path (`onDestroy` marks the session PENDING and enqueues `LiveUploadWorker`). No new service is needed. It has **no mute**, no interruption handling, and no notion of "driving". |
| `Outbox` + `LiveConnection` + `LiveUploader` | The resume path is real and good: durable append-only frames, `hello.resume` from the acked seqs, replay from `hello_ack`, 20 s OkHttp ping (dead sockets are noticed in about 40 s), 30 s max backoff, 5 s in-flight window, local-only → `createSession` → replay on reconnect. Gaps in it are in §6. |
| `notify/` | Kai reminders: 15 min `NotifyWorker` poll plus `PRIORITY_HIGH` heads-up notifications. Not a driving path, but it **will fire into a drive** (sound + heads-up). It also hard-codes `http://192.168.1.155:8881`, so on mobile data it silently returns nothing. Relevant here only as something driving mode must quiet (§5.5). Also the natural place a spoken reminder would hook in later (task 7). |
| `VoiceChatActivity` | Cannot carry driving mode: `onPause`/`onStop` destroy the speech engine and stop the conversation, so locking the screen ends it. Useful as a reference for task 7: `NetworkTtsRepository` explicitly picks the BT A2DP output device because routing sticks to the earpiece after SCO. |
| `AudioCapture` / `AudioDeviceRouter` | `AudioRecord(VOICE_RECOGNITION)` honouring a *global* preferred input device (Settings). SCO via `startBluetoothSco()` (deprecated from API 31; the app targets 34). **A capture error stops the whole session** (`onError → stopSession`). **Nothing watches for the mic being taken away** (§4). |
| Server | `idle_end_s = 600`: a live session with no audio for 10 min is ended by the watchdog (`orchestrator.check_once`). The resume path then answers `session_not_live`, which the client treats as **fatal** (`FATAL_ERROR_CODES`). Markers are stored (`MarkerRow`) with no idempotency. |

## 2. Recommendation (one paragraph)

Build **a big-button Driving mode inside the existing app, on top of `LiveSessionService`**, controlled by steering-wheel
media buttons, lock-screen notification actions, earcons, and auto-stop when the car disconnects. Treat Android Auto as a
**sideload-only second phase**: re-point the existing car screen at `LiveSessionService` as a thin remote. Don't plan around Play
distribution of an Auto app, because no Car App category fits a voice recorder (§3).

## 3. Android Auto / Car App Library vs big-button mode

### Android Auto: the real constraints

1. **No category fits.** Car App Library apps must declare a supported category: navigation, point of interest, charging, parking,
   weather, IOT (or media / messaging via different APIs). `IOT` is for *controlling connected devices*. A voice recorder is none of
   them, so a Play-listed submission should be expected to fail review. The current `IOT` declaration is not a reasonable reading of
   the policy. (From my knowledge of the policy; re-check the current Car App Library "app quality" and category pages before
   anyone submits.)
2. **Template-only UI.** No custom views, no camera, no arbitrary layouts. `MessageTemplate`, `PaneTemplate`, `GridTemplate`,
   `ListTemplate`, each with a limited number of actions, rows and text lengths, and a hard cap on screen-stack depth. Big buttons
   are fine as template actions; a live waveform or transcript is not.
3. **Host validation.** Production needs a real allow-list of Google's signing certs, not `ALLOW_ALL_HOSTS_VALIDATOR`.
4. **Mic access is the phone's.** The car app process can't just record. `CarAudioRecord` exists but is gated, and I would not
   rely on it. Capture stays in `LiveSessionService`, which is what we want anyway.
5. **Starting the mic FGS from the car process is probably refused.** The `CarAppService` is not a visible activity. The app targets
   SDK 34, and `LiveSessionService.goForeground()` already documents "Android 14 refuses a microphone FGS unless the app is
   visible". This is the single biggest technical risk for *any* in-car start button. See Spike 0.
6. **Only works with a head unit that supports Auto** (or Auto for phone screens). It does nothing in an ordinary BT-only car.

### The part that makes Auto still worth a sideload phase

The app is distributed by **sideloaded APK** (`android-v*` tag → APK). Android Auto's developer settings allow "Unknown sources",
which shows sideloaded template apps **without Play review**. So rules 1 and 3 don't block *the user's own car*; they only block
distributing it widely. That makes Auto a reasonable optional remote, not the foundation.

### Comparison

| | Big-button mode in the app (+ media buttons, notification) | Android Auto template app |
|---|---|---|
| Works in a BT-only car | **Yes** | No |
| Needs phone screen | No (media buttons, notification actions, voice-open) | No (car screen) |
| Review / policy | None | No fitting category; sideload only |
| Start from "not touching the phone" | Voice "open Jarvis Voice" or auto-arm (§5.1) | Car-screen tap, but blocked by FGS-start rule unless Spike 0 passes |
| Live status UI | Full (Compose, Instrument tokens) | Text-only templates |
| Reuses `LiveSessionService` | Yes | Yes (as a remote) |
| Effort | Medium | Small, once the above exists |

**Decision: Phase 1 = big-button + media-button mode. Phase 2 (optional) = re-point the existing Auto screen at `LiveSessionService`.**

## 4. Audio: routing, and the stolen route

### Default: don't use the car's audio at all

Capture-only needs no output route. Over Bluetooth, using the car mic forces **HFP/SCO**, which (a) drops music/nav from A2DP to
mono phone-call quality for the whole session, (b) is 8 kHz narrowband on many head units (16 kHz mSBC only if both ends
support it), and (c) is exactly the route a phone call steals.

**Driving mode therefore records from the phone's built-in mic by default** (`TYPE_BUILTIN_MIC` as the preferred device, no
SCO). Car audio keeps playing normally. "Car microphone (Bluetooth)" is an opt-in setting with the trade-offs written next to it.
The driving input choice is its own preference; it must **not** overwrite the global one in Settings.

Cost: a phone in a bag or pocket picks up a muffled, road-noisy signal. AEC/NS are already on. The drive test checks the placements
(§8). Phone placement guidance goes in the first-run screen.

If the opt-in car mic is used: on API 31+ use `AudioManager.setCommunicationDevice()` / `clearCommunicationDevice()` rather than
the deprecated `startBluetoothSco()`.

### Stolen route / mic taken away

Cases and what happens **today** versus what the plan does:

| Event | Today | Plan |
|---|---|---|
| Phone call (or other higher-priority capture) | Android keeps the `AudioRecord` alive but **silences it** (zeros, no error). The session shows "Recording", writes zeros to the outbox and gets them acked. This is the dangerous failure: silent loss that looks fine. | Register `AudioRecordingCallback.onRecordingConfigChanged` (API 29+; `isClientSilenced`) **and** a digital-silence detector (exact-zero frames for >1.5 s; covers API 26–28 and OEMs). On interruption: set `LiveUiState.interrupted`, drop a marker "mic interrupted", play the *interrupted* earcon. Keep appending the zero frames: that keeps seq and `t_ms` contiguous and the server idle timer alive, with no protocol change; the cost is 32 KB/s, so cap it (below). On recovery: marker "mic back", earcon. |
| `AudioRecord` dies (`ERROR_DEAD_OBJECT`, route switch) | `read` throws → `onError` → **`stopSession`**. A 2 s Bluetooth hiccup ends your drive. | Recreate the `AudioRecord` in place, with backoff (0.5 s, 1 s, 2 s, ... capped at 5 s), up to 60 s of failure, then stop. `t_ms` comes from the monotonic clock, not `frameIndex × 100`, so time stays right if frames were missed. |
| Interruption longer than 10 min | n/a | Stop appending zeros after 10 min of silence-gap and mark "mic lost"; the server then idles out as it does today. Say so in the UI and the notification. |
| Car BT disconnects (engine off) | Nothing | `ACL_DISCONNECTED` for the paired car device starts a **2 min** timer (walk-away, tunnel, re-pair); reconnect cancels it; expiry stops and finalizes the session. This is the no-touch Stop. |

The 10 min cap and 2 min auto-stop are the defaults I'm proposing; they're constants in one place.

### Audio output: earcons, and the task-7 seam

You can't look, so every control needs audible confirmation. Phase 1 plays 6 short tones (started, stopped, muted, unmuted,
marked, interrupted) with `USAGE_ASSISTANCE_SONIFICATION` and `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK`, so music ducks for ~300 ms and
carries on. No TTS in v1: spoken output from the phone would also be picked up by the mic.

**Task 7 seam:** put all of that behind one small interface in the service, `DrivingAudio { cue(Cue); route: OutputRoute }`. Talkback
later swaps in an implementation that requests longer-lived focus (`USAGE_ASSISTANT`), picks the A2DP device the way
`NetworkTtsRepository` does, and gates capture during playback. Nothing else in driving mode needs to change.

## 5. Controls that work without looking

| Action | Steering-wheel / headset media button | Notification action (lock screen) | Other |
|---|---|---|---|
| Mute / unmute | Play/Pause | yes | big button |
| Mark this moment | Next | yes (exists) | big button; "todo" mark on Previous is a candidate, not in v1 |
| Stop | (none: too easy to hit by accident) | yes (exists) | auto-stop on car disconnect; big button, with a hold-to-confirm |
| Start | (none) | yes | see §5.1 |

### 5.1 Start

Order of preference, all of which avoid unlocking the phone:

1. **Voice: "Hey Google, open Jarvis Voice."** Plain "open app" works for sideloaded apps (App Actions deep links need a Play listing,
   so I'm not relying on them). `LauncherRoute` sends the launch to `DrivingActivity` when driving mode is armed (setting, or
   car-mode / car BT connected). The activity shows over the keyguard (`setShowWhenLocked`) and auto-starts on create. An
   assistant launch is allowed to start a visible activity, and a visible activity may start the mic FGS.
2. **Auto-arm on car BT connect:** a notification, `VISIBILITY_PUBLIC`, with a **Start** action that is a service
   PendingIntent. Tapping a notification action is an FGS-start exemption, but it is a phone tap.
3. **Auto Phase 2** car-screen button (blocked on Spike 0).

Fully automatic start on BT connect is **not** possible without a visible app or an exemption: the mic FGS can't start from a
broadcast receiver on SDK 34. I'd rather say that than promise it.

### 5.2 Mute semantics

Mute = the capture thread **zero-fills the samples before the sink** (so neither the outbox, the server, nor on-device Whisper
ever sees audio), writes a "muted"/"unmuted" marker, and keeps frames flowing. `AudioRecord` stays open (no re-acquire latency, no
risk of losing the mic to another app). Trade-off: the system privacy indicator stays on while muted. Easy to change later if
you'd rather release the mic.

### 5.3 Media buttons without a new service

A `MediaSessionCompat` owned by `LiveSessionService` for the life of the session, state set so it is the active session for media
keys, mapped as in the table. `androidx.media3.common` is already a dependency; `MediaSessionCompat` is in AndroidX core, so
this adds no service. Whether *your car* forwards Play/Pause/Next to the phone, and whether another app (the music player)
gets them instead, is a checklist item, not an assumption.

### 5.4 Marker durability (needed, small)

Markers are the main in-car output and today they live **only in memory** (`pendingControl`, `Run.markers`). A marker sent just
before a socket drop can be silently lost, and any marker is lost if the process dies. Fix, with no wire change:
- Append each marker to `markers.jsonl` in the session directory (next to the outbox).
- On every (re)connect, resend markers whose `t_ms` is after the last-acked frame's `t_ms`; `LiveUploader` replays the file too.
- Server: make `save_marker` idempotent on `(session_id, t_ms, label)` (unique index + ignore-on-conflict; one Alembic
  migration).

### 5.5 Quiet everything else while driving

While a driving session is active: hide/disable the dictation pill (it already fails with `MIC_BUSY`); `NotifyWorker.show()`
defers instead of posting heads-up notifications and posts them after the session stops; on-device Whisper drafts are **off** by
default (server STT is authoritative; this saves heat and battery in a parked-in-the-sun car, and avoids unloading the LLM).

## 6. Connectivity gaps: use the existing resume path

The path already does the right thing for typical gaps: audio goes to the `Outbox`, reconnect sends `hello` with `resume`,
`hello_ack` is authoritative, and the unacked tail replays at up to ~20× real time (5 s window per 250 ms ack). Driving mode
adds no second path. Three gaps found:

1. **Idle watchdog kills long gaps.** After ≥10 min offline (tunnel, dead stretch) the server ends the session, and the client's
   resume gets `session_not_live` → `FATAL` → the session is marked FAILED and the buffered audio is stranded.
   *Fix (server-only, no protocol change):* track when the producer disconnected without `end`; skip the idle end while the
   producer has been gone for less than `producer_gone_grace_s` (proposed **45 min**, a new setting with that default in code, so no
   manifest change). A connected-but-silent producer keeps today's 600 s.
   *Client fallback for anything longer:* treat `session_not_live` on resume as "replay into a new session via the existing
   upload path" (as for a local-only session) rather than FAILED, and title the continuation "… (cont.)". It isn't a new
   path, it's `LiveUploader`'s existing `serverId == null` branch.
2. **Storage.** 115 MB per hour of audio, held until acked. Check free space at start; warn below 1 GB and stop appending below
   200 MB, with a marker and earcon.
3. **Catch-up speed** needs measuring (§8), but isn't a design problem.

## 7. Phases (each ends green on `make test`; TDD per `TDD.md`; conventional commits)

**Spike 0 (first, timeboxed, a throwaway branch, no merge).** On the Pixel with Android Auto Developer mode on:
(a) can the existing Car screen's Start launch a mic FGS on SDK 34? (b) can the already-granted overlay permission make it
allowed? (c) does a notification action while locked? (d) does the assistant "open app" launch work from the lock screen?
(e) do car media keys reach a `MediaSessionCompat` in `LiveSessionService`? The answers decide §5.1's order and whether
Phase 3 is worth doing. **Output: a short results note appended to this doc.**

**Phase 1: resilient session core** (`live/`): mute; `t_ms` from the monotonic clock; `AudioRecord` recreate-with-backoff;
interruption detection (callback + digital-silence detector); marker persistence and resend; storage checks. Pure-logic
parts (silence detector, interruption state machine, marker resend selection, storage thresholds) get JVM unit tests;
`Outbox`/`LiveConnection` get tests on the new marker file. Server: idempotent markers + `producer_gone_grace_s`, with tests
and a regression test for the 10-minute-gap session.

**Phase 2: driving surface:** `DrivingActivity` (Compose, Instrument tokens only, huge Mute / Mark / Stop, status line,
elapsed time, offline/buffered indicator, interrupted banner), `MediaSession` mapping, earcons behind `DrivingAudio`, public
lock-screen notification, car-BT auto-stop, `LauncherRoute` hook, quiet-mode (§5.5), Settings entry (arming, input choice,
timers). Paparazzi snapshots for the activity states (`recordPaparazziDebug`, committed) on the usual 411/851 dp, light and
dark. The design lint applies: no hard-coded colours, sizes or fonts.

**Phase 3 (optional, sideload only): Auto remote.** Re-point `JarvisCarScreen` at `LiveSessionService.ui`: states idle /
recording / muted / interrupted, buttons Start / Mute / Mark / Stop; real `HostValidator` allow-list; fix the manifest. Only if
Spike 0 shows the start path works. Say plainly in the README that it can't be Play-listed.

## 8. Manual checklist: real drive test

Do this on a quiet route with a second person present or a parked stretch for anything marked P (parked). Never operate the
phone while moving. Setup: Pixel Fold, release build, server on lab, car with BT (and Android Auto if Phase 3 is built).

**Before driving**
- [ ] P: Fresh install path: mic permission, overlay, notification permission granted; Driving mode armed in Settings.
- [ ] P: Phone placed (cup holder / bag / pocket / dash mount); note which, and re-run the drive for each.
- [ ] P: Start by voice ("Hey Google, open Jarvis Voice") from the **lock screen**; session starts without unlocking.
- [ ] P: Same via the notification Start action from the lock screen.
- [ ] P: Earcon audible over music and over navigation prompts; music ducks and recovers, nav prompt isn't cut off.
- [ ] P: Steering-wheel Play/Pause mutes/unmutes; Next marks. Music app does **not** hijack the keys. Repeat with music playing.

**While driving**
- [ ] Talk for 5 min. Phone screen stays off, session stays alive, nothing prompts for unlock.
- [ ] Mark 3 times (steering wheel). Each earcon heard; 1.5 s debounce doesn't swallow deliberate marks.
- [ ] Mute (notification action) for 30 s, speak, unmute. The system mic indicator goes off while muted; the muted section is
      silent in the note and bracketed by `muted`/`unmuted` markers; unmute picks up within ~1 s.
- [ ] Receive a **phone call** mid-session, then end it. Interrupted/back earcons; markers present; the recording continues after;
      call audio is not in the transcript; session did not stop.
- [ ] Receive a message/nav prompt/notification sound; no crash, no dictation pill.
- [ ] Airplane mode on 3 min, off. UI shows offline/buffering; back online it shows catch-up and the unacked count drops to 0.
- [ ] Longer gap: airplane mode on 15 min, off. Session still the **same** session (needs `producer_gone_grace_s`), or if the
      fallback is used, a "(cont.)" note exists. No FAILED session.
- [ ] Kill the app process mid-session (`adb shell am kill` or swipe): the audio uploads afterwards, markers intact.
- [ ] If the opt-in car mic is on: quality compared with the phone mic, and what music sounds like during the session.

**Ending**
- [ ] Park, turn the engine off, leave the car: the session auto-stops after 2 min and finalizes.
- [ ] Turn the car off and back on within 2 min: the session continues.
- [ ] A note appears in the vault with markers, in solo mode, titled "Drive …", with the right text across the gap and the
      mute/interruption boundaries.
- [ ] A Kai reminder that was due during the drive arrives **after** it, not during.

**Numbers to write down**
- [ ] Battery drop per hour; phone temperature; free space used per hour.
- [ ] Catch-up time for a 5 min and a 15 min gap (time from reconnect to unacked 0).
- [ ] Which phone placement transcribes acceptably.

## 9. Risks

- Spike 0 may show no in-car start path on SDK 34. Phase 1 and 2 still stand (voice-open and notification start), Auto drops out.
- Media keys depend on the car and the active media app; may need a "claim keys" workaround or may not be reachable.
- Mic quality from a pocketed phone; mitigated by placement guidance and the opt-in car mic.
- Recording conversations in a car has consent and legal implications for passengers; a one-line first-run notice is in Phase 2.
- Server changes (marker idempotency, grace period) ship through the normal lab → live path; migration is additive.

## 10. Open questions for you

1. Mute: is "zero the audio, keep the mic open (privacy dot stays on)" acceptable, or must mute release the mic?
2. Gap behaviour: server grace window (45 min) **and** the client replay-into-new-session fallback, or just the server change?
3. Car mic by Bluetooth: opt-in only (my recommendation), or is that the mode you actually want?
4. Is Android Auto on your head unit worth Phase 3, or is voice-open + steering-wheel keys enough? Does your car have Auto?
5. Auto-stop on car disconnect with a 2 min delay: right default?
6. Mark vocabulary: one "mark" only, or also a "todo" mark on Previous?
