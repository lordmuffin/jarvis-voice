# Jarvis Voice for Mac: manual test checklist

Audio hardware, TCC prompts and live calls cannot run on CI. This is the pass/fail record for the things that can't.

Status key: **PASS** / **FAIL** (fill in) · **NOT RUN** (needs hands-on; steps below) · **N/A**.

- Build under test: 0.1.0 (Debug), built with Xcode 27.1 RC (27A9275), macOS 26.5.2 SDK 27.0
- Date filled in: 2026-10-09 (automated items only; hardware items still open)

## Setup

1. `brew install xcodegen`, then `cp Local.xcconfig.template Local.xcconfig` and set `DEVELOPMENT_TEAM` to your personal team ID.
2. `make install`. This copies the app into `~/Applications`. Launch it from there every time, so the signing identity (and therefore every TCC grant) stays stable.
3. Settings: server URL `https://live.lab.apj.dev`, paste the device token, choose an input.
4. To reset a permission for a clean first-run test: `tccutil reset Microphone dev.apj.JarvisVoiceMac` and `tccutil reset AudioCapture dev.apj.JarvisVoiceMac` (the exact service name for audio capture may differ by OS build; use System Settings > Privacy & Security if it doesn't take).

## A. Automated (no hardware)

| # | Check | Result | Notes |
|---|---|---|---|
| A1 | `make generate` | **PASS** | XcodeGen 2.46.0 |
| A2 | `make build` (unsigned, no `Local.xcconfig`) | **PASS** | Build succeeded, no errors or warnings from our targets |
| A3 | `make test` | **PASS** | 4/4 `LocalMarkdownTests` |
| A4 | `swift test` in `JarvisLiveKit` | **PASS** | 48 tests in 8 suites; package API unchanged |
| A5 | App launches and stays up (smoke) | **PASS** | Alive after 5 s, no crash output; menu-bar only (`LSUIElement`) |
| A6 | Info.plist keys present | **PASS** | `LSUIElement`, `NSMicrophoneUsageDescription`, `NSAudioCaptureUsageDescription` |
| A7 | Signed build with stable identity + entitlements | **NOT RUN** | No Apple team/signing identity on this Mac yet; unsigned builds embed no entitlements, so mic access under hardened runtime is untested |

## B. Hands-on (MacBook Pro)

Record the result and a line of evidence for each.

| # | Check | Steps | Pass when | Result |
|---|---|---|---|---|
| B1 | First-run permission prompts | Fresh grants. Start a **Meeting** session. | Mic prompt appears, then the audio-capture prompt. Both granted: recording starts, red glyph shows. Deny audio capture: the menu shows the "No system audio" warning and the mic channel keeps working. | NOT RUN |
| B2 | Speech model install | First start after install, or after clearing the asset. | Menu shows "Installing speech model N%", then drafts appear. Second run does not download again. | NOT RUN |
| B3 | Solo session end to end | Solo mode, speak ~1 min, markers with ⌃⌥M, stop with ⌃⌥J. | Grey italic drafts become normal text as server segments arrive. Final note shows in the panel and appears in the next vault-writer PR (or the dry-run log). | NOT RUN |
| B4 | Call on headphones (Zoom / Meet / Teams) | Meeting mode, 5 min call with someone talking. | Both channels transcribed: you as Me, remote as Them. No cross-bleed. | NOT RUN |
| B5 | Call on laptop speakers (echo) | Same call, speakers at normal volume. | No duplicated "Them" text in the **final** transcript; voice processing active (no echo warning in menu). | NOT RUN |
| B6 | Wi-Fi off for 60 s mid-session | Turn Wi-Fi off, keep talking 60 s, turn it on. | Status dot goes amber ("Reconnecting"), then green. Final transcript has no gap or duplicate over that minute. | NOT RUN |
| B7 | Local-only session | Toggle **Local only**, record 2 min, stop. Tail the server logs during the session. | No request from this Mac reaches the server. A Markdown file appears in `~/Documents/Jarvis Live/`. Status shows "Offline (local only)". | NOT RUN |
| B8 | Upload for full processing | From B7, menu > "Upload for full processing". | Server creates a session and processes the audio; the Markdown front matter changes to `uploaded: <session id>`; entry leaves the menu. | NOT RUN |
| B9 | 60-minute memory | Meeting mode, 60 min. Note the app's memory in Activity Monitor at start and at the end. | Memory roughly flat (record both numbers: start ___ MB, end ___ MB). Local-only sessions grow disk, not RAM. | NOT RUN |
| B10 | Anker speakerphone as input | Select it in Settings, Solo 2 min. | Clean transcription; if voice processing is refused the menu says so and capture still works. | NOT RUN |
| B11 | DJI Mic Mini as input | Same. | Same. | NOT RUN |
| B12 | Panel behaviour | Start a session, click into another app. | Panel stays visible and does not steal focus; "On top" toggle works; resizable. Suggestions are dismissable and fade near expiry; Related opens in Obsidian. | NOT RUN |
| B13 | Keep awake | Record, leave the Mac idle past the display-sleep timer. | Session keeps recording. After stop, `pmset -g assertions` no longer lists "Jarvis Voice is recording". | NOT RUN |
| B14 | Hotkeys | From another app press ⌃⌥J, ⌃⌥M. | Start/stop toggles; marker appears in the panel and the final note. | NOT RUN |

## Known limits to check while testing

- **Audio-capture permission has no public pre-flight.** A denied tap delivers silence, so the app can only warn after ~8 s of pure silence. A genuinely silent meeting start will show the same warning until sound arrives, then clears.
- **Local-only outbox is not compacted.** Audio stays on disk until uploaded (≈ 2 × 115 MB/hour in meeting mode). Delete `~/Library/Application Support/JarvisVoice/sessions/<id>` after a successful upload if space matters.
- Draft timestamps are anchored to the first buffer of each channel; expect ±100–300 ms of alignment between channels.
