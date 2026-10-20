# Jarvis Live for macOS

A working menu-bar app (`JarvisVoiceMac`, SwiftUI, macOS 26+) and the Swift package it runs on (`JarvisLiveKit`).

## What it does

- **Live sessions.** Records the microphone (Me) and, in Meeting mode, system audio (Them), shows on-device draft text at once and streams audio to a Jarvis Live server for the authoritative transcript. Local only mode records on this Mac and uploads later.
- **Menu-bar item.** Idle waveform, recording capsule with the elapsed time, dictating capsule with a microphone. A badge names the server's channel: LAB filled, LIVE outlined, all monochrome so it follows the menu bar. Menu: Start/Stop Session (⌃⌥J), Mode, Local only, Add Marker (⌃⌥M), Copilot panel, Upload for Full Processing, Open Local Transcripts, Settings, Quit.
- **Copilot panel.** Non-activating floating panel in the *B · Feed* layout: header with the recording state, a time-ordered feed (draft and final lines swap in place, Me / Them, markers, "new since you looked" marks), a "Now" dock with the newest suggestion that fades as it expires, and notes, actions, decisions and Related (`obsidian://` links) by kind. Layouts for about 320, 480 and 760 points, plus a collapsed state. The On top toggle keeps it above other windows.
- **Dictation.** A right modifier key (hold, tap-tap or double-tap) dictates anywhere, entirely on this Mac, and shows the shared pill: Listening, Transcribing, the last words, Copied, Didn't catch that.
- **Settings.** Server URL and device token (Keychain), Away from home, microphone, panel, dictation key, mode and paste.

## Design

The app follows system light and dark and is styled only with [Instrument](https://github.com/lordmuffin/instrument): colours from `Color.inst.*`, type from `InstType` with `.instStyle()`, sizes from `InstSpace`, `InstRadius` and `InstSize`. The vendored copy lives in `JarvisVoiceMac/Sources/Instrument/` (managed by the Instrument sync PR, never edit it); `project.yml` compiles `InstrumentTokens.swift` and bundles `Instrument.xcassets` in the app target. Instrument components for SwiftUI are in `Sources/UI/Components/`. The artboards are in `docs/design/instrument/mac/`. Don't add hex colours or fixed fonts to the app: use a token, or add one to Instrument.

## Build and test

```sh
brew install xcodegen            # Xcode 26+ required
cd macos/JarvisVoiceMac
make build                       # unsigned compile check
make test                        # unit tests (JarvisVoiceMacTests)
cd ../JarvisLiveKit && swift test
```

For a signed build you can install, copy `Local.xcconfig.template` to `Local.xcconfig`, set `DEVELOPMENT_TEAM` and run `make install`. Audio, permission prompts and the panel on screen can't run in CI: see [`JarvisVoiceMac/TESTING.md`](JarvisVoiceMac/TESTING.md).

## Layout

| Path | What |
|---|---|
| `JarvisLiveKit/` | Protocol types, transport, outbox, session store (consumes `protocol/v1/fixtures`) |
| `JarvisVoiceMac/Sources/App` | `AppModel`, settings, hotkeys, pipeline |
| `JarvisVoiceMac/Sources/UI` | Menu, icon, Copilot panel, HUD, settings, `PresentationLogic.swift` (the testable rules) |
| `JarvisVoiceMac/Sources/Instrument` | Vendored design system (managed) |
