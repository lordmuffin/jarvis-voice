# Plan: hold to ask, hear a short answer

Status: **proposed, awaiting approval. Nothing is built.** R6 in `Tasks.herd`. Written 2026-11-03.
Design: `docs/designs/voice-talkback.md`, corrected by [this section](#corrections-to-the-talkback-design).

## Goal

Mid-session, hold a control, ask, release, and hear a short answer through headphones. The reply is
text on the existing WebSocket, spoken on the device. Done when Andrew can do this on the Pixel Fold
and the Mac, with end of ask to first audio measured.

## What you need to decide

The plan assumes the recommended answer to each. Say so if you want otherwise.

| # | Decision | Recommended | Why it is yours |
|---|---|---|---|
| 1 | **What does a hold do?** | Answer from the session. If the words open with "Jarvis", try filing first through today's voice pass | The voice pass cannot answer anything. It files, and says nothing for the rest. See [What a hold does](#what-a-hold-does) |
| 2 | **What does 2.5 s measure?** | The LLM stage, p95, as the REPLAN says. End of ask to first audio is reported beside it with no bar until R5's numbers exist | The design says 2.5 s p95 end to end. The REPLAN says 2.5 s for the LLM part. Both cannot hold: see [Budget](#budget) |
| 3 | **Do Bluetooth speakers count as headphones?** | Accept Bluetooth audio in this slice and name the risk | Neither platform's audio API tells headphones from a Bluetooth speaker or a car. Telling them apart on Android needs a new runtime permission, and the Mac has no equivalent I can rely on. "Headphones only" is therefore not fully provable. See [Headphones only](#headphones-only) |
| 4 | **Is a held ask allowed in a meeting?** | Yes, as design §4.1 says. "Meeting-silent" means Jarvis never speaks unprompted | On a Mac call, the other people hear your question, because your mic feeds the call. Design open question 5 is still open. Type-to-ask is not in this plan |
| 5 | **How does the Mac build reach the Mac?** | You build the branch in Xcode on the Mac, using `macos/JarvisVoiceMac/TESTING.md` | CI runs `swift test` only. The app is built only for a `macos-v*` tag, and getting one means merging the release PR, which also promotes the server. That is safe, because `VOICE_ENABLED` is false on live, but it is a heavier way to try a build |

## Decision 2026-11-05: Android speaks through the device

**Andrew's decision, replacing "Headphones only" on Android.** On the Fold with nothing connected a
held ask came back "Shown, not spoken: no headphones", and he wants it spoken. So on Android a held
ask's reply is now spoken on whatever the phone's media audio plays on, the built-in speaker
included, **by default** (new installs and upgrades alike).

- **The setting:** Settings → Audio → *Speak answers through*: **This device** (the default) or
  **Headphones only**, which is the old rule unchanged (`OutputRoutes.allClosed`). It is read on
  every reply, so a change applies to the next one.
- **This device:** speaks on any media route Android reports. A route change while speaking keeps
  speaking (pull the earbuds and it carries on from the speaker); only an empty route stops it, shown
  as "Shown, not spoken: no audio output to speak on." Below Android 13 the route cannot be read, so
  both settings stay text only, as before.
- **Played reports are exact,** because EchoGuard trusts them: `started` at the first audio, then
  exactly one close, `done` when audio played (also when it was then interrupted, superseded or
  failed, with the reason) and `skipped` only when nothing played. Losing audio focus now closes the
  window instead of leaving it open.
- **The Mac and Linux are unchanged: headphones only.** Andrew's decision covers Android alone: the
  problem was the phone with nothing connected. On a Mac call a speaker reply would be heard by the
  other people through the mic, which decision 4 already flags for the question itself. On Linux a
  `--system` session hears every stream, the app's own included, so the second defence in [Where the words
  go](#where-the-words-go) does not exist there and the route gate is most of what is left. Neither has been asked for.
- **The platform echo canceller is not switched on.** `AudioCapture` creates an
  `AcousticEchoCanceler` but leaves it as the platform set it. Enabling it only while a reply plays
  would change the recording mid-stream (the filter must converge, so the first words leak) and treat
  speech over Jarvis differently; enabling it always would change what every session records. A Fold
  experiment comparing echo drops with it on is the way to decide, not a JVM test.

## What I found

Read from the code and the cluster on 2026-11-03. Figures are labelled by how far they can be trusted.

| Fact | Evidence |
|---|---|
| Lab's voice model today: **`qwen3.8-flash-next`** (`VOICE_MODEL` empty, so the copilot's). Lab runs `sha-3d5af64` | Lab's ConfigMap and Deployment |
| **The voice pass cannot answer a question.** Target `none` returns `None`, and the speaker hears nothing | `voice/service.py:138` |
| **R2's voice eval does not cover an answer.** It runs `VOICE_SYSTEM` and `VoiceRequest` on 25 filing cases. So R5's 2.5 s, as written, would not be measured on what a hold sends | `server/tests/evals/voice.py` on `chore/interactive-model-eval` |
| The only live Whisper tier is `gamingpc`. `spark`, `pve` and `kokoro` have 0 replicas | Cluster |
| Whisper on real segments: mean 1.21 s on lab and 0.93 s on live, p95 about 1.9 s on both. Mic segments are about 5.5 s at p50. **Thin:** both pods restarted at 02:12, so this is about 200 segments, in coarse buckets | Prometheus |
| `whisper-server` runs with no parallel setting, and I expect it to work serially. It is shared with live. **Unverified.** If so, an ask queues behind any segment in flight | Homelab manifest. [Task 6](#delivery) measures it |
| Each `gamingpc` success in that window came with one failed try on `spark` and `pve`, about 0.1 s in total. `tiers.py` as read should not do that, since `gamingpc` was healthy. **Unexplained**, small, and looked at in [Task 2](#delivery) | Prometheus, `stt/tiers.py` |
| Flash has no measurement that fits. Voice on the gateway was p50 17.21 s, but thinking on, at about 393 tokens. The copilot's whole request ran at 24 tok/s. **Expect seconds, and a queue.** | `docs/plans/llm-path-latency.md`, `fast-tier.md` |
| `features=` on the stream URL already gates server messages, and the bus filters on it. Swift asks for `speaker_update`. **Android asks for nothing yet** | `bus.py`, `LiveConnection.swift:253`, `LiveApiClient.kt:220` |
| An unknown client message closes the socket (`bad_message`). So a new client must not send a new message to an old server | `api/stream.py:213` |
| Frames carry `t_ms`, but the segmenter's positions count samples. On the Mac the two differ by the first frame's offset | `protocol.py`, `segmenter.py`, `Pipeline.swift` |
| Control messages are sent before audio and survive a reconnect, on both clients | `LiveConnection.kt:296`, `LiveConnection.swift:325` |
| `HotKeys.swift` listens for key press only. Hold needs the release event too | `HotKeys.swift` |
| `AudioDevices.defaultOutputUID` reads the system **alert** device, which is not where playback goes | `AudioDevices.swift:38` |
| `InstButton` on Android has `onClick` only | `ui/compose/InstComponents.kt:73` |
| `TtsRepository` may pick a default voice that needs the network. That would send reply text to a third party | `chat/TtsRepository.kt` |
| Capture needs no change: an ask reuses the mic stream. There is no audio-focus or output-route code in the Live path | grep |

## How it works

```
hold   client: ask_begin {id, t_ms} ──────────┐
       mic frames keep flowing, as today      ├─ server keeps that mic audio in memory
release client: ask_end {id, t_ms} ───────────┘
                                                wait for the last frame
                                                Whisper, directly (no segmenter, no 3 s wait)
                                                "Jarvis ..."?  file it : answer it
       ask_reply {id, outcome, text} <───────── one LLM call, whole reply, no streaming
speak  route closed? on-device TTS : text only
       ask_played {id, started, latency_ms} ──> metric, stored row, echo window
```

The normal pipeline carries on untouched. The audio is still stored and transcribed.

### What a hold does

**Recommended, decision 1:**

- **Words that do not open with the wake phrase are a question.** A new `AskPass` sends them with the
  last 180 s of transcript. For the owner it also sends a few passages of their Notes vault: see
  [`voice-ask-vault.md`](voice-ask-vault.md). It gets `{reply}` back and nothing else: **it has no way to file
  anything.** The transcript holds other people's words, so joining it to filing in one call would
  let a caller talk Jarvis into proposing work, and `voice-commands.md` rules 2 and 3 forbid that.
- **Words opening with "Jarvis" go to `VoicePass.run` unchanged:** same prompt, same gates, no
  transcript, same approval step. Its reply is spoken. If it answers `none`, they go to `AskPass`,
  at the cost of a second LLM call. So the plain way to ask is to just ask.
- **Wake-phrase speech with no key works as today.** Its reply stays a copilot notice, and it is
  **never spoken**. Only a held key produces speech, which is what makes meeting silence structural.

`VoiceLoop` must skip lines inside an ask's window, or a held "Jarvis, ..." is filed twice.

### Protocol

Added to `protocol/v1`, with schemas, fixtures, and server, Swift and Android parsing, in one PR.

| Direction | Message | Fields |
|---|---|---|
| Client to server | `ask_begin` | `{id, t_ms}` |
| Client to server | `ask_end` | `{id, t_ms}` |
| Client to server | `ask_played` | `{id, state: started\|done\|skipped, t_ms, latency_ms?, reason?}` |
| Server to client | `voice_state` | `{available}`, feature `voice_ask` |
| Server to client | `ask_reply` | `{id, outcome: answer\|filed\|empty\|no_stt\|timeout\|failed, text}`, feature `voice_ask` |

**How old and new meet.** A client adds `voice_ask` to `features`. Only then does a server send
`voice_state`, on hello and when eligibility resolves. **The control appears only after
`available: true`, so a new client never sends `ask_begin` to an old server,** and an old client
never sees either message. Lab and live can differ safely.

`t_ms` uses the audio frames' clock, not a second clock. The server maps it to stream position
through the frames it saw, which also fixes the Mac's offset.

Reconnects: asks are never resent. Both clients drop queued ask messages at reconnect, and a late
`ask_reply` is shown but not spoken.

### Server

New files in `server/src/jarvis_live/voice/`. All of it runs off the audio path.

| Piece | Does |
|---|---|
| `ask.py`, `AskDesk` | One per eligible session, on `SessionIngest`. `feed()` is a synchronous append from `_producer_loop`, keeping 300 ms before the key so the first word survives. It waits up to 400 ms for the last frame, since `ask_end` can overtake it. Caps a hold at 30 s and frees audio at once. A new ask cancels one in flight |
| `ask_pass.py`, `AskPass` | STT, then the routing above, then `complete_json` with `think=False` and a `voice_ask_timeout_s` of 20 s. Reuses `is_hallucination`. VAD finds no speech: `empty` and no LLM call |
| `spoken.py` | Pure: strip markdown, cap at 40 words on a sentence boundary |
| `echo.py` | Pure: a segment overlapping the played window, plus 300 ms, with token overlap of at least 0.6 is dropped. Real speech over Jarvis is kept |

- **Eligibility:** `voice_enabled` and `_ideas_eligible`, the same as the voice loop. That means off
  for local-only, off for restricted sessions, and owner and admins only. **No new on/off setting:**
  R7's `VOICE_ENABLED` turns this on with the rest. The new settings are only the tunables here.
- **Whisper away:** `no_stt`, and the client says "Transcription is offline". No retry, no queue.
- **Session end:** asks in flight are cancelled in `_end`.
- **Stored:** a `copilot_items` row, kind `voice_ask`, with question, reply, outcome, model, tier and
  stage times. Retention and backup already cover that table. It is never in the transcript, the
  copilot snapshot, or the note.

### Where the words go

- **Jarvis's speech: nowhere in the transcript.** No `jarvis` speaker, no `kind: ask`, no segment,
  and no copilot notice, so it never feeds the copilot's prompt.
- **Your question stays as ordinary `me` speech,** because it is in the stored audio and the room
  heard it. It reaches the copilot and the note like anything else you say.
- **The text** shows on the client that asked. The dashboard does not show it in this slice.

~~Three defences keep speech out: the [route gate](#headphones-only), playing from our own process on
the Mac, and `echo.py`. The design's text filter runs on both channels, because Mac system audio is
where a leak would land.~~ **Replaced for Android on 2026-11-05** (see
[the decision](#decision-2026-11-05-android-speaks-through-the-device)); it still holds for the Mac.

**On Android, under "This device" (the default), the route gate is gone,** so the reply from the
speaker reaches the session's own mic. What keeps it out now:

1. **The echo guard** (`EchoGuard` in `voice/ask.py`): a segment that overlaps the played window,
   plus 300 ms, and whose words are at least 60 % the reply's is dropped. Speech over Jarvis is kept.
   It is now the main defence, not the last one.
2. **Exact played reports.** The window is the server's stream position when `started` and the
   closing report arrive, so a missing or late close would leave it open, and a missing `started`
   would leave no window at all. The client sends `started` at the first synthesized audio (slightly
   before sound, the safe side) and one close after playback, on every path.
3. **Only one reply plays at a time,** and a new ask stops the last, so windows do not stack.

The residual risk is a reply that STT garbles past the 60 % overlap, or whose tail lands in a segment
the owner keeps talking into: those words reach the transcript. Under "Headphones only" the old route
gate applies as before.

On Linux the second defence does not hold: `--system` records the default sink's monitor, which
hears every stream, the app's own included. So a Linux session that records system audio is text
only, as the daemon's `status` reports it, and the route gate and `echo.py` are the defences left.

### Headphones only

On Android this table is now the **Headphones only** setting, no longer the default: see [the
decision](#decision-2026-11-05-android-speaks-through-the-device). The Mac and Linux columns still
apply as written.

| | Android | macOS | Linux |
|---|---|---|---|
| Read the real route | `getAudioDevicesForAttributes` (API 33+). Below that, text only | `kAudioHardwarePropertyDefaultOutputDevice`, transport type and data source | `pw-dump`: the default sink, its device's form factor, bus, profile and active port |
| Closed | Wired, USB headset, A2DP, BLE headset, hearing aid | Headphone jack, Bluetooth, USB | Headphone jack reporting a plug, USB headset or headphones, Bluetooth headset or headphones on A2DP or HFP |
| Never | Built-in speaker, earpiece, BLE speaker, HDMI, anything unknown | Built-in speakers, HDMI, DisplayPort, AirPlay, anything unknown | Speakers, HDMI, DisplayPort, USB interfaces and line outs (no form factor), virtual sinks, LE Audio, anything unknown, and every `--system` session |
| Route changes while speaking | `AudioDeviceCallback`: stop at once | Property listener: stop at once | Polls `pw-dump` every 0.1 s: stop at once. The stream is pinned to the sink with `node.dont-reconnect` |

**The check fails closed.** The route decides from a pure function on each platform, tested on every
type. With no closed route the answer is text only, reported as `skipped: route`.

**Residual risk, decision 3:** a Bluetooth speaker or car reads as A2DP. A USB speaker on the Mac reads
as USB. On Linux a Bluetooth speakerphone that reports form factor headset (the
Anker PowerConf S3 does) reads as closed; a device that says `speaker` is refused.

### Android

- `AskSpeaker` in `LiveSessionService`, warmed at session start, because a cold engine costs seconds.
  It extends `TtsRepository` with `USAGE_MEDIA` plus `CONTENT_TYPE_SPEECH`,
  `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK`, and **offline voices only: no offline voice, no speech.**
- `LiveConnection`: asks for `voice_ask`, sends and drops ask messages as above.
- **The control:** an on-screen hold button in `RecordControls`, both layouts, as `InstButton` with a
  press and release gesture. It adds no new visual, so nothing goes to lordmuffin/instrument.
  Disabled while muted, with a TalkBack action. State text uses `InstText` and `InstChip`.
- Paparazzi images re-recorded.
- **Not in this slice:** headset button, Quick Settings tile, watch. A headset button is a toggle, not
  a hold. It is the obvious next step for a phone in a pocket.

### macOS

- **`AVSpeechSynthesizer.write(_:toBufferCallback:)`, never `speak`.** Rendered buffers play through an
  `AVAudioPlayerNode` on an engine in our own process, so the system tap's process exclusion
  applies. `speak` is believed to render in a daemon, which the tap would capture as `them`.
- `HotKeys` gains the release event and a hold key, **⌃⌥A**, beside ⌃⌥J and ⌃⌥M. It needs no permission.
- State in the Copilot panel, with `InstChip` and `InstLivePill`.
- Route classification lives in `JarvisLiveKit`, so `swift test` covers it. New rows in `TESTING.md`.
- **Unverified until tried on hardware:** how a second engine behaves beside `MicCapture`'s voice
  processing, including ducking.

### Linux

- `jarvis-voice` drives the headless client's daemon (`ask_begin`, `ask_end`, `ask_played` on its
  control socket); the daemon holds the stream and the `voice_ask` feature. No second client.
- **Piper** (`piper-tts`, optional `speech` extra, GPL-3.0, not vendored) with
  `en_US-lessac-medium`, SHA-256 pinned in `jarvis-voice models install`, synthesised in the app a
  sentence at a time into a `pw-cat` child it owns. Warmed on the session's first `voice_state`.
- **The key:** Right Alt held alone past 0.2 s, or Right Meta when dictation has Right Alt;
  configurable, never dictation's key. No cancel once begun.
- **Not yet reachable:** the evdev monitor, pill and app loop are `linux-desktop.md` PRs 4 to 7.
  The ask flow, route gate and speaker are tested with fakes; the hardware rows are in
  `linux/TESTING.md`.

### Local-only

**Hard off, by three independent routes.** The server never makes a local-only session eligible. The
control needs `voice_state` from a socket, and a local-only session has none. `EgressGate` cutting a
live connection clears `available`, stops playback and discards an ask. There is no on-device
fallback, and the client states why: "Local only: Jarvis can't hear or speak."

## Measuring it

**End of ask** is release on the client. **First audio** is the first audio handed to the output. Both
are on the client's monotonic clock, so no clock sync is needed. The client sends the difference.

| Stage | Where | Label |
|---|---|---|
| Waiting for the last frame | Server | `wait` |
| Whisper | Server | `stt` |
| LLM, the REPLAN's 2.5 s | Server | `llm` |
| Delivery and speech start | Client total minus the above | `client` |
| **End of ask to first audio** | Client | total |

- Metrics: `voice_ask_seconds{stage}`, `voice_ask_total{outcome}`, and `voice_ask_first_audio_seconds`.
  `stage` joins `ALLOWED_LABELS`, a closed set. Every stored row names its model.
- Android: first `onAudioAvailable`, because `onStart` fires before synthesis finishes. macOS: first
  buffer scheduled.
- **Known blind spot:** neither sees Bluetooth output delay, likely 100 to 250 ms (an estimate). So
  once per platform, 5 asks are timed from a separate recording, and the offset is reported.
- `jarvis_live/ask_report.py` prints p50 and p95 by nearest rank, per stage, per model, per device.
- **The run:** 30 asks on each device, on lab, on headphones, in a real session with speech flowing.
  Half are run while segments are in flight, to expose a Whisper queue.
- **Reported as measured on `qwen3.8-flash-next`, or whatever lab has then, named.** It is not judged
  against 2.5 s. No SLO or alert until R5.

## Budget

For R5's model, assuming decision 2. Nothing below is measured on an ask yet.

| Stage | p95 | Basis |
|---|---:|---|
| `ask_end` to last frame | 0.2 s | 100 ms frames, plus the network. Estimate |
| Whisper, 3 to 5 s clip | 1.5 to 2 s | Thin sample above, on longer clips. Excludes any queue |
| LLM | 2.5 s | R5's bar |
| Delivery | 0.1 s | Estimate |
| Speech start, on device | 0.3 s | **Unknown** |
| **Sum** | **4.6 to 5.1 s** | Summing p95s overstates it. Expect about 4 s |

**So the design's 1.5 s p50 and 2.5 s p95 end to end cannot be met** on Whisper as it runs today.
Whisper alone uses over half of that. Streaming would not rescue it, because speech cannot start
before Whisper is done. If the full figure matters once measured, the levers in order are: use the
draft words the client already has on device for the ask, then give Whisper a second context.

A timeout at 20 s is deliberately generous for Flash on lab. An answer later than that has lost its
point. Tighten it after R5.

## Corrections to the talkback design

Applied to `docs/designs/voice-talkback.md` in Task 1, with a status note at its head.

| § | Says | Now |
|---|---|---|
| 0, 2, 5, 8 | TTS on the Spark behind a tier list, Kokoro first, PCM downstream | **On-device: Android `TextToSpeech`, macOS speech synthesizer.** No Kokoro, no GPU for TTS, no `tts/tiers.py`, no binary downlink, no channel `2`, no `PcmPlayer` |
| 1.1, 5.2 | Kokoro is deployed, at a known address | `kokoro` is at 0 replicas. Keep the PCM finding as history. The address no longer applies |
| 1.3, 5.1 | `pve` is the everyday STT tier, `spark` the best | Whisper is on the gaming PC only. Live may depend on it, and says so when it is away (REPLAN, R3) |
| 5.3 | VRAM tables, the Spark as a capacity question | Removed: no TTS on a GPU. pve's card goes to `interactive` |
| 5.1 | Stream the LLM, sentence-split, stream TTS | One non-streaming call, a whole reply capped at 40 words, spoken once it arrives |
| 5.1 | 1.5 s p50, 2.5 s p95 end to end | See [Budget](#budget), subject to decision 2 |
| 2.1 | `hello.features` | The stream URL's `features`, which exists |
| 2.1, 3.3, 7 | `speaker: "jarvis"`, `kind: "ask"`, Jarvis as a transcript entry | **Removed.** Jarvis's speech stays out of the transcript |
| 2.1 | `speak`, `speak_state`, `talkback` messages | `ask_reply`, `ask_played`, `voice_state`. Mute stays local |
| 5.4 | No TTS tier: stay silent, one status event | Speech failure: text only, reported as `skipped` |
| 7 | Task 6 still open, gate to be built | `EgressGate` exists on both clients. Note `local-only-audit.md`: local-only is a fallback, not a privacy barrier. Talkback stays hard off anyway, as you asked |
| 8 | Phase 1 speaks suggestions on tap | Skipped. R6 goes straight to the ask. Suggestions on tap stay later work, as do Phases 3 and 4 |

`voice-commands.md` gets its status and out-of-scope lines updated in Task 7.

## Delivery

Three PRs, in order, each following `TDD.md` and each passing `make test`. Lab gets each on merge to
`main`. Lab already has `VOICE_ENABLED`, so no manifest change is needed to switch this on.

| Task | Change | PR | Proved by |
|---|---|---|---|
| 1 | Schemas, fixtures, server models, `voice_ask` feature, Swift and Android parsing, design corrections | 1, `feat(server)` | Fixture suites on server and Swift, old-client test |
| 2 | `AskDesk`: buffer, pre-roll, last frame, caps, position mapping. Check the dead-tier tries | 1 | `test_voice_ask.py` |
| 3 | `AskPass`, routing, `spoken.py`, stored row, metrics, `ask_report.py` | 1 | Real orchestrator test: audio in, reply out. Transcript-instruction test: a line saying "file a Pi session" files nothing |
| 4 | `echo.py`, `VoiceLoop` skipping ask windows | 1 | Includes real speech over Jarvis **kept**, and a held "Jarvis, ..." filed once |
| 5 | Ask eval: 12 invented cases, its bar stated before any model runs, beside R2's | 1 | Offline test. Quality run on local CPU, as R2's rules |
| 6 | Android: `AskSpeaker`, route gate, connection, hold control | 2, `feat(android)` | Unit tests, Paparazzi, 30-ask run on the Fold, Whisper queue check |
| 7 | macOS: hotkey, speech, route gate, panel state, `TESTING.md` | 3, `feat(macos)` | `swift test` in CI, your hardware rows, 30-ask run on the Mac |
| 8 | Results in this plan, `voice-commands.md` updated | With the last PR | The tables below |

**Required tests, from the brief and the design:**

| Requirement | Test |
|---|---|
| Meeting-silent | A meeting session with the copilot emitting suggestions and wake-phrase replies yields zero `ask_reply` |
| Local-only | No `voice_state`, no ask task, and `ask_begin` on such a session does nothing |
| Out of the transcript | After an ask, no segment, snapshot or note holds the reply |
| Empty and long holds | Release before speech, and a 60 s hold, both end cleanly |
| Reconnect | No ask message is resent |

**I cannot do these myself:** the 30-ask runs, and the Mac build. There is no Swift or Xcode on this
machine, so Mac code is checked by CI's `swift test` only until you run it, and no device is attached
to adb. CI uploads a debug APK on each PR, which installs on the Fold. Expect some back and forth on the Mac.

## Risks

- **Whisper is shared with live and may be serial.** A hold during heavy speech waits, and asks add
  one request each to a card live also uses.
- **Lab's Flash may time out at 20 s under agent load.** That is a true result, and gets reported.
- **`write(_:toBufferCallback:)` has had reliability trouble in the past.** If it fails, the Mac shows
  text only until that is fixed, because falling back to `speak` risks Jarvis landing in the transcript.
- **Coordination.** `fix/server-coverage` may add voice tests. R2 owns `tests/evals/voice*.py`, so the
  ask eval goes in new files. `make test-all` fails on `main` at the coverage ratchet until that
  fix lands, so I compare against base.

## What other tasks need from this

- **R5:** its done test measures `VoicePass`'s request. A hold usually sends `AskPass`'s, with 180 s of
  transcript. **Add the ask eval to R5's done test,** or 2.5 s is judged on the wrong request.
- **R7:** `VOICE_ENABLED` now also turns on hold to ask, on live. Its brief should say so.

## Out of scope

Unprompted speech, speaking suggestions, barge-in beyond a new hold cancelling, wake word, headset
button, watch, Kokoro, F5, the dashboard, and any change to voice's approval rule.

## Results

Filled in by Task 8.

| Device | Model | n | LLM p50 / p95 | Whisper p50 / p95 | End of ask to first audio p50 / p95 | Output offset |
|---|---|---:|---:|---:|---:|---:|
| Pixel Fold | | | | | | |
| Mac | | | | | | |
| Linux desktop | | | | | | |
