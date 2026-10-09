# Jarvis Voice

Self-hosted Android voice assistant and dictation overlay. A floating mic button transcribes speech offline and injects it into any focused text field — or routes it to Kai (your LLM) for a full voice conversation with tool calls, vault access, agent tasks, push notifications, and git workspace operations.

Built as a self-controlled alternative to Wispr Flow / Google Recorder.

---

## Features

| Feature | Status | Since |
|---------|--------|-------|
| Offline STT via Whisper (sherpa-onnx) | ✅ | v1.0 |
| Android on-device STT fallback | ✅ | v1.0 |
| Hold-to-record / tap-to-toggle | ✅ | v1.0 |
| Screen-off recording (PARTIAL_WAKE_LOCK) | ✅ | v1.0 |
| Auto-copy to clipboard + accessibility inject | ✅ | v1.0 |
| Real-time chunked VAD — no freeze on long dictation | ✅ | v1.0 |
| Ambient noise / artifact filter | ✅ | v1.0 |
| Confidence threshold filter (Android STT) | ✅ | v1.0 |
| Custom Dictionary (word substitution) | ✅ | v1.0 |
| Dictation History (SQLite session log) | ✅ | v1.0 |
| Metro stats (WPM, word count per session) | ✅ | v1.0 |
| In-app Debug Log (rotated, copyable) | ✅ | v1.0 |
| Voice chat with LLM (Kai) via LiteLLM proxy | ✅ | v1.1 |
| Kokoro network TTS (voice clone) | ✅ | v1.1.52 |
| Barge-in — tap to interrupt Kai mid-speech | ✅ | v1.1 |
| Always-on VAD barge-in + AEC | ✅ | v1.1 |
| Vault tool calls (read/write notes, shell, web fetch) | ✅ | v1.1.48 |
| Multi-session chat with persistent history | ✅ | v1.2.3 |
| Agent task delegation with live progress UI | ✅ | v1.2.4 |
| Inline agent task panel + live model selector | ✅ | v1.2.5 |
| Agent task replies + conversation threading | ✅ | v1.2.6 |
| Git workspace tools (clone, write, commit, PR) | ✅ | v1.2.6 |
| Push notifications via server-scheduled timers | ✅ | v1.2.7 |

---

## Architecture

### Full System

```
  ┌──────────────────────────────────────────────────────────────────────────┐
  │  Android Device                                                           │
  │                                                                           │
  │  ┌─────────────────────────────┐   ┌──────────────────────────────────┐  │
  │  │  VoiceOverlayService        │   │  VoiceChatActivity               │  │
  │  │  (foreground, always-on)    │   │  (full chat UI)                  │  │
  │  │                             │   │                                  │  │
  │  │  Floating 56dp button       │   │  ViewModel / StateFlow           │  │
  │  │  IDLE→RECORDING→PROCESSING  │   │  LlmRepository (tool dispatch)   │  │
  │  │  PARTIAL_WAKE_LOCK          │   │  VaultToolExecutor               │  │
  │  └──────────┬──────────────────┘   └──────────┬───────────────────────┘  │
  │             │                                  │                          │
  │             ▼                                  ▼                          │
  │  ┌──────────────────────┐         ┌────────────────────────────────────┐  │
  │  │  SpeechEngine        │         │  NotifyWorker (WorkManager 15min)  │  │
  │  │  ├─ SherpaOnnx       │         │  Polls /api/v1/notify/pending      │  │
  │  │  │  Whisper base.en  │         │  Fires system notifications        │  │
  │  │  └─ AndroidSTT       │         └────────────────────────────────────┘  │
  │  │     (fallback)       │                                                  │
  │  └──────────────────────┘                                                  │
  │                                                                           │
  └──────────────────────────┬────────────────────────┬───────────────────────┘
                             │ HTTP :8881              │ HTTP :8881
                             │                         │
  ┌──────────────────────────▼─────────────────────────▼───────────────────────┐
  │  DGX Spark (192.168.1.155)  — Jarvis Capture API                           │
  │                                                                             │
  │  FastAPI / uvicorn — /home/lordmuffin/.agent-venv                          │
  │  /opt/jarvis-voice/src/jarvis_voice/capture_api.py                         │
  │                                                                             │
  │  ┌────────────────────────────────────────────────────────────────────────┐ │
  │  │  Capture                   Vault              Agent Tasks              │ │
  │  │  POST /capture/voice       GET  /vault/note   POST /agent/tasks        │ │
  │  │  POST /capture/audio       GET  /vault/search GET  /agent/tasks        │ │
  │  │                            GET  /vault/sprint GET  /agent/tasks/{id}   │ │
  │  │  Notifications             POST /vault/note/  POST /agent/tasks/{id}/  │ │
  │  │  POST /notify/schedule          append             reply               │ │
  │  │  GET  /notify/pending      POST /vault/note/  DEL  /agent/tasks/{id}  │ │
  │  │  DEL  /notify/{id}              write                                  │ │
  │  │  GET  /notify                                                          │ │
  │  │                            Git Workspaces     System                   │ │
  │  │                            POST /git/clone    POST /system/exec        │ │
  │  │                            POST /git/write    GET  /web/fetch          │ │
  │  │                            GET  /git/status                            │ │
  │  │                            POST /git/commit                            │ │
  │  │                            POST /git/pr                                │ │
  │  └────────────────────────────────────────────────────────────────────────┘ │
  │                                   │                                         │
  │                         ┌─────────▼──────────┐                              │
  │                         │  LiteLLM Proxy      │                              │
  │                         │  192.168.1.93:4000  │                              │
  │                         │  qwen3-heavy        │                              │
  │                         └─────────────────────┘                              │
  │                                                                             │
  │  Vault: /home/lordmuffin/Notes/ (SSHFS or local)                           │
  │  Git workspaces: /home/lordmuffin/jarvis-workspaces/                        │
  └─────────────────────────────────────────────────────────────────────────────┘

  Supporting services (homelab)
  ├── Kokoro TTS   192.168.1.43:8880   voice clone TTS
  └── LiveKit      192.168.1.101:7880  WebRTC media (voice agent mode)
```

### Android Component Map

```
com.lordmuffin.jarvisvoice/
│
├── VoiceOverlayService.kt         # Foreground service — floating mic, state machine
├── JarvisAccessibilityService.kt  # Tracks focused node for text injection
├── TextInjector.kt                # ACTION_SET_TEXT (accessibility) + clipboard fallback
├── TranscriptProcessor.kt         # Ambient filter, bracket/filler strip
├── CustomDictionaryManager.kt     # Word substitution (SharedPrefs)
├── DictationHistoryManager.kt     # SQLite session log
├── DebugLog.kt                    # Async rotating log (200 KB cap)
│
├── speech/
│   ├── SpeechEngine.kt            # Interface
│   ├── SherpaOnnxSpeechEngine.kt  # Offline Whisper base.en via sherpa-onnx
│   └── AndroidSpeechEngine.kt     # On-device / Google STT fallback
│
├── chat/
│   ├── VoiceChatViewModel.kt      # StateFlow, coroutines, ViewModel lifecycle
│   ├── LlmRepository.kt           # LiteLLM streaming + Vault tool dispatch
│   └── ChatMessage.kt             # Message model (role, content, timestamp)
│
├── notify/
│   ├── AppNotification.kt         # Data class (id, title, body, firesAt)
│   ├── NotificationRepository.kt  # OkHttp — /notify/schedule + /notify/pending
│   └── NotifyWorker.kt            # WorkManager 15-min poll, NotificationCompat
│
├── VoiceChatActivity.kt           # Full-screen chat UI + Kokoro TTS playback
├── AgentTaskActivity.kt           # Agent task list, bottom-sheet conversation
├── MainActivity.kt                # Permission gate → Settings
├── SettingsActivity.kt            # STT engine, vault URL/key, features
├── HistoryActivity.kt             # Session log viewer
├── DictionaryActivity.kt          # Word substitution pairs
└── DebugLogActivity.kt            # In-app debug log viewer
```

### STT Pipeline (dictation path)

```
Microphone
    │
    ▼
SherpaOnnxSpeechEngine
    • VAD silence flush every 1.5s
    • Whisper chunks ≤ 3s (accumulated via committedText)
    • Ambient noise check → discard
    │  OR
AndroidSpeechEngine (fallback)
    • Confidence threshold ≥ 0.65
    │
    ▼
TranscriptProcessor
    • Strip [X] / (X) brackets
    • Optional filler word removal
    │
    ▼
CustomDictionaryManager
    • Apply word substitutions
    │
    ▼
TextInjector
    • ACTION_SET_TEXT (accessibility)
    • Clipboard fallback
```

### Voice Chat Pipeline

```
Microphone → SpeechEngine → LlmRepository
                                  │
                    ┌─────────────▼──────────────┐
                    │  LiteLLM Proxy (:4000)      │
                    │  Qwen3 / any OpenAI-compat  │
                    └─────────────┬───────────────┘
                                  │ tool_use response?
                          ┌───────▼──────────┐
                          │  VaultTool?       │ ──► POST /api/v1/vault/...
                          │  AgentTask?       │ ──► POST /api/v1/agent/tasks
                          │  ScheduleNotif?   │ ──► POST /api/v1/notify/schedule
                          │  GitOp?           │ ──► POST /api/v1/git/...
                          │  SystemExec?      │ ──► POST /api/v1/system/exec
                          └───────┬───────────┘
                                  │ tool result → back to LLM
                                  ▼
                    Kokoro TTS (:8880) or Android TTS
                                  │
                               Speaker
```

---

## Server-Side Components

### Jarvis Capture API

**Location:** `/opt/jarvis-voice/src/jarvis_voice/capture_api.py`  
**Port:** `8881`  
**Auth:** `x-jarvis-key` header (default key baked into both sides — see Configuration)

#### API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `POST` | `/api/v1/capture/voice` | Land a pre-transcribed voice note in vault inbox |
| `POST` | `/api/v1/capture/audio` | Upload raw audio — server runs faster-whisper |
| `GET` | `/api/v1/vault/note` | Read a vault note by path |
| `GET` | `/api/v1/vault/search` | Full-text search across vault |
| `GET` | `/api/v1/vault/sprint-state` | Read current sprint board state |
| `POST` | `/api/v1/vault/note/append` | Append text to a vault note |
| `POST` | `/api/v1/vault/note/write` | Write/create a vault note |
| `GET` | `/api/v1/web/fetch` | Fetch a URL, strip HTML, return text |
| `POST` | `/api/v1/system/exec` | Run a shell command on the server |
| `POST` | `/api/v1/notify/schedule` | Schedule a push notification (`delay_minutes`) |
| `GET` | `/api/v1/notify/pending` | Poll for notifications that have fired |
| `DELETE` | `/api/v1/notify/{id}` | Cancel a scheduled notification |
| `GET` | `/api/v1/notify` | List all scheduled notifications |
| `POST` | `/api/v1/agent/tasks` | Create + run a background LLM task |
| `GET` | `/api/v1/agent/tasks` | List all agent tasks |
| `GET` | `/api/v1/agent/tasks/{id}` | Get a single agent task |
| `POST` | `/api/v1/agent/tasks/{id}/reply` | Continue an agent task conversation |
| `DELETE` | `/api/v1/agent/tasks/{id}` | Delete an agent task |
| `POST` | `/api/v1/git/clone` | Clone a GitHub repo into a workspace |
| `POST` | `/api/v1/git/write` | Write a file in a workspace |
| `GET` | `/api/v1/git/status` | `git status` for a workspace |
| `POST` | `/api/v1/git/commit` | Commit staged changes |
| `POST` | `/api/v1/git/pr` | Push branch + create pull request |

### Supporting Services

| Service | Address | Purpose |
|---------|---------|---------|
| LiteLLM Proxy | 192.168.1.93:4000 | OpenAI-compat LLM gateway (Qwen3, etc.) |
| Kokoro TTS | 192.168.1.43:8880 | Network TTS — voice clone |
| LiveKit | 192.168.1.101:7880 | WebRTC media server (voice agent mode) |

---

## Installation

### Prerequisites

| Component | Requirement |
|-----------|------------|
| Android device | API 26+ (Android 8.0+), ARM64 |
| Build machine | JDK 17, Android SDK API 33+ |
| Server | Python 3.10+, `faster-whisper`, `ffmpeg` |
| LLM | LiteLLM proxy at a reachable IP |
| TTS (optional) | Kokoro TTS server |

### Android — Install from Release APK

1. Download `jarvis-voice-vX.X.X.apk` from [Releases](https://github.com/lordmuffin/jarvis-voice/releases)
2. Pause Play Protect: Settings → Security → Google Play Protect → ⚙️ → disable **Scan apps with Play Protect**
3. Open the APK from your file manager and tap **Install**
4. Re-enable Play Protect if desired

**Signing note:** v1.2.6+ are release-signed (`CN=Andrew Jackson, OU=Jarvis`). If upgrading from a debug build, uninstall first:
```bash
adb uninstall com.lordmuffin.jarvisvoice
```

**Via ADB** (bypasses Play Protect entirely):
```bash
adb install jarvis-voice-v1.2.7.apk
```

### Android — Build from Source

```bash
git clone https://github.com/lordmuffin/jarvis-voice
cd jarvis-voice

# Download sherpa-onnx Kotlin bindings, .so files, and whisper-base.en (~145 MB)
bash download-models.sh

# Release build (requires signing — see Configuration below)
cd android
source ../.signing.env
export KEYSTORE_PATH KEY_ALIAS KEYSTORE_PASSWORD
./gradlew assembleRelease

# Or debug build (no signing needed)
./gradlew assembleDebug

adb install app/build/outputs/apk/release/app-release.apk
```

### Server — Capture API

```bash
# On your server node
git clone https://github.com/lordmuffin/jarvis-voice /opt/jarvis-voice
cd /opt/jarvis-voice

# Create virtualenv
python3 -m venv ~/.agent-venv
~/.agent-venv/bin/pip install -r requirements.txt -r requirements-agent.txt

# Configure
cp jarvis-agent.env.template ~/.jarvis-agent.env
chmod 600 ~/.jarvis-agent.env
# Edit ~/.jarvis-agent.env — set LIVEKIT_URL, LITELLM_URL, etc.

# Install via systemd (recommended)
cp systemd/jarvis-capture-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now jarvis-capture-api

# Or run manually
cd /opt/jarvis-voice/src
nohup ~/.agent-venv/bin/uvicorn jarvis_voice.capture_api:app \
    --host 0.0.0.0 --port 8881 >> /tmp/capture_api.log 2>&1 &

# Verify
curl http://localhost:8881/health
# {"status":"ok"}
```

### SSH / Git Auth (for agent git operations)

The server uses a deploy key to push to GitHub. If port 22 outbound is blocked, this config routes via `ssh.github.com:443`:

```bash
mkdir -p ~/.config/jarvis
cat > ~/.config/jarvis/github-ssh.config << 'EOF'
Host github.com
    Hostname ssh.github.com
    Port 443
    IdentityFile /home/lordmuffin/.ssh/github-homelab-deployer
    StrictHostKeyChecking accept-new
    IdentitiesOnly yes
EOF

# Test
ssh -F ~/.config/jarvis/github-ssh.config -T git@github.com
# Hi lordmuffin/jarvis-voice! You've successfully authenticated...
```

`capture_api.py` automatically injects `GIT_SSH_COMMAND` for all git subprocesses when `~/.config/jarvis/github-ssh.config` exists.

---

## Configuration

### Server: `~/.jarvis-agent.env`

```bash
# Auth — required (no default; API returns 503 if unset). Must match "Capture key" in the app.
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
JARVIS_CAPTURE_KEY=change-me

# Shell/git endpoints and agent tools are off by default
# JARVIS_ENABLE_DANGEROUS_TOOLS=false

# Vault root (defaults to /home/lordmuffin/Notes)
# VAULT_ROOT=/home/lordmuffin/Notes

# LiteLLM proxy
LITELLM_URL=http://192.168.1.93:4000
LITELLM_MODEL=qwen3-heavy

# LiveKit (voice agent mode)
LIVEKIT_URL=ws://192.168.1.101:7880
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# Kokoro TTS (optional — omit to use Android TTS)
KOKORO_URL=http://192.168.1.43:8880/v1/audio/speech
KOKORO_VOICE=af_sky
```

### Android App Settings

Configure from the in-app Settings screen (tap app icon or notification → Settings):

| Setting | Description |
|---------|-------------|
| Vault URL | Server base URL, e.g. `http://192.168.1.155:8881` |
| Vault API Key | Must match `JARVIS_CAPTURE_KEY` on server |
| STT Engine | Sherpa-ONNX (offline) or Android STT |
| LLM URL | LiteLLM proxy, e.g. `http://192.168.1.93:4000` |
| Kokoro TTS URL | TTS endpoint URL |
| Clipboard Notification | Toast/notification after each dictation |

### APK Release Signing

```bash
# Generate keystore (one-time)
keytool -genkeypair -v -keystore jarvis-voice-release.jks \
  -alias jarvis-voice -keyalg RSA -keysize 4096 -validity 10000 \
  -dname "CN=Andrew Jackson, OU=Jarvis, O=lordmuffin"

# Store credentials (gitignored)
cat > .signing.env << 'EOF'
KEYSTORE_PATH=/home/lordmuffin/jarvis-voice/jarvis-voice-release.jks
KEYSTORE_PASSWORD=<your-password>
KEY_ALIAS=jarvis-voice
EOF

# Build
source .signing.env && export KEYSTORE_PATH KEY_ALIAS KEYSTORE_PASSWORD
cd android && ./gradlew assembleRelease
```

`.jks` files and `.signing.env` are gitignored.

---

## Permissions

| Permission | Why |
|-----------|-----|
| `SYSTEM_ALERT_WINDOW` | Floating overlay over other apps |
| `RECORD_AUDIO` | Microphone access |
| `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_MICROPHONE` | Keep recording alive in background |
| `WAKE_LOCK` | Keep CPU awake when screen turns off mid-dictation |
| `RECEIVE_BOOT_COMPLETED` | Auto-start overlay after reboot |
| `ACCESSIBILITY_SERVICE` | Detect focused text field, inject text |
| `POST_NOTIFICATIONS` | Push notification delivery (Android 13+) |
| `INTERNET` | Vault API, LLM, Kokoro TTS |

---

## Repo Layout

```
jarvis-voice/
├── download-models.sh                    # Dev setup: sherpa-onnx + whisper model
├── .env.example                          # Minimal server env template
├── jarvis-agent.env.template             # Full server config template
├── requirements.txt                      # FastAPI, faster-whisper, uvicorn
├── requirements-agent.txt                # LiveKit agent extras
│
├── android/
│   └── app/src/main/java/com/lordmuffin/jarvisvoice/
│       ├── VoiceOverlayService.kt        # Core service + overlay state machine
│       ├── JarvisAccessibilityService.kt
│       ├── TextInjector.kt
│       ├── TranscriptProcessor.kt
│       ├── CustomDictionaryManager.kt
│       ├── DictationHistoryManager.kt
│       ├── DebugLog.kt
│       ├── speech/                       # SpeechEngine, SherpaOnnx, Android
│       ├── chat/                         # ViewModel, LlmRepository, ChatMessage
│       ├── notify/                       # AppNotification, NotifyWorker, Repo
│       ├── VoiceChatActivity.kt
│       ├── AgentTaskActivity.kt
│       ├── MainActivity.kt
│       ├── SettingsActivity.kt
│       ├── HistoryActivity.kt
│       ├── DictionaryActivity.kt
│       └── DebugLogActivity.kt
│
├── src/jarvis_voice/
│   ├── capture_api.py                    # All FastAPI endpoints
│   ├── vault.py                          # Vault path helpers + note writer
│   ├── vault_tools.py                    # LLM tool definitions
│   ├── transcribe.py                     # faster-whisper wrapper
│   ├── livekit_agent.py                  # LiveKit voice agent
│   └── f5_tts_api.py                     # F5-TTS voice clone endpoint
│
├── systemd/
│   ├── jarvis-capture-api.service        # systemd unit for capture API
│   ├── jarvis-voice-agent.service        # systemd unit for LiveKit agent
│   ├── jarvis-f5-tts.service             # systemd unit for F5-TTS
│   ├── mnt-notes.mount                   # SSHFS vault mount
│   └── mnt-notes.automount
│
└── .github/workflows/
    ├── build.yml                         # CI — debug APK on push
    └── release.yml                       # Release workflow
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "overlay permission required" | Grant **Display over other apps**: Android Settings → Apps → Jarvis Voice → Special app access |
| No text injected | Enable **Jarvis accessibility service**: Android Settings → Accessibility |
| Sherpa engine fails to init | Re-run `bash download-models.sh`; verify model files in `assets/models/whisper-base-en/` |
| Recording stops when screen turns off | PARTIAL_WAKE_LOCK should prevent this — check Debug Log for "wake lock released" |
| "App not installed" | Play Protect blocking sideload. Pause it: Settings → Security → Google Play Protect → ⚙️ |
| Signature conflict on install | `adb uninstall com.lordmuffin.jarvisvoice` then reinstall |
| Server unreachable | `curl http://192.168.1.155:8881/health` — if fails, check `pgrep -f uvicorn` and restart |
| LLM not responding | Check LiteLLM proxy at :4000; verify `LITELLM_URL` in `~/.jarvis-agent.env` |
| Notifications not arriving | Confirm `POST_NOTIFICATIONS` granted; check WorkManager enrolled: `adb shell dumpsys jobscheduler \| grep jarvis` |
| Git push fails on server | Test SSH: `ssh -F ~/.config/jarvis/github-ssh.config -T git@github.com`; confirm deploy key is in repo → Settings → Deploy keys |
| Agent task stuck | GET `/api/v1/agent/tasks/{id}` — check `status` and `error` fields; review `/tmp/capture_api.log` |

---

## Changelog

| Version | Highlight |
|---------|-----------|
| **v1.2.7** | Push notifications — `schedule_notification` LLM tool, WorkManager 15-min polling, server-side notify endpoints |
| **v1.2.6** | Agent task replies with conversation threading; git workspace tools (clone/write/commit/PR) via SSH deploy key |
| **v1.2.5** | Inline agent task panel + live model selector on Agent screen |
| **v1.2.4** | Background LLM agent tasks with live progress UI |
| **v1.2.3** | Multi-session persistent chat history + session switcher |
| **v1.2.0** | Stable: full chat history across restarts, STT double-release crash fix |
| **v1.1.52** | Kokoro network TTS — voice clone over HTTP |
| **v1.1.48** | Vault tool calls in Android chat (read/write/search/shell/web) |
| **v1.1** | Voice chat with Kai, barge-in, always-on VAD + AEC |
| **v1.0** | Offline Whisper dictation overlay — core product |

---

## License

MIT — see [LICENSE](LICENSE).
