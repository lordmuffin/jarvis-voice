# Plan: hold to ask while streaming through OBS

Status: **proposed, awaiting approval. Nothing is built.** Written 2026-11-04.
Builds on: `docs/plans/voice-push-to-talk.md` (hold to ask, shipped on the Fold and the Mac) and
`docs/obs.md` (OBS and phones streaming into a live session through MediaMTX, lab only).

## Goal

While OBS streams or records into a live session, hold a key, ask, release, and hear a short
answer in your headphones, as hold to ask does on the Fold and the Mac today. Jarvis's voice
never reaches OBS, the viewers, the recording or the transcript.

Done when Andrew can do this on lab with the Fold, then the Mac, as the companion, with the
alignment error and end of ask to first audio measured.

## What you need to decide

The plan assumes the recommended answer to each. Say so if you want otherwise.

| # | Decision | Recommended | Why it is yours |
|---|---|---|---|
| 1 | **Which option?** | A companion that sends only the hold markers and plays the reply (option A below). The Fold first, then the Mac | See [Options](#options) |
| 2 | **Do viewers hear your question?** | Yes. With option A the question has to be in the stream's mic, so viewers and call guests hear it. Only Jarvis's answer is private | Keeping the question off the stream means muting OBS's mic during the hold, which only works with option B. It changes the recommendation |
| 3 | **Which machine runs OBS?** | Assumed: a machine that is **not** the companion. The Mac refuses to speak while OBS runs on it (text only) | On the OBS machine itself, OBS's desktop audio capture can record Jarvis, and nothing in our code can rule that out. See [Keeping Jarvis out of the stream](#keeping-jarvis-out-of-the-stream) |
| 4 | **Which streams can a companion join?** | Stream sessions only (`source` `obs` or `mobile`), the owner's only | A companion on any live session (say the Fold asking into a Mac recording) is a small step from here, but it is a new way to talk into someone's session and should be its own decision |

## What I found

Read from the code on `main` (`fc4b075`) on 2026-11-04.

| Fact | Evidence |
|---|---|
| **A stream's producer is the server itself.** `StreamIngest` runs a `Capturer` per track (ffmpeg reading MediaMTX) and a `ProducerConnection` to its own WebSocket, with a producer ticket it issues itself | `streams/ingest.py`, `streams/wiring.py` |
| **Asks are accepted only on the producer socket.** A viewer that sends anything is closed (`read_only`). A second producer supersedes the first (4409). So a companion today would either be refused or **knock the stream's own producer off** | `api/stream.py:118-140`, `_viewer`, `_ask` |
| Ticket roles are `producer` and `viewer`. A producer ticket needs the session's owner and a live session | `auth.py:19`, `api/sessions.py:566` |
| **The stream's `t_ms` is the server's clock, not OBS's.** The `Capturer` counts samples from ffmpeg's stdout. After a read fails (OBS dropped), the restart's `t_ms` jumps forward to the wall-clock time since the stream's start, so the timeline skips the gap. It never goes backwards | `client/capture.py`, `Capturer.run` |
| Mic and system tracks have separate `Capturer`s with their own start. Asks use the mic only | `streams/ingest.py`, `api/stream.py:220` |
| `AskAudio` maps an ask by the mic frames' `t_ms` and works in stream positions after that. It assumes `ask_begin.t_ms` is on the frames' clock | `voice/ask.py` |
| `AskDesk` waits only `voice_ask_tail_ms` (400 ms) for the last frame after release | `voice/desk.py:_answer`, `config.py:240` |
| A desk opens only when `voice_enabled` and `_ideas_eligible`: not local-only, not restricted, the owner's or an admin's | `orchestrator.py:203`, `_ideas_eligible` |
| `EchoGuard` places the played window at `pipeline.position_ms()` when `ask_played` arrives, and the pipeline applies it to **both channels** | `voice/desk.py:played`, `ingest/pipeline.py:211` |
| `ask_reply` and `voice_state` reach every subscriber that asked for `voice_ask`, viewers included. The web dashboard asks only for `speaker_update` | `bus.py`, `web/src/ws.ts:22` |
| Both apps' `AskController` already does markers out, route gate, speech and `ask_played`. Each is wired to a recording run (`LiveSessionService.openAsk`, Mac `AppModel`), and its connection is a producer | `android/.../live/ask/`, `macos/JarvisLiveKit/.../Ask/AskController.swift` |
| Android speaks with `USAGE_MEDIA`. It does **not** set a capture policy, so another app's playback capture (a phone streaming app recording internal audio) may hear it | `TtsAskSpeaker.kt:24` |
| The Mac renders speech in its own process, so its **own** system tap skips it. That says nothing about OBS's capture on the same Mac | `SpeechOutput.swift`, header comment |
| `docs/obs.md` reports 0.5 s added lag over SRT and 1 to 2 s over RTMP. **Not measured for asks**, and it does not include OBS's own buffering | `docs/obs.md` |
| MediaMTX's path list says which protocol is publishing (`source.type`). **I expect `rtmpConn` / `srtConn`; unverified** against lab's MediaMTX version | `streams/mediamtx.py` reads the same list |

## Options

| | A. Companion, markers only (recommended) | B. Companion with its own mic | C. OBS hotkey through obs-websocket |
|---|---|---|---|
| What it is | The Fold or the Mac joins the stream's session. The hold sends `ask_begin` and `ask_end`. The ask's audio is cut from the stream's mic. The reply plays on the companion | As A, but the companion also sends its own mic audio for the length of the hold, and the ask is transcribed from that | A key bound in OBS. An OBS script forwards press and release; something sends markers to the server |
| Clock | **The hard part.** Markers arrive on one path, the audio 0.5 to 2 s later on another. The server maps one to the other: [The clock](#the-clock) | **None needed for the clip:** it is the companion's own audio. Ask windows still need an approximate position, to keep a held "Jarvis, ..." from being filed twice | No better. An OBS script knows OBS's output time, but not when ffmpeg on the server emits that audio |
| Latency | Waits for the stream's delay after release: + 0.5 to 2 s | No wait: the companion's audio is already in | As A |
| Jarvis out of the stream | The reply exists only on a device that is not OBS's | Same as A | **The reply has to play somewhere.** On the OBS machine, OBS's desktop audio capture takes the default output, which is the headphones. OBS's "monitor only" is captured too when the monitoring device is the captured one. Nothing we control prevents it |
| Your question on stream | Yes, always | Can be kept off, if you mute OBS's mic while you hold (by hand, or a matching OBS push-to-mute key) | Yes |
| Protocol | A `companion` ticket role, and a stream list | Both of A's, plus audio frames from a non-producer, kept in memory only: a new channel or message | A credential for OBS (an OBS script has to reach the server), which breaks the "nothing but the ingest key in OBS" rule. obs-websocket has no hotkey-pressed event, so a script is needed anyway |
| Client work | A companion mode around the existing `AskController` | A's, plus capture, framing and a second mic open while streaming | An OBS script in Python or Lua, and still a player |
| Verdict | **Recommended** | Fallback, with a kill criterion below | **Rejected:** fails the headphones rule and needs a credential in OBS |

**Why A:** it reuses every piece that ships today, hold to ask's prompt, gates, eval and stored
row, and both apps' `AskController`, with one new ticket role and one new list. The ask is heard
by the stream's own mic, the best mic in the room. Its cost is the clock, which is a server-side
problem with a testable answer.

**Kill criterion for A, decided before anything is built:** in the 30-ask run on the Fold, if
more than 2 asks lose words at either end, or pick up a sentence that was not the question, A's
alignment is not good enough. Then B replaces the clip source and keeps everything else.

## The clock

The hold's markers and the stream's audio come by different roads:

```
you speak at R ──> OBS ──> MediaMTX ──> ffmpeg ──> Capturer emits the frame at R + D
you press at R ──> companion ──> server receives ask_begin at R + c
```

`D` is the stream's delay: OBS's buffer and encoder, the network, MediaMTX and ffmpeg. `c` is the
companion's network delay, tens of milliseconds on the LAN or the VPN. The server has no way to
read OBS's clock, but it does not need one: **the stream's `t_ms` is the server's own clock**,
because the `Capturer` runs inside the server process.

### How the server maps one to the other

A new pure `StreamClock` (`streams/clock.py`), one per stream, fed from the `Capturer`'s
`on_frame` with `(time.monotonic(), frame.t_ms, duration)`.

1. **Anchor.** `ask_begin` arrives at server time `A`. The frame the press lines up with will be
   emitted at about `A - c + D`. Its `t_ms` is `clock.t_at(A) + lead`, where `t_at` extrapolates
   from the newest frame (frames come at real-time pace) and `lead` estimates `D - c`.
2. **Length.** The companion's own `t_ms` is used only for the hold's length:
   `end = begin + (ask_end.t_ms - ask_begin.t_ms)`. That is exact on its clock and immune to the
   network jitter of the second message.
3. **Hand-off.** The mapped `t_ms` go into `AskDesk.begin` and `end` unchanged. `AskAudio`,
   positions, ask windows and the transcript filter work as today.
4. **Margins.** A stream's pre-roll is `voice_ask_preroll_ms` plus `streams_ask_margin_ms`
   (400 ms to start, from the spread measured in task 1.4), and the same margin after the
   release. The wait for the last frame is `lead` plus `voice_ask_tail_ms`.
5. **`lead`** is a setting per protocol, `streams_ask_lead_ms_srt` and `_rtmp`, chosen from MediaMTX's
   `source.type` when the stream starts. Its defaults come from the lab measurement in task 1.4,
   not from a guess.
6. **Echo.** The played window from the companion's `ask_played` is moved by the same `lead`: a
   leak would reach the stream `D` after it was heard.

**After a drop.** OBS drops, the read fails, ffmpeg restarts, and `t_ms` jumps forward. `D` can
differ after a reconnect (a new SRT handshake, a different RTMP buffer). So:

- `StreamClock` sees the jump (a frame not starting where the last one ended) and **starts over**:
  no extrapolation across a gap.
- While no mic frame has come for 1 s, the desk is **unavailable**: `voice_state {available: false}`
  to companions, and a held ask is dropped. When frames return, `available: true` again.
- `lead` is per protocol, not learned per stream, so it needs no re-learning after a reconnect.
  Learning it per stream from speech onsets was considered and left out: hesitation after the
  press varies more than `D` does, so a learned lead would slide late and cut first words.

Every companion ask row stores `align: {lead_ms, margin_ms, onset_ms}`, where `onset_ms` is where
VAD first finds speech, relative to the mapped begin. That is the measured alignment error for
real OBS, which the run reports.

### How a test proves it

| Level | Test | Proves |
|---|---|---|
| Pure | `test_stream_clock.py`: fake clock, frames emitted at `t0 + t_ms + D` | `t_at` and `lead` give the frame the press lines up with, within one frame (100 ms). A `t_ms` jump starts over. No frames for 1 s means unavailable |
| Server, real socket and real Postgres | `test_stream_ask.py`: a `StreamIngest` whose reader is a small script that writes a synthetic clip at real-time pace, **started `D` late**. The clip has tone bursts at known offsets (`tests/audio.tone`). A test companion sends `ask_begin` and `ask_end` at the bursts' "spoken" times. A fake STT records the clip it is given | The clip holds the whole burst and at most the margin either side, for `D` = 0.7 s and 1.5 s, and with `lead` off by 300 ms. Then the reader exits, comes back 2 s later with a different `D`: the next ask lines up too |
| Server | Same harness, drop during a hold | `voice_state false`, the ask is dropped, no `ask_reply` |
| Lab, by hand (task 1.4) | `ffmpeg` on this machine pushes a click track to lab's MediaMTX over SRT and over RTMP. A probe script sends markers as a companion at each click's local time | `D - c` per protocol, 20 clicks each, gives the defaults and the spread. Through OBS, the 30-ask run's `onset_ms` covers OBS's buffering |

## Keeping Jarvis out of the stream

The order matters: everything in the stream has already left OBS before the server sees it. So
**prevention on the companion is the guarantee**. The server's checks can only keep a leak out of
the transcript, and stop it happening twice.

| Layer | Option A (and B) | Option C |
|---|---|---|
| Where the reply exists | Only on the companion, a different device from OBS's | On the OBS machine |
| What stops OBS capturing it | It is not on OBS's machine. **Fold:** `setAllowedCapturePolicy(ALLOW_CAPTURE_BY_NONE)` on the speech's audio attributes, so no app on the phone can capture it (a phone streaming app included). **Mac:** if OBS is running on this Mac, the reply is text only, `ask_played skipped: route` | Nothing we control |
| What stops the mic hearing it | The route gate as today: closed headphones only, fails closed | Same gate, if it played there |
| Viewers and the recording | Follow from the two above | Not guaranteed |
| The transcript | Jarvis's words are never a segment, snapshot or note (as today). The echo guard drops a leaked copy, moved by `lead` | Same |
| Its text | `ask_reply` goes only to `voice_ask` subscribers. The dashboard never asks for it, so a dashboard in an OBS browser source shows no reply. A test pins that | Same |

**If the guarantee fails** (a leak reaches the stream, for example Bluetooth earbuds turned up
next to the streaming mic, or the Mac's OBS check missed):

1. The echo guard drops the matching segment, on either channel, as today.
2. A drop on a stream session is a **leak**: the ask's row gets `leaked: true`, `voice_ask_leak_total`
   counts it, and the server logs it.
3. **Asks turn off for the rest of that session:** `voice_state {available: false}`, and the
   companion says "Jarvis was heard on the stream. Asks are off until the stream restarts."
4. It cannot unsend: viewers heard it, OBS recorded it, and the server's stored audio holds it.
   Silencing that span in the stored audio is possible, and an [open question](#open-questions).

**If the echo guard fails as well** (Whisper's words differ too much from the reply's), the leak
lands in the transcript as ordinary speech. That is the residual risk. The test set includes
leaked replies transcribed with typical errors, to pin the 0.6 overlap threshold.

## Joining

**Nothing new goes into OBS.** OBS holds only the ingest key, which can publish one stream and
cannot list, read or get a ticket. The companion uses the device token it already has, on its own
device.

1. **Find.** `GET /v1/streams` (new, device token or dashboard login): the caller's streams that
   are live now, from `StreamIngest.active`: `{items: [{name, session_id, title, mode, restricted,
   online, started_at}]}`. Never a key. Empty when streams are off, as on live.
2. **Pin.** The companion pins a stream **by name** (`obs-desk`), not by session. With exactly one
   stream live and nothing pinned, it offers that one. With several, you pick.
3. **Ticket.** `POST /v1/sessions/{id}/ticket {role: "companion"}`: the owner only, the session
   live, `source` `obs` or `mobile` (decision 4), not local-only. Otherwise 403, 404 or 409.
4. **Connect.** `/v1/sessions/{id}/stream?ticket=…&features=voice_ask`. **No hello, no audio.** The
   server answers with `voice_state` at once, `false` if the session takes no asks. The companion
   may send `ask_begin`, `ask_end` and `ask_played`. Anything else, binary frames included, closes
   it (`companion_only`). It never supersedes the stream's producer. Several companions may join.
   One ask runs at a time, as today, and a new hold replaces the last.

| When | The companion |
|---|---|
| OBS drops, back within the grace period (180 s) | Stays connected. Asks are off while frames are missing, back on when they return |
| The stream ends (grace runs out) | The server sends `voice_state false` and closes the socket when the session leaves `live`. The companion goes back to waiting for its pinned name, polling `GET /v1/streams` every 5 s |
| The pinned stream starts again | A new session with the same name: the companion joins it on its own |
| Another stream starts | Shown in the list. Never joined in place of the pinned one |
| The ticket expires, the network drops | New ticket with the device token, reconnect. Asks are never resent, as today |

## Meeting, local-only and restricted

Hold to ask's rules, unchanged:

- **Meeting** (`--meeting` on the key): asks work. Meeting-silent means Jarvis never speaks
  unprompted, and only a held key produces speech. Call guests hear your question through your mic.
- **Local-only:** a stream is never local-only. The `companion` ticket is refused for a local-only
  session anyway, and a companion device that is in a local-only session of its own offers no
  companion mode (its `EgressGate` is closed).
- **Restricted** (`--restricted`): no desk, as today. The companion gets `voice_state false` and says
  "No asks in restricted sessions". A test pins that `ask_begin` there does nothing.
- **Voice off** (live): no desk. Streams are lab only anyway.

## Clients

### Android (the Fold), first

- **Companion mode**, a screen reached from the live screen when not recording: the stream list,
  the pinned stream, and the hold control `RecordScreen` already uses, with the same `InstButton`,
  `InstText` and `InstChip`. No new component, so nothing goes to lordmuffin/instrument.
- `CompanionConnection`: ticket, socket, no outbox, no hello. Feeds `AskController` as
  `LiveConnection` does. `AskController` gets its clock from `SystemClock.elapsedRealtime`, since
  there are no frames.
- `TtsAskSpeaker`: `ALLOW_CAPTURE_BY_NONE` (API 29+, the Fold has it), for recording mode too.
- A foreground service keeps the socket while the screen is off. No microphone permission is used.
- Paparazzi images for the new screen.

### macOS, second

- **Companion mode** from the menu bar ("Ask a stream"), exclusive with recording. ⌃⌥A drives the
  companion's `AskController` while it is on.
- `CompanionLink` in `JarvisLiveKit`, tested with `TestWebSocketServer`, and the stream list model.
- **OBS on this Mac means text only.** `NSRunningApplication` for OBS's bundle id, checked at each
  reply. The check is part of `routeClosed`, so it fails closed.
- State in the Copilot panel with `InstChip` and `InstLivePill`. New rows in `TESTING.md`.

## Budget

Option A, SRT, assuming hold to ask's numbers. Nothing here is measured on a stream yet.

| Stage | p95 | Basis |
|---|---:|---|
| Stream delay after release (`lead`) plus margin and tail | 0.5 to 2.4 s | `docs/obs.md`'s 0.5 s (SRT) to 2 s (RTMP), plus 400 ms. **Estimate** |
| Whisper, STT, LLM, delivery, speech start | as hold to ask | `voice-push-to-talk.md`, Budget |

So a stream ask is slower than a Mac or Fold ask by the stream's delay. Over RTMP that is up to
2 s more. That is the main argument for SRT here, and the main thing B would win.

## Delivery

Each PR is based on `main`, follows `TDD.md`, passes `make test`, and leaves `main` working on its
own. Lab gets each on merge. **No manifest change:** streams and voice are already on in lab and off
in live, and the new settings have defaults. Live gets the server code with each release, inert.

| Phase | PR | Change | Proved by |
|---|---|---|---|
| 1 | `feat(server): stream clock for held asks` | 1.1 `StreamClock`, pure. 1.2 fed from `StreamIngest`, reset on a jump, protocol from `source.type`. 1.3 desk availability follows frames (`voice_state` on drop and return). 1.4 lab probe, `D - c` per protocol, defaults set from it | `test_stream_clock.py`, `streams/test_ingest.py`, probe numbers in this plan. Inert: no companion can connect yet |
| 2 | `feat(server): companion role for stream asks` | 2.1 `companion` in `ticket_request`, schema, fixtures, server, **Swift and Android parsing, in this PR**. 2.2 `GET /v1/streams`, `stream_list` schema and fixture, same three. 2.3 the companion socket. 2.4 markers through `StreamClock`, margins, wait. 2.5 echo moved by `lead`, leak turns asks off, `align` and `leaked` stored. 2.6 protocol README | `test_stream_ask.py` (the table above), fixture suites on all three, the rules below. Shippable: the server takes companions, none exist yet |
| 3 | `feat(android): ask a stream from the Fold` | Companion mode, capture policy, Paparazzi | Unit tests, Paparazzi, 30 asks on lab over SRT, 10 over RTMP |
| 4 | `feat(macos): ask a stream from the Mac` | Companion mode, OBS check, panel state, `TESTING.md` rows | `swift test` in CI, your hardware rows, 30 asks |
| 5 | With PR 4 | Results below, a "Hold to ask while streaming" section in `docs/obs.md` | The tables |

**Required tests, from the brief:**

| Requirement | Test |
|---|---|
| The clock | The `test_stream_ask.py` cases above, drop included |
| Never in the transcript | After a stream ask, no segment, snapshot or note holds the reply. A leaked copy on either channel is dropped |
| Leak turns asks off | A dropped echo on a stream session sends `voice_state false`, and a later `ask_begin` does nothing |
| The producer survives a companion | A companion connecting, sending, and sending something it may not, never closes the stream's producer |
| Meeting | A meeting stream answers a held ask, and yields no `ask_reply` without one |
| Restricted, local-only | `voice_state false` (restricted), ticket refused (local-only), `ask_begin` does nothing |
| Not the owner | Another user's device gets 404 for the ticket and an empty stream list |
| Dashboard | The web stream URL never asks for `voice_ask` |

**I cannot do these myself:** the OBS runs, the 30-ask runs, and the Mac build (no Xcode here, no
device on adb). I can run the lab probe in task 1.4 from this machine, with a lab ingest key I
create and revoke, which never goes into the repo.

## Risks

- **`D` may wander within a stream**, as OBS's buffer grows under load. The margin absorbs some of
  it. `align.onset_ms` shows it, and the kill criterion decides.
- **A margin that catches the sentence before the question.** Whisper then hears both, and the
  answer can be about the wrong thing. Same kill criterion.
- **Bluetooth earbuds turned up next to a sensitive streaming mic** is the likeliest real leak. The
  leak rule turns asks off after the first one, and that first one is out.
- **The Mac's OBS check knows only OBS.** Another streaming app on the same Mac is not detected.
- **MediaMTX `source.type`** may be named differently. If it cannot be read, the RTMP lead (the
  larger) is used.

## Out of scope

Option C, the dashboard showing asks, a companion for non-stream sessions (decision 4), the Linux
desktop app as a companion, a headset button, the watch, and any change to hold to ask's prompt,
gates or approval rule.

## Results

Filled in by phases 1 and 5.

| Path | Protocol | n | `D - c` p50 / p95 | `onset_ms` p50 / p95 | Asks clipped or overrun | End of ask to first audio p50 / p95 |
|---|---|---:|---:|---:|---:|---:|
| Probe | SRT | | | | | |
| Probe | RTMP | | | | | |
| Fold | SRT | | | | | |
| Fold | RTMP | | | | | |
| Mac | SRT | | | | | |

## Open questions

1. **Should viewers hear your question?** If not, option B with OBS's mic muted during the hold is the
   only way, and the plan changes (decision 2).
2. **Which machine runs OBS, and which headphones do you wear while streaming?** If it is the Linux
   desktop with wired headphones, the Fold's reply goes to a second pair of earbuds. Is one earbud
   from the Fold workable, or should the companion be the Linux desktop app, which has no hold to
   ask today?
3. **The Fold's key.** The on-screen hold button needs the phone in reach and the screen on. Volume
   down held while the companion screen is open gives a real press and release. Want it?
4. **SRT or RTMP?** SRT costs about 1 s less per ask, but puts the key in OBS's server URL
   (`docs/obs.md`). Is SRT through the recording output (section 3) what you use?
5. **After a leak, silence it in the stored audio?** The server could zero the played window in its
   own copy. OBS's recording and the viewers are out of reach either way.
6. **One companion or several?** The plan lets the Fold and the Mac both join, last hold wins.
7. **Decision 4:** stream sessions only, or companions on any live session you own?
