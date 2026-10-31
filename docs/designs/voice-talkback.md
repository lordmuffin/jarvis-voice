# Voice talkback for Live sessions — design

Status: **design only, no code.** Date: 2026-10-30. Backlog item: Tasks.txt #7.

Goal: while a Live session runs on the Pixel Fold or the Mac, with headphones on, (a) hear Jarvis speak short responses or suggestions, and (b) ask it a question by voice mid-session.

## 0. Summary and recommendations

| Question | Recommendation |
|---|---|
| Where does TTS run? | Server-side, on the Spark, behind a tier list like `WHISPER_TIERS`. Kokoro first. Audio streams down the **existing Live WebSocket** as PCM. No client-to-TTS direct HTTP. |
| How does the user ask? | **Push-to-talk first.** The client sends `ask_begin` / `ask_end` markers on the audio it already streams. The release is the endpoint, so there is no VAD wait. No wake word in v1. |
| Reuse Android chat AEC and barge-in? | **No, not for barge-in.** The chat code itself turns auto barge-in off for Kokoro (§3.2). Reuse the playback and sentence-splitting pieces, not the barge-in. |
| Keeping Jarvis out of the transcript | Headphone-only gating, plus server-side tagging of the playback window, plus a text-similarity echo filter. No reliance on acoustic AEC. |
| Unprompted speech | **Meeting: never.** Solo: allowed, throttled by a pure policy function keyed on the `mode` flag. Any route change away from headphones mutes. |
| Local-only | Talkback is **hard off**: no ask, no speak, no on-device LLM fallback. Gated at the same transport layer as task 6. |
| First slice | Phase 1: "speak this suggestion" on tap, headphones only, meeting-silent by construction. |

Latency, VRAM and a few Android and macOS behaviours below are **estimates or unverified** and are labelled that way. Phase 0 exists to replace them with measurements.

### What I read, and what I did not

- Read in full: the three vault notes (LiveKit Voice Agent — Session Notes and Final Solution; F5-TTS Voice Clone — Implementation Plan; Andrew Voice Clone — Research and Recommendation). All are dated 2026-05-27.
- Read in the repo: `chat/NetworkTtsRepository.kt`, `chat/TtsRepository.kt`, `chat/SentenceSplitter.kt`, `AudioDeviceRouter.kt`, `live/AudioCapture.kt`, `live/VadSegmenter.kt`, `VoiceChatActivity.kt` (the barge-in section), `stt/tiers.py`, `configmap.yaml`, `protocol/v1/README.md`, the macOS `MicCapture.swift` and `SystemCapture.swift` headers, `copilot/prompts.py` (mode usage), and the manifest's foreground-service types.
- **Not read:** the np-home-homelab repo (so I have not seen the Spark's real profile and role definitions), `ingest/segmenter.py` and `ingest/pipeline.py` internals, `copilot/loop.py` in full, `DictationController.swift`, and `LiveSessionService.kt` beyond a grep. Tasks 5 and 6 are still open in Tasks.txt, so their mode flag and transport gate do not exist yet. I design against their stated scope.
- Not verified: whether the LiveKit agent from May is still running. The vault note lists open items (Traefik route, DNS), so treat it as a reference, not a live dependency.

## 1. What already exists

### 1.1 From the vault (reuse)

1. **Kokoro returns raw PCM.** The LiveKit note found that the MP3 path failed to decode (`av` 17.0.1) and that `response_format: "pcm"` (24 kHz, 16-bit, mono) works. Their conclusion: *"This PCM pattern should be the default for any Kokoro integration."* Adopt it. PCM also streams straight into an `AudioTrack` or `AVAudioPlayerNode` with no decoder.
2. **Persona rules for spoken output:** concise, lead with the so-what, short sentences, no markdown. Reuse as the talkback system prompt.
3. **Anthropic needs a user-role message.** A system-only `generate_reply` returned 400. The talkback LLM call must always carry a user turn. The note also dropped the "connected" greeting. Keep that: Jarvis never announces itself in a session.
4. **Kokoro endpoint pattern.** `KOKORO_URL` / `KOKORO_VOICE` env config, and the OpenAI-shaped `POST /v1/audio/speech`. The Android chat path already uses the same shape (`NetworkTtsRepository.kt`), so one server contract serves both.
5. **Existing Kokoro instance:** `http://192.168.1.43:8880`, voice `af_sky` (vault, May 2026). Whether it is still there, and whether it is on GPU, is unknown (open question 1).

### 1.2 From the vault (do not reuse as-is)

- **F5-TTS wrapper.** The plan shells out to `f5-tts_infer-cli` per request and writes `/tmp/f5_out/out.wav`. That reloads the model on every call and is non-streaming, so it cannot hit 1.5 s. F5 is a possible Phase 4 voice, behind a persistent-model server, not a Phase 1 dependency. Its stated purpose is Andrew's voice for podcast lines. Using your own voice as the assistant's is a product decision, not an engineering default (open question 2).
- **LiveKit/WebRTC as the transport.** It would give turn-taking, jitter buffering and WebRTC echo cancellation. It would also add a second transport beside the Live WebSocket (resume, outbox, durable PCM), double-transcribe the mic, and give task 6 a second path to gate. Recommendation: **do not adopt LiveKit for Live.** If the headphones-only premise is ever dropped, linking WebRTC's audio processing module into the clients is the real way to get software echo cancellation, and the premise should be revisited then.

### 1.3 In the repo

| Fact | Evidence |
|---|---|
| Live has no TTS on server, Android or macOS. | grep; Tasks.txt #7 |
| Android chat TTS: remote Kokoro (`NetworkTtsRepository`) or on-device `TextToSpeech` (`TtsRepository`). Kokoro is fetched as one **whole WAV** (`response.body?.bytes()`) before playback starts. | `NetworkTtsRepository.kt` |
| It plays with `USAGE_MEDIA`, and explicitly prefers an A2DP/BLE output device. | `NetworkTtsRepository.kt`, `preferredBluetoothOutput()` |
| **No audio-focus handling anywhere in the Android app** (no `requestAudioFocus`/`AudioFocusRequest`). | grep over `android/app/src/main` |
| Live Android capture: `AudioRecord(VOICE_RECOGNITION)`, 16 kHz mono, 100 ms frames, AEC + NS attached when available. **Mic channel only.** | `live/AudioCapture.kt:54,66`; `LiveSessionService.kt:182` |
| Live Android endpointing: energy VAD (`silenceRms = 200`) that commits after **3 s** of silence. | `live/VadSegmenter.kt` defaults |
| macOS mic: `AVAudioEngine`, voice processing enabled when it can be, with a fallback flag `voiceProcessingActive`. | `MicCapture.swift:24,54` |
| macOS system audio: a Core Audio process tap that **excludes this app's process**. | `SystemCapture.swift:54` |
| Protocol: `session_create_request.mode` is `meeting` or `solo`. Binary frames go client → server only. `segment.speaker` is `me` or `them`. All JSON objects reject unknown fields. | `protocol/v1/README.md` |
| `mode` reaches the **final-note and reduce prompts only** (`prompts.py:81,89`). I found no use of it in the live copilot loop. | grep of `copilot/loop.py` |
| STT: ordered tier list `spark` / `pve` / `gamingpc`. The Spark tier is down whenever its active profile parks the whisper role. The everyday tier is `pve` (an RTX 2070). | `configmap.yaml:12-22`; `stt/tiers.py` |
| Foreground services declare `foregroundServiceType="microphone"`. | `AndroidManifest.xml:164,169` |

## 2. How talkback fits the pipeline

Today: `client mic → WS binary frames → server ingest → segmenter → Whisper tier pool → segment → copilot loop → copilot snapshot → client`.

Talkback adds a **downlink speech path** and an **ask path**. Both ride the existing socket.

```
                 ┌──────────────── Live WebSocket (one socket, one gate) ────────────────┐
 mic ─ frames ─▶ │ ingest ─▶ segmenter ─▶ STT tiers ─▶ segments ─▶ copilot loop           │
 ask_begin/end ─▶│            │                             │                              │
                 │            └─ ask cutter ─▶ STT ─▶ talkback agent (LLM, session ctx)    │
                 │                                   │            │                        │
                 │ policy(mode, silence, caps) ◀─────┘     sentence splitter              │
                 │            │                                   │                        │
                 │            └──────────▶ TTS tiers (Spark Kokoro) ─ PCM ─▶ speak frames │
 headphones ◀─ PcmPlayer ◀── speak / speak_end / speak_cancel ◀──────────────────────────┘
```

Design rules:

1. **Everything over the Live socket.** The Android chat path calls Kokoro directly from the phone. For Live, do **not** copy that. A direct client-to-TTS call is an egress path that a transport-level local-only gate (task 6b) would not cover. One socket means one gate.
2. **The server decides what to say and when; the client only plays and reports.** Policy, throttling and mode logic live in one testable server module.
3. **Speech is real-time, never durable.** No resend after a reconnect. Every `speak` carries an expiry (like `suggestions.expires_at_ms`); stale speech is dropped, not played late.
4. **Ask reuses the audio already streamed.** PTT does not open a second capture. The client marks an interval; the server cuts that interval from the mic channel and transcribes it right away.

### 2.1 Protocol additions (proposal, subject to the repo rule)

Per CLAUDE.md, `protocol/` changes need fixtures plus server **and** Swift tests in the same PR. Sketch:

| Direction | Message | Fields |
|---|---|---|
| client → server | `hello` | adds `features: ["talkback_v1"]`. `hello` rejects unknown fields today, so old servers and clients need a schema change plus fallback. |
| client → server | `ask_begin` / `ask_end` | `{id, t_ms}` |
| client → server | `speak_state` | `{id, state: started \| done \| interrupted, t_ms}`. Gives the server the **real** playback window for echo handling. |
| client → server | `talkback` | `{muted, unprompted}`. Mute is a first-class message, not a UI-only flag. |
| server → client | `speak` | `{id, kind: answer \| suggestion, text, sample_rate: 24000, expires_at_ms, source_id?}` |
| server → client | binary downlink frame | same 12-byte header, new channel code `2` (tts), `t_ms` = offset in the utterance, PCM16LE mono 24 kHz. This changes the README's "client → server" framing statement and needs fixtures. |
| server → client | `speak_end` / `speak_cancel` | `{id}` |
| server → client | `segment.speaker` | add `"jarvis"`; add `kind: "ask"` for the user's question. |

### 2.2 Server components (all new, all under `server/src/jarvis_live/`)

- `tts/tiers.py` — copy the shape of `stt/tiers.py` (`parse_tiers`, `TierPool`, probe loop, fall-through). Config `JARVIS_LIVE_TTS_TIERS` in the same `<name> <endpoint> <model>` format. Any Kokoro-compatible endpoint is a tier. Probe path to confirm in Phase 0 (`stt` probes `/models` then `/health`).
- `talkback/policy.py` — a **pure function** `decide(mode, now, last_user_speech_ms, last_spoken_ms, spoken_count, suggestion) -> Speak | Skip(reason)`. This is the TDD target.
- `talkback/agent.py` — ask → STT → LLM (LiteLLM alias `fast-chat`, the copilot's) with the transcript so far and copilot state → sentence-split → TTS. Reuses the `SentenceSplitter` idea (`chat/SentenceSplitter.kt`) in Python.
- `talkback/echo.py` — the transcript echo filter (§3.4).
- Metrics for task 9: `talkback_ttfa_seconds{kind,tier}`, `talkback_spoken_total{kind}`, `talkback_suppressed_total{reason}`. No per-session labels.

## 3. Input and echo

### 3.1 Push-to-talk vs wake word vs barge-in

| | Push-to-talk | Wake word | Barge-in |
|---|---|---|---|
| Latency to start of ask | Instant; the key *is* the endpoint | Detector delay + word | n/a |
| False triggers | None | **Other people saying "Jarvis"** in a meeting; system-channel audio on Mac | Own TTS bleed, room noise |
| Fits meeting mode | Yes (explicit) | Poorly | Only if Jarvis is speaking, which it isn't in meetings |
| Hands-free | No | Yes | Yes |
| Cost | A control | Always-on detector, licence or model, tuning | VAD tuned against Jarvis's own voice |

**Recommendation:** PTT in Phases 1–3. Defer wake word to Phase 4, solo mode only, mic channel only. Barge-in starts as **tap to interrupt** (same control as PTT) and becomes acoustic only under the headphone gate (§3.3).

PTT also solves the endpoint problem. The current Live VAD commits after 3 s of silence, which alone would blow the 1.5 s budget. A key release gives the endpoint for free. A separate fast endpointer is only needed for hands-free (Phase 4).

PTT controls (open question 4):

- **Android:** headset media button via a `MediaSession` (toggle: first press begins, second ends), a Quick Settings tile, and an on-screen hold button. A notification action cannot be hold-to-talk. A Wear OS companion already exists (`wear/`), so a watch PTT is a later option.
- **macOS:** a global hold-to-talk hotkey. The app already has a dictation hotkey and HUD, so the plumbing likely exists (not read; verify).

### 3.2 Can Live reuse the Android chat AEC and barge-in? No.

The task statement says the chat path has AEC and VAD barge-in. That is true only in a limited sense. The code says so itself, at `VoiceChatActivity.kt:376-384`:

> Skip auto-barge-in when Kokoro (NetworkTTS) is active — hardware AEC only references VOICE_COMMUNICATION streams, not USAGE_ASSISTANT, so Kokoro's speaker output bleeds through the mic and self-triggers. The user can still tap btn_talk to interrupt manually.

and the guard is `!viewModel.hasNetworkTts()`. So with Kokoro, the one TTS path we want, auto barge-in is **off** and only tap-to-interrupt works. Why Live cannot simply inherit it:

1. **The AEC reference does not match.** Both the chat engine (`SherpaOnnxSpeechEngine.kt:113`) and Live (`AudioCapture.kt:54`) record from `VOICE_RECOGNITION` and attach an `AcousticEchoCanceler`. Kokoro plays as `USAGE_MEDIA` (`NetworkTtsRepository.kt`). Per the code comment, that AEC does not use it as a reference. Effect availability is also device-dependent. Pixel behaviour for this combination is **unverified** and is a Phase 0 measurement.
2. **Switching to `VOICE_COMMUNICATION` is not a free fix.** It changes the audio mode, the BT profile (A2DP → SCO, call-quality audio) and ducking, and may change capture gain and processing that the Whisper tiers have been tuned on. The Live recording is also the durable artifact. I would not change its source to help talkback.
3. **The barge-in is welded to an Activity.** It is a "shadow mic" restarted via `SpeechEngine.startListening` on a main-thread handler. Live runs in a foreground service with a continuous capture thread. They share neither lifecycle nor audio path.
4. **Live's VAD is the wrong detector.** `VadSegmenter` is an energy threshold (`silenceRms = 200`) tuned for utterance commit, not for distinguishing a person from speaker bleed.

What **is** reusable from the chat path:

- `NetworkTtsRepository`'s `AudioTrack` playback, `preferredDevice` routing and immediate `stop()` — but it must be rewritten to stream PCM, not wait for a whole WAV (§5.1).
- `SentenceSplitter` (`chat/SentenceSplitter.kt`), the token-flow-to-sentence logic. Port the idea server-side.
- `AudioDeviceRouter` for input device selection and the SCO helpers.

### 3.3 Keeping Jarvis out of the transcript

The premise is headphones, so the acoustic loop is mostly closed. Do not rely on AEC; layer cheap, independent defences:

1. **Route gate (primary).** Talkback speaks **only** when the output route is a closed device: wired headphones, USB headset, BT A2DP or BLE headset. If the route is the phone/laptop speaker, do not speak. A route change away from headphones mid-utterance cancels playback immediately and mutes talkback. This also prevents the worst failure: a Bluetooth drop that sends Jarvis out of the phone speaker in a meeting. Note `AudioTrack.preferredDevice` *falls back to the default output* if the device disappears, so the gate must be an explicit check (an `AudioDeviceCallback` on Android; a default-output-device listener on macOS), not a preference.
2. **Process separation on macOS (already half there).** The system tap excludes this app's process (`SystemCapture.swift:54`). Play TTS **from our own process** with `AVAudioEngine`. Do not use `AVSpeechSynthesizer.speak`, whose audio I believe is rendered by a system daemon and would be captured on the `system` channel (unverified). If an on-device fallback voice is ever used, use `AVSpeechSynthesizer.write(_:toBufferCallback:)` and play the buffers ourselves.
3. **Tagging (server).** The server knows each utterance's start (and, from `speak_state`, the real played window). Mic segments overlapping `[start, end + 300 ms]` get a `during_talkback` flag.
4. **Text echo filter (server).** For a flagged segment, compare normalized tokens against the spoken text. If overlap ≥ ~0.6, drop it as echo. Otherwise keep it, because it is real speech (the user barging in, or a person in the room). This is cheap, deterministic and testable with fixtures, and it never discards genuine speech just because Jarvis was talking. The 0.6 threshold is a starting guess for Phase 2 tuning.
5. **Jarvis's own words are a first-class transcript entry**, `speaker: "jarvis"`, not a mic segment. They show in the UI and can be excluded from the note.

Where the mic is matters (open question 3):

- **Phone/Mac mic + headphone output (A2DP/BLE):** best audio quality out, no echo path with closed headphones, but the pickup is the device mic. A Pixel in a pocket is a poor mic.
- **Headset mic (Bluetooth SCO/HFP):** the headset goes to call-quality audio for input *and* output, and the TTS is downsampled. Android's `AudioDeviceRouter.startBluetoothSco()` already exists for this and is how a preferred SCO input works today.
- **Mac:** AirPods-class devices switch to the low-quality profile once their mic is used. Default to the built-in mic when output is Bluetooth headphones.

### 3.4 Barge-in under the headphone gate (Phase 4)

If the route is closed, acoustic barge-in is safe without AEC: the server watches the mic channel while Jarvis is speaking and, on sustained speech (it must not be bleed because the route is closed), sends `speak_cancel`. The detector should be the same fast endpointer needed for hands-free, not `VadSegmenter`. Keep the headphone gate as a hard precondition.

## 4. When Jarvis speaks

### 4.1 Policy matrix

| Trigger | Meeting mode | Solo mode |
|---|---|---|
| Explicit ask (PTT) | Answer by voice and show text | Same |
| Tap "speak" on a suggestion card | Speaks | Speaks |
| Unprompted suggestion | **Never** (default, not a setting) | Allowed, throttled |
| Session start/stop/status announcements | Never | Never (earcon at most) |
| Local-only | Never | Never |
| Output route not closed | Never | Never |
| User muted | Never | Never |

`mode` already exists on the session (`session_create_request`: `meeting` or `solo`). Task 5 will make it concrete in `copilot/prompts.py`, and the live loop does not use it today (§1.3). **Dependency:** talkback must read the same single flag task 5 settles on. The policy default for a missing or unrecognised mode is `meeting`, silent.

### 4.2 Solo-mode interjection policy (initial, all tunable)

Each condition is a testable branch of `decide(...)`:

- **User not talking.** Speak only after ≥ 2 s of no mic speech, and never while a user utterance is in progress.
- **Not expired.** Drop if `expires_at_ms` has passed before playback would start.
- **Kind filter.** Start with `question`, `gap`, `counterpoint`. `fact_check` is the riskiest to speak aloud if wrong; open question 7.
- **Rate.** At most 1 interjection per 90 s and 4 per 10 min.
- **Once only.** Track spoken suggestion ids per session; the copilot snapshot has stable ids.
- **Length.** ≤ ~25 words. Over the limit goes through a "spoken form" rewrite or is skipped, not read as a paragraph.
- **Cooldown after the user mutes or interrupts.** Interrupting a suggestion silences unprompted speech for 5 min.

### 4.3 Meeting-silent is structural

Meeting-silence should not depend on a prompt behaving. It is enforced twice: `decide()` returns `Skip("meeting")` for unprompted triggers, and the unprompted path is not even wired unless `mode == solo`. A replay test over `replay.py` fixtures asserts **zero `speak` messages in meeting mode**, whatever the copilot emits.

An open product point: in a meeting with other people, PTT means **they hear you ask**. On a Mac call, your mic feeds the call too. See open question 5; a type-to-ask path with a spoken answer may be the real meeting-mode feature.

## 5. TTS on the Spark

### 5.1 Latency target and budget

Target (proposal): **time from end of the user's utterance (the `ask_end` marker) to the first audio sample played ≤ 1.5 s p50, ≤ 2.5 s p95.** For an unprompted suggestion there is no STT or endpoint step, so the target is **suggestion text available → first audio ≤ 1.0 s p50**.

Estimated budget for an ask (every number is an estimate to be measured in Phase 0):

| Step | Budget |
|---|---|
| `ask_end` → server (network) | 30–80 ms |
| STT of a 2–5 s clip (Whisper large-v3, `spark` or `pve` tier) | 250–500 ms |
| LLM time-to-first-sentence (`fast-chat` via LiteLLM) | 400–600 ms |
| TTS first chunk (Kokoro, streamed PCM) | 100–250 ms |
| Downlink + client jitter buffer (~150 ms of audio) + start playback | 150–250 ms |
| **Total** | **~0.95–1.7 s** |

The target is reachable but not comfortable. It holds only if:

1. The ask path **does not wait for the normal segmenter** (the 3 s silence commit). The cutter transcribes the marked interval at once.
2. The LLM response **streams**, and TTS starts on the **first sentence**, not the full answer.
3. TTS returns **streaming PCM**. The current client code requests WAV and buffers the entire body (`response.body?.bytes()`), which makes first-audio equal to full synthesis time. The new player must read the body incrementally.
4. Answers are constrained to short spoken form (persona prompt, §1.1).

If STT lands on the `pve` RTX 2070 tier instead of the Spark, expect the STT line to be the upper end or worse. That is the first thing Phase 0 should break out.

### 5.2 Which engine

**Kokoro now; F5-TTS later if wanted.** Kokoro is deployed, small, OpenAI-shaped and has a known-good PCM path. F5-TTS offers cloning but needs a persistent-model server and a latency check before it can be a talkback voice.

### 5.3 VRAM next to STT and diarization

**These are estimates, not measurements.** I did not see the Spark's profile definition and did not run anything.

| Component | Rough GPU memory | Basis |
|---|---|---|
| Whisper large-v3 (fp16) | ~3–4 GB weights; **more** under vLLM, which preallocates by `gpu-memory-utilization` | model size; the repo's `spark` tier is `vllm-whisper` |
| Diarization (pyannote-class, task 3) | ~1–2 GB | small models; not yet chosen |
| Kokoro-82M | ~1–2 GB in practice | 82M parameters; runtime overhead dominates |
| F5-TTS (persistent) | ~4–10 GB | the vault note budgets "8–10GB VRAM"; I would plan on the high end |

Reading it:

- **On the Spark** this is not a capacity problem. It has 128 GB of unified memory (public spec; I did not check the node), so Kokoro + STT + diarization is a rounding error. The constraint is **contention** (bandwidth and the profile mechanism that parks roles; the config comment says the `spark` STT tier is down whenever the active profile parks whisper), plus co-resident LLM profiles I have not seen. TTS must become a **role in that profile system** or it will be evicted unpredictably.
- **On the `pve` RTX 2070 (8 GB)**, Whisper + Kokoro + pyannote is roughly 5–8 GB: tight but plausible. F5 does not fit there. So TTS tiers should be `spark` (Kokoro), then the existing Kokoro instance, then none, and F5 is Spark-only.
- Because TTS shares the box with STT and diarization, a **GPU-busy** STT burst can add TTS latency. Phase 0 must measure TTFA while STT and a copilot LLM call run concurrently, not on an idle machine.

### 5.4 Fallbacks

If no TTS tier is healthy: **stay silent and show the text** (the answer or suggestion still appears in the UI). Do not fall back to a phone-speaker voice or to a different voice mid-session. Raise a single status event, as the STT "all tiers down" alert does.

## 6. Platform audio

### 6.1 Android (Pixel Fold)

Where the pieces are, and what to add:

- **Process and service.** The Live foreground service already holds the mic (`foregroundServiceType="microphone"`, manifest :164,169). Play from the same service so the process lives as long as the session. Whether the playback needs an additional `mediaPlayback` foreground-service type is **unverified**; test on-device before adding a permission.
- **Audio focus (missing).** The app never requests focus. A podcast or music app keeps playing over Jarvis. Request `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK` per utterance, abandon after it, and stop on `AUDIOFOCUS_LOSS`. This is new code and needs a unit-testable wrapper.
- **Attributes.** `USAGE_MEDIA` + `CONTENT_TYPE_SPEECH`, as the chat path chose after finding `USAGE_ASSISTANT` unreliable over A2DP on some OEMs (its code comment). Keep that.
- **Route gate.** Use `AudioDeviceCallback` to detect output changes; stop immediately on removal; refuse to speak unless a closed device is the active output (§3.3).
- **Bluetooth.** Prefer A2DP/BLE output with the phone mic. Using the headset mic forces SCO. `AudioDeviceRouter.startBluetoothSco()` uses `AudioManager.startBluetoothSco`, which is deprecated on recent Android in favour of `setCommunicationDevice`; flag as tech debt, not in this scope.
- **Capture.** Keep `AudioCapture` as is. Talkback adds no second recorder.
- **Doze/lock screen.** The foreground service plus its existing partial wake lock handles a locked screen; confirm playback survives lock and a screen-off Bluetooth reconnect in Phase 1 testing.

### 6.2 macOS

- **Playback:** `AVAudioPlayerNode` in an `AVAudioEngine`, from our own process, so the system tap's process exclusion applies.
- **Echo reference:** `MicCapture` enables voice processing on its engine. To benefit from the AEC, TTS playback should be **on the same engine** (or at least go through the same voice-processing unit). The mechanics of this on macOS are **unverified**; Phase 0 tests it on hardware. Be aware that enabling voice processing also changes ducking of other audio; `MicCapture` already sets an ducking configuration, so Jarvis speaking will interact with it.
- **Device selection:** Settings has an input-device choice (`inputDeviceUID`). Add an output-device choice, defaulting to the system default, with the same closed-route gate (Bluetooth or wired headphones; built-in speakers = no talkback).
- **Calls:** with a call app running, Jarvis plays to local output only, so call participants do not hear it, but they do hear your mic, including your PTT question (open question 5).
- **Manual test checklist** goes in `macos/JarvisVoiceMac/TESTING.md`, since hardware audio is not unit-testable.

## 7. Interaction with local-only (task 6)

Task 6 is still open, so this is written against its stated scope: *a hard gate at the transport layer, ambiguous classification defaults to local-only, a no-egress test fails on any unlisted outbound call.*

Rules:

1. **Talkback is hard off in local-only.** A voice question carries content (and the answer needs session context), so asking is itself egress to the server. There is no partial version of this that is safe. Do **not** add an on-device LLM fallback to "make it still work"; that is a separate product decision.
2. **No client-direct TTS call.** Everything flows through the Live socket (rule 1 in §2). The no-egress test then covers talkback for free, plus an explicit case: start a local-only session, press PTT, assert **zero outbound requests** and no `ask_*` frames queued.
3. **Mid-session transitions.** Live already has an automatic local-only switch (the macOS `AutoLocalOnly`/away mode, Android `NetworkPlaceMonitor`). On any transition to local-only, cancel in-flight playback, drop any buffered speech, discard an in-progress ask, and disable the controls.
4. **Ambiguous classification → local-only → talkback off.** Same default as task 6e. A calendar event classified as a work call (task 5) forces both local-only and meeting mode.
5. **Visibility.** The talkback control must show its state: "Local only: Jarvis can't hear or speak" rather than a silently inert button. This sits next to the recording indicator task 6d requires.
6. **TTS audio is not retained.** The text is the record. Server-side nothing new is written for the audio, so retention and soft-delete (task 1) need no new audio class. The `jarvis` transcript entries do follow the session's normal retention.

Spark TTS itself is in-cluster, so server-side TTS adds no new external egress in a streaming session.

## 8. Phased plan

Each phase is shippable on its own and starts with failing tests (TDD.md). Android UI changes need Paparazzi images re-recorded; protocol changes need fixtures plus server and Swift tests in the same PR; manifests ship in `server/deploy/k8s/base/` with the code that needs them.

### Phase 0 — Measure (no feature code)

Throwaway scripts only; results go into this doc.

- Kokoro on each candidate endpoint: TTFA and total time for 10/25/60-word inputs, **streaming PCM**, idle and under concurrent STT + copilot load.
- GPU memory per role on the Spark and on `pve` (note unified memory reports differently on the Spark).
- Pixel Fold with real headphones: does `USAGE_MEDIA` playback leak into a `VOICE_RECOGNITION` + AEC recording? Test speaker and closed headphones, A2DP and SCO. This settles §3.2 item 1 with data.
- Mac: leak test with and without voice processing, playback on the mic engine vs a separate engine.
- **Acceptance:** a measurements table replaces every "estimate" above; a go/no-go on the 1.5 s target; the Kokoro tier endpoint and probe path are decided.

### Phase 1 — Speak a suggestion on tap (smallest useful slice)

- TTS tier pool + `speak` / binary downlink / `speak_end` / `speak_cancel`; `hello` feature flag; Android `PcmPlayer` (streaming, audio focus, route gate, immediate stop); macOS `AVAudioEngine` player; a speaker button on suggestion cards; a mute control.
- No ask, no unprompted speech, no transcript changes yet.
- **Acceptance:**
  - Tapping a suggestion speaks it on Android and Mac headphones, TTFA ≤ 1.0 s p50 on the chosen tier.
  - Disconnecting headphones mid-utterance stops audio within 300 ms and nothing plays from the speaker.
  - Other media ducks during speech and recovers after.
  - TTS down → no audio, text unchanged, one status event.
  - Local-only and meeting sessions: no new network calls at all (no-egress test passes).
  - Protocol fixtures validate in both server and Swift suites; the old-client `hello` path still works.

### Phase 2 — Push-to-talk ask

- `ask_begin` / `ask_end`; server cutter, STT, talkback agent, sentence-chunked TTS; `jarvis` speaker and `kind: ask` segments; echo tagging + text filter; PTT controls (Android media button + tile + on-screen; Mac hotkey).
- **Acceptance:**
  - `ask_end` → first audio ≤ 1.5 s p50, ≤ 2.5 s p95 over a 30-ask run on headphones.
  - A 20-minute headphone soak with 10 asks produces **zero** transcript segments that are Jarvis echo.
  - The question and answer are in the session record as `ask` / `jarvis`, and the final note does not attribute them to meeting participants.
  - Release-before-speech (empty ask) and a 60 s hold both handled without hanging.
  - The echo filter's unit tests include a real user utterance overlapping Jarvis speech that is **kept**.
  - Local-only PTT: zero outbound requests.

### Phase 3 — Solo-mode interjections

- `talkback/policy.py` driven by the task 5 `mode` flag; per-session spoken-id tracking; throttles; settings to enable unprompted speech (off by default).
- **Dependency:** task 5 defines how `mode` reaches the live loop.
- **Acceptance:**
  - Policy unit tests cover every §4.2 condition and the missing-mode → meeting default.
  - A `replay.py` meeting-mode run with the copilot emitting suggestions yields **zero** `speak` messages.
  - A solo replay obeys the rate caps and speaks each suggestion at most once.
  - Interrupting a suggestion silences unprompted speech for the cooldown.

### Phase 4 — Hands-free (optional, each item separately justified)

- Headphone-gated acoustic barge-in with a proper fast endpointer; optional wake word (solo, mic channel only); Wear OS PTT; F5-TTS voice behind a persistent server; coordination with task 8's driving mode (which scopes talkback out but wants to slot it in later).
- **Acceptance:** per item; each must prove no regression to the Phase 2 echo soak and the meeting-silent replay.

## 9. Risks

- **The latency target is tight** (§5.1) and sensitive to which STT tier answers.
- **Embarrassing audio** (speaker fallback, a talkback in a call) is the highest-impact failure. The route gate and structural meeting silence exist for this; both need tests, not just manual checks.
- **Protocol change scope.** The first server → client binary stream and the `jarvis` speaker are real protocol additions with Swift fixtures.
- **Two voices of truth for `mode`.** If task 5 and talkback read different sources, meeting silence can break. One flag, one reader.
- **GPU contention** on the Spark with a profile I have not seen.

## 10. Open questions for you

1. **Where does Kokoro run for this?** Is `192.168.1.43:8880` (vault, May 2026) still the instance, and is it on GPU? Or should TTS become a new role in the Spark profile? I have not read np-home-homelab; can a `tts` role sit beside `whisper` in a profile, or do profiles park it?
2. **Which voice?** Kokoro's `af_sky`, a different stock voice, or the F5 clone of you? I recommend a distinct, non-you voice for an assistant that speaks during meetings. Do you disagree?
3. **Microphone with headphones.** For Live you want phone/Mac mic plus headphone output (best sound, but a pocketed Pixel is a bad mic), or the headset mic over Bluetooth (call-quality audio)? What do you use today?
4. **PTT control.** Which do you want first: headset media button, Quick Settings tile, on-screen hold button, watch, Mac hotkey (which key)?
5. **Asking out loud in a meeting.** On a Mac call the participants hear your PTT question. Do you want a type-to-ask path with a spoken answer in meeting mode, or is audible asking acceptable?
6. **Q&A in the record.** Should asks and answers appear in the final note, only in the transcript, or be dropped from meeting-mode notes?
7. **Unprompted kinds and limits.** Which suggestion kinds are worth speaking in solo mode (`fact_check` aloud seems risky), and are 1 per 90 s and 4 per 10 min about right?
8. **Local-only.** Confirm talkback is hard off with no on-device fallback. Are on-device earcons or status tones acceptable in local-only, or must it be fully silent?
9. **Protocol scope.** Are you comfortable with a downlink binary stream and a `jarvis` speaker value in `protocol/v1`, with hello feature negotiation for old clients, rather than a v2?
10. **Ordering.** Tasks.txt puts this after tasks 5, 6 and 9. Phase 1 is inert in local-only and silent in meeting mode by construction, so it could ship earlier. Do you want that, or hold everything until 5 and 6 land?
11. **Wake word and Wear OS.** Do you actually want either, or should Phase 4 be dropped from the plan?
12. **LiveKit.** I recommend not moving Live onto it. Is the May agent at `kai.apj.dev` still something you use, and should anything here be coordinated with it?
