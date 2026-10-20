# Jarvis Voice — Android Client (Phase 1)

Floating dictation pill for Android. Tap a text field anywhere on your device, tap the pill, speak — transcript injects directly into the focused field via Accessibility or clipboard fallback. No cloud, no round-trip latency on supported Pixel hardware.

The app also hosts **Jarvis Live** (session recording and history), **Kai Chat**, **Agent** tasks, on-device **Models**, and **Settings**. The launcher icon opens **Record**, the home screen. The UI follows the system light/dark setting (Settings → Device → Appearance overrides it) and uses the [Instrument](../docs/design/instrument/README.md) design system. Record and Session Detail switch to two panes at 840dp wide (unfolded).

---

## Prerequisites

| Tool | Version |
|------|---------|
| Android Studio | Ladybug (2024.2.1) or later |
| JDK | 17 (bundled with Android Studio) |
| Android SDK | compileSdk 34; minSdk 26, targetSdk 34 (`app/build.gradle.kts`) |
| `adb` | In PATH (ships with Android Studio platform-tools) |
| Device | Android 8.0+ (API 26) physical device; Android 13+ for the system on-device recognizer fallback (overlay + accessibility require real hardware for full testing) |

---

## Build

First fetch the sherpa-onnx (on-device Whisper) Kotlin bindings, native libraries and model, from the repo root:

```bash
bash download-models.sh
```

The bindings and native libraries are pinned to the same sherpa-onnx release (`SHERPA_VERSION` in the script). They must match: the native code builds Kotlin objects by signature, so mixing versions crashes the app on the first transcription. Re-run the script after changing `SHERPA_VERSION`; it refreshes files from any other version.

```bash
cd android/
./gradlew assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk`

For a release build (unsigned):
```bash
./gradlew assembleRelease
```

---

## Install & Sideload

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Or drag the APK onto a device with Developer Options → Install unknown apps enabled.

---

## First Launch — Permission Setup

Onboarding walks you through these one-time grants:

1. **Overlay permission** — "Draw over other apps"
   - Dialog opens `Settings → Apps → Special app access → Display over other apps`
   - Toggle Jarvis Voice on, then navigate back. The app re-checks on resume.

2. **Microphone permission** — standard runtime dialog
   - Grant when prompted.

3. **Accessibility service**
   - Dialog opens `Settings → Accessibility → Downloaded apps → Jarvis Voice Overlay`
   - Toggle on. Android will warn about data access — this is expected (the service reads focused-node info to inject text).

4. **All files access** (optional, Android 11+) — keeps your dictation history across a reinstall.

Once overlay, microphone and accessibility are granted, `VoiceOverlayService` starts as a foreground service and the launcher icon opens **Record**. From then on the icon always lands on Record (`MainActivity` re-checks the grants first and asks for any that were revoked).

---

## How to Use

1. Tap any text input field in any app.
2. The pill appears near the bottom of the screen while a keyboard is open, and hides when it closes.
3. **Tap** the pill to begin dictation.
4. Speak. The pill shows "Listening", a waveform and a timer; it does not show partial text (the pill is compact).
5. **Tap again** to stop. In tap mode both engines also stop on their own after a pause: the default on-device Whisper engine commits and finishes after about 3 s of silence following speech (and gives up after about 60 s of pure silence), and the Android-recognizer fallback (see below) stops after 2.5 s. Hold mode (long-press) never auto-stops.
6. The transcript is always copied to the clipboard, then injected into the focused field (`ACTION_SET_TEXT`, falling back to paste). A toast reports the result, for example "42 words · 138 wpm", or "Copied · 42 words · 138 wpm" when no field had focus.
7. **Drag** the pill anywhere on screen to reposition it; it remembers where you left it.
8. **Long-press** the pill to trigger hold-to-record mode (releases on lift).

The pill's state lives in one place, `DictationPill` (`pill/PillModel.kt`); `PillView` draws it and the Android Auto screen reads it. It follows the system light/dark setting.

| State | Pill | Tap |
|---|---|---|
| Idle | Breathing dot | Start recording |
| Listening | Accent outline, "Listening", waveform, timer | Stop and transcribe |
| Processing | Spinner and "Processing" while Whisper (and the optional on-device LLM pass) runs | Ignored |
| Done | A tick, accent outline; lasts 1.5 s after injection, then Idle | Back to Idle |
| Error | Red (or dashed) outline and a short reason; clears itself after 6 s | See below |

Errors that used to fail silently now show on the pill:

| Message | Meaning | Tap |
|---|---|---|
| ✕ Didn't catch that | Nothing usable was heard | Try again |
| ✕ Microphone is off | Microphone permission is off or the recorder could not start | Opens the app's system settings |
| ⚑ Mic in use | A Jarvis Live session has the microphone | — |
| ✕ Speech model unavailable | No speech model is downloaded or the engine failed to start | Opens Settings → STT Model |

---

## On-Device ASR Note

`SpeechEngineFactory` picks the engine. By default it is `SherpaOnnxSpeechEngine` (on-device Whisper), using the model chosen under Settings → STT Model. When "On-device STT (Offline)" is turned off, or no Whisper model is downloaded, it falls back to `AndroidSpeechEngine`.

`AndroidSpeechEngine` prefers on-device recognition (`SpeechRecognizer.createOnDeviceSpeechRecognizer`) when available, falling back to the standard Google recognizer (network). On-device ASR is available on:

- Pixel 6 and newer running Android 13+
- Requires "Speech Recognition & Synthesis" from Google to be downloaded in `Settings → Apps → Speech Recognition & Synthesis → Download languages`

On non-Pixel devices or when on-device is unavailable, the engine falls back to the cloud-backed recognizer automatically. Requires an active internet connection in that case.

---

## Boot Persistence

`BootReceiver` fires on `ACTION_BOOT_COMPLETED`. Below Android 14 it re-starts `VoiceOverlayService`; on Android 14+ the system will not allow a microphone foreground service to start from boot, so it posts a "Tap to restart the dictation overlay" notification instead. The service returns `START_NOT_STICKY`: if the system kills it, open the app (or tap that notification) to bring it back. To stop it permanently, Force Stop the app from Settings.

---

## Phase 2 Roadmap

- **LiveKit WebRTC transport** — swap `AndroidSpeechEngine` for a `LiveKitSpeechEngine` implementation of `SpeechEngine` that streams raw PCM to the Jarvis LiveKit server and receives a final transcript over the data channel. The interface is already abstracted; engines are chosen in `SpeechEngineFactory`.
- **Wake-word activation** — integrate Picovoice Porcupine or ONNX-based wake word so the pill auto-activates on "Hey Jarvis" without a tap.
- **Command routing** — detect command prefix ("Jarvis, open…", "Jarvis, send…") and route to the Jarvis API instead of injecting text.

## Phase 3 Roadmap

- **Clipboard history** — maintain a local ring buffer of the last N transcripts, accessible from a swipe gesture on the pill.
- **Per-app profiles** — configure injection strategy, language model, and silence timeout per package name.
- **Floating transcript history** — expandable pill that shows last 3 dictations for quick re-injection.
