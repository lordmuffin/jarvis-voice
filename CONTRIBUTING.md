# Contributing to Jarvis Voice

## What this is

Jarvis Voice is a self-hostable Android dictation overlay — a Wispr Flow / Eloquent alternative with offline STT (Whisper via sherpa-onnx) and no cloud dependency. Contributions that improve dictation accuracy, UX, or offline capability are most welcome.

## Before you start

- Open an issue to discuss significant changes before writing code. Small bug fixes and typo corrections can go straight to a PR.
- All new features should align with the offline-first, privacy-first design: no telemetry, no analytics, no third-party cloud services baked into the default path.

## Dev setup

### Prerequisites

- Android Studio Hedgehog or later
- JDK 17
- Android SDK with API 33+
- ~2 GB free disk for the whisper-base.en model

### First build

```bash
git clone https://github.com/lordmuffin/jarvis-voice
cd jarvis-voice
bash download-models.sh         # downloads sherpa-onnx libs + 145 MB model
cd android
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

For CI or model-free testing:
```bash
bash download-models.sh --no-model   # skips 145 MB download; SherpaOnnxSpeechEngine won't work
```

## Code style

- Kotlin only for Android code. No Java new files.
- Follow the existing patterns: single-responsibility classes, no external libraries without discussion.
- All new logging goes through `DebugLog` (not `android.util.Log`) so the in-app debug viewer captures it.

## Testing

There are no automated Android tests yet — this is a known gap. For PRs that touch the dictation pipeline (`SherpaOnnxSpeechEngine`, `VoiceOverlayService`, `TranscriptProcessor`), include a manual test checklist in the PR description:

- [ ] Tap recording: starts, stops after silence
- [ ] Hold recording: records while held, stops on release
- [ ] Screen-off recording: continues after screen turns off
- [ ] Ambient noise (buzzer/beep): filtered, not injected
- [ ] Dictionary substitution: applied correctly
- [ ] History: session appears with correct WPM

## Pull requests

- Keep PRs focused. One feature or fix per PR.
- PR title format: `feat: short description` / `fix: short description` / `chore: ...`
- Reference any related issue: `Closes #N`
- The CI workflow builds a debug APK — all PRs must pass before merge.

## What we won't accept

- Google Play Services dependencies
- Cloud STT fallbacks that phone home by default
- Any analytics, crash reporting, or usage tracking

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
