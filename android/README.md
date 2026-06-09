# Jarvis Voice — Android Client (Phase 1)

Floating dictation pill for Android. Tap a text field anywhere on your device, tap the pill, speak — transcript injects directly into the focused field via Accessibility or clipboard fallback. No cloud, no round-trip latency on supported Pixel hardware.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Android Studio | Ladybug (2024.2.1) or later |
| JDK | 17 (bundled with Android Studio) |
| Android SDK | API 33+ (minSdk), API 35 (targetSdk) |
| `adb` | In PATH (ships with Android Studio platform-tools) |
| Device | Android 13+ physical device (overlay + accessibility require real hardware for full testing) |

---

## Build

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

The app walks you through three one-time grants:

1. **Overlay permission** — "Draw over other apps"
   - Dialog opens `Settings → Apps → Special app access → Display over other apps`
   - Toggle Jarvis Voice on, then navigate back. The app re-checks on resume.

2. **Microphone permission** — standard runtime dialog
   - Grant when prompted.

3. **Accessibility service**
   - Dialog opens `Settings → Accessibility → Downloaded apps → Jarvis Voice Overlay`
   - Toggle on. Android will warn about data access — this is expected (the service reads focused-node info to inject text).

Once all three are granted, `MainActivity` finishes itself and `VoiceOverlayService` starts as a sticky foreground service. You won't see the launcher activity again unless you kill the service and re-open the app.

---

## How to Use

1. Tap any text input field in any app.
2. The dark pill floats near the bottom of the screen.
3. **Tap** the pill mic icon to begin dictation.
4. Speak. Partial results scroll across the pill in real time.
5. **Tap again** to stop early, or let the 10-second silence timeout fire.
6. Transcript injects into the focused field. If injection fails (some hardened apps block `ACTION_SET_TEXT`), the pill shows "Tap to copy" — tap it to put the text on the clipboard.
7. **Drag** the pill anywhere on screen to reposition it.
8. **Long-press** the pill to trigger hold-to-record mode (releases on lift).

---

## On-Device ASR Note

`AndroidSpeechEngine` prefers on-device recognition (`SpeechRecognizer.createOnDeviceSpeechRecognizer`) when available, falling back to the standard Google recognizer (network). On-device ASR is available on:

- Pixel 6 and newer running Android 13+
- Requires "Speech Recognition & Synthesis" from Google to be downloaded in `Settings → Apps → Speech Recognition & Synthesis → Download languages`

On non-Pixel devices or when on-device is unavailable, the engine falls back to the cloud-backed recognizer automatically. Requires an active internet connection in that case.

---

## Boot Persistence

`BootReceiver` fires on `ACTION_BOOT_COMPLETED` and re-starts `VoiceOverlayService`. The service is declared `START_STICKY` — Android will restart it if killed by the system. If you want to stop it permanently, Force Stop the app from Settings.

---

## Phase 2 Roadmap

- **LiveKit WebRTC transport** — swap `AndroidSpeechEngine` for a `LiveKitSpeechEngine` implementation of `SpeechEngine` that streams raw PCM to the Jarvis LiveKit server and receives a final transcript over the data channel. The interface is already abstracted; the swap is a one-line change in `VoiceOverlayService.onCreate()`.
- **Wake-word activation** — integrate Picovoice Porcupine or ONNX-based wake word so the pill auto-activates on "Hey Jarvis" without a tap.
- **Command routing** — detect command prefix ("Jarvis, open…", "Jarvis, send…") and route to the Jarvis API instead of injecting text.

## Phase 3 Roadmap

- **Clipboard history** — maintain a local ring buffer of the last N transcripts, accessible from a swipe gesture on the pill.
- **Per-app profiles** — configure injection strategy, language model, and silence timeout per package name.
- **Floating transcript history** — expandable pill that shows last 3 dictations for quick re-injection.
