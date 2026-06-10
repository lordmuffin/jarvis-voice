# Jarvis Voice

Self-hostable Android dictation overlay. Hold or tap a floating mic button — speech is transcribed offline and injected directly into whatever text field has focus. No cloud. No account. No subscription.

Built as a Wispr Flow / Google Eloquent alternative that you actually control.

---

## Features

| Feature | Status |
|---------|--------|
| Offline STT via Whisper (sherpa-onnx) | ✅ |
| On-device Android STT fallback | ✅ |
| Hold-to-record / tap-to-toggle | ✅ |
| Screen-off recording (PARTIAL_WAKE_LOCK) | ✅ |
| Auto-copy to clipboard + paste into focused field | ✅ |
| Real-time chunked VAD — no freeze on long dictations | ✅ |
| Ambient noise filter (buzzer/beep discarded) | ✅ |
| Confidence threshold filter (Android STT) | ✅ |
| Custom Dictionary (word substitution) | ✅ |
| Dictation History (SQLite, session log) | ✅ |
| Metro stats (WPM, word count per session) | ✅ |
| In-app Debug Log (rotated, copyable) | ✅ |
| Clipboard notification (toggle in Settings) | ✅ |
| Settings accessible from app icon and notification | ✅ |

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
  Accessibility     │  VoiceOverlayService (foreground)        │
  Service tracks ──►│    • Floating 56dp button overlay        │
  focused node      │    • IDLE → RECORDING → PROCESSING→ DONE │
                    │    • PARTIAL_WAKE_LOCK while recording    │
                    └───────────────┬─────────────────────────┘
                                    │ startListening / stopListening
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  SpeechEngine (interface)                │
                    │   ├── SherpaOnnxSpeechEngine             │
                    │   │    • Offline Whisper base.en         │
                    │   │    • VAD-flush chunked (≤3s/chunk)   │
                    │   └── AndroidSpeechEngine (fallback)     │
                    │        • On-device / cloud Google STT    │
                    │        • Confidence threshold ≥ 0.65     │
                    └───────────────┬─────────────────────────┘
                                    │ onFinal(text)
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  TranscriptProcessor                     │
                    │    • Ambient noise check → discard       │
                    │    • Bracket removal [X] / (X)           │
                    │    • Optional filler word strip          │
                    └───────────────┬─────────────────────────┘
                                    │ filtered text
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  CustomDictionaryManager                 │
                    │    • Word substitution (SharedPrefs)     │
                    └───────────────┬─────────────────────────┘
                                    │ final text
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │  TextInjector                           │
                    │    • ACTION_SET_TEXT via accessibility   │
                    │    • Clipboard fallback                  │
                    └─────────────────────────────────────────┘
```

**Recording modes:**
- **Hold:** button held → records while held, stops instantly on release (no silence detection)
- **Tap:** toggle on → records until 30s of silence (VAD auto-stop), or tap again to stop

**Long dictation handling:** Audio is flush-processed every 1.5s of silence into bounded ~3s Whisper chunks. `committedText` accumulates across flushes. No freeze on 60s+ recordings.

---

## Build

### Prerequisites

- Android Studio Hedgehog+ or command-line tools
- JDK 17
- Android SDK API 33+
- ~2 GB free disk (model)

### First build

```bash
git clone https://github.com/lordmuffin/jarvis-voice
cd jarvis-voice

# Download sherpa-onnx Kotlin bindings, native .so files, and whisper-base.en (~145 MB)
bash download-models.sh

# Build
cd android
ANDROID_HOME=~/Android/Sdk ./gradlew assembleDebug

# Install (device connected via USB or ADB over network)
adb install app/build/outputs/apk/debug/app-debug.apk
```

For CI or builds without the model (AndroidSpeechEngine fallback only):
```bash
bash download-models.sh --no-model
```

### Gradle notes

The project sets `org.gradle.jvmargs=-Xmx4g` in `gradle.properties` and marks `.onnx`/`.bin` assets as no-compress to avoid Java heap OOM during asset packaging.

---

## Repo layout

```
jarvis-voice/
├── download-models.sh                    # dev setup script
├── android/
│   └── app/src/main/
│       ├── java/com/lordmuffin/jarvisvoice/
│       │   ├── VoiceOverlayService.kt    # core service, overlay state machine
│       │   ├── MainActivity.kt           # permission gate → opens Settings
│       │   ├── SettingsActivity.kt       # STT choice, Metro stats, features
│       │   ├── HistoryActivity.kt        # session log (SQLite)
│       │   ├── DictionaryActivity.kt     # word substitution pairs
│       │   ├── DebugLogActivity.kt       # in-app debug viewer
│       │   ├── TranscriptProcessor.kt    # ambient filter + bracket/filler strip
│       │   ├── CustomDictionaryManager.kt
│       │   ├── DictationHistoryManager.kt
│       │   ├── DebugLog.kt               # async rotating log (200KB)
│       │   ├── TextInjector.kt           # clipboard + accessibility inject
│       │   ├── JarvisAccessibilityService.kt
│       │   └── speech/
│       │       ├── SpeechEngine.kt       # interface
│       │       ├── SherpaOnnxSpeechEngine.kt   # offline Whisper
│       │       └── AndroidSpeechEngine.kt      # Google STT fallback
│       ├── assets/models/whisper-base-en/      # git-ignored, ~145 MB
│       └── jniLibs/arm64-v8a/                  # git-ignored, sherpa-onnx .so files
├── .github/workflows/build.yml           # CI — debug APK on push
├── LICENSE                               # MIT
└── CONTRIBUTING.md
```

---

## Permissions

| Permission | Why |
|-----------|-----|
| `SYSTEM_ALERT_WINDOW` | Floating overlay over other apps |
| `RECORD_AUDIO` | Microphone access for dictation |
| `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_MICROPHONE` | Keep recording alive in background |
| `WAKE_LOCK` | Keep CPU awake when screen turns off mid-dictation |
| `RECEIVE_BOOT_COMPLETED` | Auto-start overlay after device reboot |
| `ACCESSIBILITY_SERVICE` | Detect focused text field, inject text |

---

## Settings

Access: tap the app icon, or pull down notification shade → Settings button.

| Setting | Description |
|---------|-------------|
| STT Engine | Sherpa-ONNX (offline) or Android STT |
| Clipboard Notification | Toast + notification shade alert after each dictation |
| History | Session log — timestamp, WPM, word count, transcript |
| Dictionary | Word substitution pairs (applied post-transcription) |
| Debug Log | Timestamped internal log — share for bug reports |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| App shows "overlay permission required" | Grant Display over other apps in Android Settings |
| No text injected | Enable the Jarvis accessibility service in Android Settings → Accessibility |
| Sherpa engine fails to init | Check `download-models.sh` ran successfully; model files in `assets/models/whisper-base-en/` |
| Recording stops when screen turns off | Should be fixed in current build (PARTIAL_WAKE_LOCK). Check Debug Log for "wake lock released" |
| Ambient noise transcribed | Expected behavior from Android STT fallback; Sherpa engine has better ambient filtering |

---

## License

MIT — see [LICENSE](LICENSE).
