# Jarvis Voice for Mac: manual test checklist

Audio hardware, TCC prompts and live calls cannot run on CI. This is the pass/fail record for the things that can't.

Status key: **PASS** / **FAIL** (fill in) · **NOT RUN** (needs hands-on; steps below) · **N/A**.

- Build under test: 0.1.0 (Debug), built with Xcode 27.1 RC (27A9275), macOS 26.5.2 SDK 27.0
- Date filled in: 2026-10-09 (automated items only; hardware items still open)

## Setup

1. `brew install xcodegen`, then `cp Local.xcconfig.template Local.xcconfig` and set `DEVELOPMENT_TEAM` to your personal team ID.
2. `make install`. This copies the app into `~/Applications`. Launch it from there every time, so the signing identity (and therefore every TCC grant) stays stable.
3. Settings: server URL `https://live.apj.dev`, paste a device token (create one on the web dashboard's **Devices** page), choose an input.
4. To reset a permission for a clean first-run test: `tccutil reset Microphone dev.apj.JarvisVoiceMac` and `tccutil reset AudioCapture dev.apj.JarvisVoiceMac` (the exact service name for audio capture may differ by OS build; use System Settings > Privacy & Security if it doesn't take).

## A. Automated (no hardware)

| # | Check | Result | Notes |
|---|---|---|---|
| A1 | `make generate` | **PASS** | XcodeGen 2.46.0 |
| A2 | `make build` (unsigned, no `Local.xcconfig`) | **PASS** | Build succeeded, no errors or warnings from our targets |
| A3 | `make test` | **PASS** | 4/4 `LocalMarkdownTests`; not yet re-run with the new `DictationTriggerTests` and `DictationTextTests` |
| A4 | `swift test` in `JarvisLiveKit` | **PASS** | 48 tests in 8 suites; package API unchanged |
| A5 | App launches and stays up (smoke) | **PASS** | Alive after 5 s, no crash output; menu-bar only (`LSUIElement`) |
| A6 | Info.plist keys present | **PASS** | `LSUIElement`, `NSMicrophoneUsageDescription`, `NSAudioCaptureUsageDescription` |
| A7 | Signed build with stable identity + entitlements | **NOT RUN** | No Apple team/signing identity on this Mac yet; unsigned builds embed no entitlements, so mic access under hardened runtime is untested |

## B. Hands-on (MacBook Pro)

Record the result and a line of evidence for each.

| # | Check | Steps | Pass when | Result |
|---|---|---|---|---|
| B1 | First-run permission prompts | Fresh grants. Start a **Meeting** session. | Mic prompt appears, then the audio-capture prompt. Both granted: recording starts, the menu-bar item shows the recording capsule with the elapsed time. Deny audio capture: the menu shows the "No system audio" warning and the mic channel keeps working. | NOT RUN |
| B2 | Speech model install | First start after install, or after clearing the asset. | Menu shows "Installing speech model N%", then drafts appear. Second run does not download again. | NOT RUN |
| B3 | Solo session end to end | Solo mode, speak ~1 min, markers with ⌃⌥M, stop with ⌃⌥J. | Grey italic drafts become normal text as server segments arrive. Final note shows in the panel and appears in the next vault-writer PR (or the dry-run log). | NOT RUN |
| B4 | Call on headphones (Zoom / Meet / Teams) | Meeting mode, 5 min call with someone talking. | Both channels transcribed: you as Me, remote as Them. No cross-bleed. | NOT RUN |
| B5 | Call on laptop speakers (echo) | Same call, speakers at normal volume. | No duplicated "Them" text in the **final** transcript; voice processing active (no echo warning in menu). | NOT RUN |
| B6 | Wi-Fi off for 60 s mid-session | Turn Wi-Fi off, keep talking 60 s, turn it on. | Status dot goes amber ("Reconnecting"), then green. Final transcript has no gap or duplicate over that minute. | NOT RUN |
| B7 | Local-only session | Toggle **Local only**, record 2 min, stop. Tail the server logs during the session. | No request from this Mac reaches the server. A Markdown file appears in `~/Documents/Jarvis Live/`. Status shows "Offline (local only)". | NOT RUN |
| B8 | Upload for Full Processing | From B7, menu > "Upload for Full Processing". | Server creates a session and processes the audio; the Markdown front matter changes to `uploaded: <session id>`; entry leaves the menu. | NOT RUN |
| B9 | 60-minute memory | Meeting mode, 60 min. Note the app's memory in Activity Monitor at start and at the end. | Memory roughly flat (record both numbers: start ___ MB, end ___ MB). Local-only sessions grow disk, not RAM. | NOT RUN |
| B10 | Anker speakerphone as input | Select it in Settings, Solo 2 min. | Clean transcription; if voice processing is refused the menu says so and capture still works. | NOT RUN |
| B11 | DJI Mic Mini as input | Same. | Same. | NOT RUN |
| B12 | Panel behaviour | Start a session, click into another app. | Panel stays visible and does not steal focus; "On top" toggle works; resizable. Suggestions are dismissable and fade near expiry; Related opens in Obsidian. | NOT RUN |
| B13 | Keep awake | Record, leave the Mac idle past the display-sleep timer. | Session keeps recording. After stop, `pmset -g assertions` no longer lists "Jarvis Voice is recording". | NOT RUN |
| B14 | Hotkeys | From another app press ⌃⌥J, ⌃⌥M. | Start/stop toggles; marker appears in the panel and the final note. | NOT RUN |
| B15 | Dictation: Accessibility | Fresh grant (`tccutil reset Accessibility dev.apj.JarvisVoiceMac`), launch. | The Accessibility prompt appears once. Before granting, the menu shows "Dictation needs Accessibility" and Settings offers the button. After granting (no relaunch), the menu shows "Dictation: hold Right ⌘". | NOT RUN |
| B16 | Dictation: hold to talk | Focus TextEdit, then Safari's address bar, a Chrome text field, then a Slack message. Hold Right ⌘, say a sentence, release. | The pill shows "Listening…" and live words, never takes focus. On release the text is pasted at the cursor in every one of them (not just copied) and is on the clipboard (⌘V again pastes it). The first word is not cut off. | NOT RUN |
| B17 | Dictation: tap mode | Settings › Dictation › Mode: Tap. Tap Right ⌘, speak 20 s, tap again. Then tap, speak, press Esc. | First run pastes; Esc discards with nothing pasted. | NOT RUN |
| B17a | Dictation: double-tap mode | Settings › Dictation › Mode: Double-tap. Tap Right ⌘ once; then double-tap it, speak, tap once. Double-tap, speak, double-tap. Double-tap, speak, press Esc. Then tap Right ⌘, type a letter, tap Right ⌘. | A single tap never starts listening, nor does tap–letter–tap. The single tap and the double tap each stop and paste, and the double tap doesn't start a new dictation. Esc discards with nothing pasted. The menu shows "Dictation: double-tap Right ⌘". | NOT RUN |
| B18 | Dictation leaves shortcuts alone | Hold mode. Press Right-⌘C, Right-⌘V, Right-⌘-click, Right-⌘-Tab. Hold Right ⌘ 1 s, then press C. | Every shortcut behaves as before. The orange mic indicator doesn't flash for quick shortcuts; the long hold + C cancels with nothing pasted. | NOT RUN |
| B19 | Dictation during a session | Start a Meeting session (⌃⌥J), dictate into another app twice, stop the session. Repeat with Wi-Fi off and **Local only**. | Session keeps recording throughout (no echo-cancellation warning appears, the system channel stays live); its final note / local file is intact. Dictation still works offline, and no dictation request reaches the server. | NOT RUN |
| B20 | Settings opens | Menu › Settings… (and ⌘, with the menu open), while another app is frontmost. | The Settings window opens in front, every time. | NOT RUN |
| B21 | Dictation: first mic prompt | `tccutil reset Microphone dev.apj.JarvisVoiceMac`, then press the dictation key once. Allow. Press again and dictate. Quit, relaunch, dictate. | The first press only shows the prompt (pill: "Microphone allowed. Press Right ⌘ to dictate."); it never sits on "Transcribing…". After that, no prompt again, also after relaunch. With the release zip this needs the ad-hoc signed build (macOS 0.6.1+). | NOT RUN |
| B22 | Dictation: mic closes on release | Hold the key, speak, release. Repeat on a fresh install while the speech model is still downloading. | The orange mic indicator goes off as soon as the key is released. During a model download the pill shows "Installing speech model N%"; it never stays on "Transcribing…" for more than ~15 s. | NOT RUN |
| B23 | Local only away from home | Settings › Away from home on. At home: open the menu. Tether to a phone hotspot (NetBird off), wait a few seconds, open the menu. Turn NetBird on. Then, still away with NetBird off, turn **Local only** off by hand, go home, leave again. | Home: "Home network · streaming", toggle off. Hotspot: "Away from home · Local only turned on", toggle on, and a session records locally without a server error. NetBird: "On NetBird · streaming", toggle off. The hand-set toggle stays off while away ("your choice until…"), and Local only comes back on by itself after the next trip home. | NOT RUN |
| B24 | Version and lab/live tag | Settings › Server URL `https://live.apj.dev`; open the menu and Settings. Change it to `https://live.lab.apj.dev`, wait a second, look again. Start a session, then start dictating. | The menu opens with "Jarvis Voice v<version>" and "Server: LIVE · live.apj.dev (<server version>)"; Settings › Version shows the same app version, a red LIVE tag and the server version. The menu-bar icon carries an outlined LIVE tag. On lab: a filled LAB tag beside the icon, "Server: LAB · live.lab.apj.dev (…)", an orange LAB tag in Settings. Tag and icon turn red while recording and orange while dictating, and stay legible in a light and a dark menu bar. | NOT RUN |

## Known limits to check while testing

- **Audio-capture permission has no public pre-flight.** A denied tap delivers silence, so the app can only warn after ~8 s of pure silence. A genuinely silent meeting start will show the same warning until sound arrives, then clears.
- **Local-only outbox is not compacted.** Audio stays on disk until uploaded (≈ 2 × 115 MB/hour in meeting mode). Delete `~/Library/Application Support/JarvisVoice/sessions/<id>` after a successful upload if space matters.
- **Release builds are ad-hoc signed, not notarized.** macOS keeps the microphone, audio-capture and Accessibility grants across relaunches, but an update has a new signature, so expect the prompts again after installing a new version (or remove and re-add the app under Accessibility). A `make install` build with your own team keeps them across rebuilds.
- **Dictation is heard by a running session.** The session mic hears you too, so dictated words also appear in the session's Me channel.
- **Dictation pastes with a synthetic ⌘V** using the US key position of V, so on layouts that move V (Dvorak without "⌘ QWERTY") the paste may land on another key. The text is always on the clipboard regardless.
- Draft timestamps are anchored to the first buffer of each channel; expect ±100–300 ms of alignment between channels.
