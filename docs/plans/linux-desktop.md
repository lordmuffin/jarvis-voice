# Plan: Jarvis Voice on the Linux desktop

Status: **approved 2026-11-03**, not implemented. Task 23. Target: Andrew's daily driver (CachyOS, KDE Plasma
6.7.5, KWin Wayland), replacing WisprFlow and matching the macOS app's desktop features.
Builds on the headless client from task 10 (`server/src/jarvis_live/client/`); no second client.
**No `protocol/` change for phases 1 to 3 as scoped**; resuming an ended session (section 3.1, question J) would be the one exception.

## 0. Facts this plan rests on (checked on this machine, 2026-11-03)

| Fact | How checked | Consequence |
|---|---|---|
| KWin 6.7.5 advertises `zwlr_layer_shell_v1` v5, `ext_data_control_manager_v1` v1, `zwp_input_method_v1`; **not** `zwp_virtual_keyboard_manager_v1` | `wayland-info` | Layer-shell overlay works; `wl-copy` works without focus; **wtype cannot work on KWin** |
| `layer-shell-qt` 6.7.5 ships the QML module `org.kde.layershell` | `/usr/lib/qt6/qml/org/kde/layershell/` | The pill can be a layer surface from QML, with no C++ |
| Portals: `GlobalShortcuts` v2, `RemoteDesktop` v2, `InputCapture` v2, `Clipboard` v1 (xdg-desktop-portal 1.22.1, KDE backend) | `busctl` on `org.freedesktop.portal.Desktop` | Chord shortcuts and portal input injection are available |
| `org.kde.StatusNotifierWatcher`, `org.kde.kglobalaccel`, `org.freedesktop.secrets` (ksecretd) on the session bus | `busctl --user list` | SNI tray, KGlobalAccel and Secret Service all present |
| Andrew is already in the `input` group; `/dev/uinput` is `root:input 0660` with an ACL | `id`, `ls -l` | evdev reading and uinput writing work today with no new grant |
| **GPU is AMD** (Radeon RX 9070 XT + Radeon 610M iGPU). There is no NVIDIA GPU on the daily driver | `lspci`, no `nvidia-smi` | CUDA-only GPU paths (faster-whisper, sherpa-onnx GPU builds) don't apply; GPU means whisper.cpp on Vulkan, which is now the default engine (section 2.2). **NVIDIA is out of scope** (decided 2026-11-03): only the 9070 XT matters |
| CPU Ryzen 9 7900X (24 threads), 61 GiB RAM (about 17 GiB free while working) | `lscpu`, `free` | CPU inference is a real option |
| Qt 6.12 system packages (`pyside6` 6.12.0, `qt6-webengine` 6.12, `kirigami`, `python-evdev`, `python-secretstorage` all in the repos); system Python 3.14 | `pacman -Si` | A Python + PySide6 app packages cleanly for Arch |
| The task 10 client shipped: `capture`, `config`, `control`, `daemon`, `local`, `outbox`, `transport`, `render`, `capture_cli`. Server deps are already in the `server` extra, so `jarvis-live` core is slim (httpx, pydantic, websockets) | `server/pyproject.toml`, `client/` | The desktop app can depend on `jarvis-live` without pulling in the server stack |
| The control socket offers `status`, `stop`, `subscribe`. **No `marker`**, and local mode runs no STT, so it has no transcript | `client/daemon.py::_handle`, `client/local.py` | Phase 2 adds `marker`; "Open Local Transcripts" needs a decision (open question G) |
| The server already drops mic/system duplicates (`stt/guards.py`, echo filter) | grep | Meeting mode with speakers should not duplicate Them text; the checklist verifies it |
| Web auth is an HttpOnly session cookie from `/auth/login` (OIDC); device tokens are Bearer for REST/WS | `web/src/api.ts`, `auth.py` | A web-view panel signs in once in its own persistent profile |

## 1. Toolkit and language

**Decision: Python 3 + PySide6 (system Qt 6) with QML for every surface, in a new `linux/`
component that imports `jarvis_live.client`.**

| Option | For | Against |
|---|---|---|
| **Python + PySide6/QML** (chosen) | Imports `jarvis_live.client` directly (config, control, capture commands, tail renderer), so there is still one client. STT engine, evdev and Secret Service all have mature Python bindings. QML can use LayerShellQt's QML module, so the pill needs no C++. `QSystemTrayIcon` is an SNI item on Plasma. QtWebEngine is there for phase 3. Pure modules test with plain pytest, as the client already does | Has to use the distro's PySide6 (PyPI PySide6 bundles its own Qt and can't load the system `org.kde.layershell` plugin), so the packaged app runs on system Python 3.14. Qt and asyncio need a bridge (risk R4) |
| Qt/Kirigami in C++ | Best KDE fit: `KStatusNotifierItem`, `KGlobalAccel`, the LayerShellQt C++ API, KWallet | Can't import `jarvis_live.client`, so transport and config would be rewritten, or only reached over the socket, with the pure modules duplicated in C++. Two languages for one app and the most code |
| Tauri (Rust + WebKitGTK) | Could reuse web components and Instrument CSS as they are | Adds a fourth language. Layer-shell on a Tauri window is a hack. WebKitGTK on KDE. Still needs a bridge to the Python daemon |
| GTK4 + PyGObject | GTK 4.16+ CSS variables could read Instrument-like tokens; `gtk4-layer-shell` | No SNI tray, foreign on Plasma, and a second Python UI stack |

The UI is QML throughout (pill, settings, tray icon painting, panel chrome), so it can move to C++
later without changing the pure modules.

### 1.1 Instrument for QML (proposed upstream, not built here)

Instrument emits `web`, `swift`, `android`, `echarts` and `tokens.flat.json`; it has no Qt
target. Before phase 1's UI PR, propose to **lordmuffin/instrument**:

- a `dist/qml/Instrument/` module: a `qmldir` and a `Tokens` singleton (colour roles for light and
  dark, picked from `Qt.styleHints.colorScheme`, plus type roles, `space`, `radius` and `size`),
  generated by `scripts/build.mjs` from `tokens/instrument.tokens.json` like the other targets;
- components the app needs and Instrument doesn't have for Qt: `InstPill` (the dictation HUD),
  `InstButton`, `InstSwitch`, `InstTextField`, `InstSelect`, `InstSection`, `InstChannelBadge`
  (LAB/LIVE), `InstStatusDot`;
- a `linux` consumer entry in `consumers.json`, so the sync PR vendors it into
  `linux/src/jarvis_desktop/instrument/` (managed, never edited), as for web and macOS.

The tray icon is painted in Python (`QPainter`), with colours from the vendored
`tokens.flat.json`, never literals.

### 1.2 How `make lint-design` covers it

New `scripts/lint/check_qml_design.py` (tests in `scripts/lint/`, like the Android checker), added
to `lint-design`. In `linux/**/*.qml` and `linux/**/*.py` outside `instrument/` it fails on: hex
colours, `Qt.rgba(`/`Qt.hsla(`, named colour strings, `font.family:`, a numeric
`font.pixelSize`/`pointSize`, numeric `width|height|radius|spacing|*Margin|padding` other than 0,
`QColor(` or `QFont(` with literals. The escape is `design-lint:ignore <reason>` on the line.
Native Plasma menus (the tray's `QMenu`) follow the Breeze theme, as the Mac's `NSMenu` follows
macOS, and are not styled by us.

## 2. Phase 1: Dictation anywhere

### 2.1 Trigger under KDE Wayland

**Is a lone modifier reliable on Wayland?** Not through any compositor API: Wayland gives no
client a global key stream, and the shortcut APIs can't bind a lone right-hand modifier.
**It is reliable through evdev**, below the compositor. That is the recommendation, with a
portal chord as the automatic fallback.

| Option | Lone right modifier | Hold / tap / double-tap | Chord detection | Permission cost | Flatpak |
|---|---|---|---|---|---|
| **evdev** (`python-evdev`, read `/dev/input/event*`) — chosen | Yes (`KEY_RIGHTCTRL` etc., left and right are distinct) | Yes, all three | Yes: sees every other key and the mouse buttons | Membership of `input`: **any process of yours can read every keystroke, passwords included**. Andrew already has it, so this adds nothing on this machine, but the README must say what it means | No (needs `--device=all` and the group, which defeats the sandbox) |
| GlobalShortcuts portal (KDE backend → KGlobalAccel) | **No**: chords only | Hold only if KDE sends `Deactivated` on release (to verify, spike S1); tap works | n/a (a chord by nature) | None beyond a one-time KDE "bind this shortcut" dialog | Yes |
| KGlobalAccel over D-Bus | No | Has a released signal | n/a | None, KDE only | No |
| KWin `ModifierOnlyShortcuts` (kwinrc) | Lone modifier, but left and right are the same key, and Meta is the launcher | **No**: fires on release only | KWin's | Edits kwinrc | n/a |
| InputCapture portal | Not for hotkeys (pointer barriers / input sharing) | — | — | — | — |

**Design:**

- `trigger.py` (pure) ports `DictationTrigger` exactly: keys `off | right_ctrl | right_alt |
  right_shift | right_meta`; modes `hold | tap | double_tap`; `HOLD_THRESHOLD = 0.2`,
  `TAP_MAX = 0.35`, `DOUBLE_TAP_GAP = 0.4`, tolerance 0.01; any other key, modifier or mouse
  button makes the press a chord, which never starts and cancels a hold that has begun; Esc
  cancels; the controller decides start versus stop for `toggle`/`tap`/`double_tap`
  (`tap_may_start` as on the Mac). The Swift tests are the spec: every case in
  `DictationTriggerTests` gets a pytest twin.
- `keys/evdev_monitor.py` (I/O) opens every device with `EV_KEY` capability (keyboards and mice),
  follows hot-plug through a `/dev/input` inotify watch, ignores autorepeat (`value == 2`), and
  feeds the trigger with `CLOCK_MONOTONIC` event timestamps. The hold timer is an asyncio
  `call_later`. It only observes and never grabs, so every key still reaches its app, as with the
  Mac's monitors. Default key: **Right Ctrl** (Right Alt is AltGr on many layouts, and Right Meta
  is often absent).
- Touchpad tap-to-click happens in libinput, inside the compositor, so it emits no `BTN_LEFT`
  at evdev and can't cancel a hold. Physical clicks do. Documented and accepted.
- **Fallback:** if no readable keyboard device exists (not in `input`, or in a sandbox), the app
  binds a chord through the GlobalShortcuts portal (default proposal `Meta+Space`: open question
  B), says in Settings and the pill that a lone key needs evdev, and links to the fix.

### 2.2 On-device STT: measured

One 10.9 s English utterance (piper TTS, 16 kHz mono), on this machine while it was working
(load average 3.6), 8 threads, model kept resident. Median of 3 runs after a warm-up.
whisper.cpp figures are `total time - load time` from `whisper_print_timings` (commit `4da27c4`).

| Engine | Model | Device | Decode 10.9 s | RTF |
|---|---|---|---|---|
| **sherpa-onnx 2.0.1** | **parakeet-tdt-0.6b-v2 int8** | **CPU** | **311 ms** | **0.029** |
| sherpa-onnx 2.0.1 | whisper-base.en int8 | CPU | 730 ms | 0.067 |
| sherpa-onnx 2.0.1 | whisper-small.en int8 | CPU | 2474 ms | 0.227 |
| faster-whisper (CTranslate2) | base.en int8 | CPU | 263 ms | 0.024 |
| faster-whisper | small.en int8 | CPU | 736 ms | 0.068 |
| faster-whisper | distil-large-v3 int8 | CPU | 2529 ms | 0.232 |
| faster-whisper | large-v3-turbo int8 | CPU | 3064 ms | 0.281 |
| whisper.cpp | base.en | CPU | 514 ms | 0.047 |
| whisper.cpp | small.en | CPU | 1594 ms | 0.146 |
| whisper.cpp | large-v3-turbo q5_0 | CPU | 5859 ms | 0.538 |
| whisper.cpp | base.en | Vulkan, RX 9070 XT | 142 ms | 0.013 |
| whisper.cpp | small.en | Vulkan, RX 9070 XT | 221 ms | 0.020 |
| whisper.cpp | large-v3-turbo q5_0 | Vulkan, RX 9070 XT | 234 ms | 0.021 |

All of them transcribed this clean utterance correctly, so a single TTS clip can't rank them.
NVIDIA is out of scope (decided); the only GPU that matters is the RX 9070 XT.

**Accuracy, measured 2026-11-03** (PRs #297 and #298; WER computed by `jarvis-voice eval score`):

| Set | Parakeet-TDT-0.6B-v2 int8, CPU | whisper.cpp large-v3-turbo q5_0, Vulkan |
|---|---|---|
| **Andrew reading 40 known sentences** (MacBook Pro built-in mic, 2 min) | 10.5 % (6.9 % ignoring number and time formatting) | **7.2 %** (**4.8 %**) |
| 400 TTS clips, 5 voices, clean and 10 dB noise (exact text) | 4.7 % (4.5 % noisy) | **4.0 %** (4.1 % noisy) |
| 200 real clips from live (the server's transcript as reference) | 24.7 % disagreement | **19.9 %** |

The live clips' references are often cut short mid-word, so that row only ranks the engines. On
the read set Parakeet's mistakes were also the worse kind ("look at the cooling" became "Kool
Aid", "standup" became "stain up", "GPU" became "G P U", numbers spelled out), while Whisper's were
small and it writes digits.

**Decision (revised 2026-11-03): whisper.cpp large-v3-turbo q5_0 on Vulkan, with Parakeet on CPU
as the automatic fallback.**
- **Accuracy:** best on all three sets, most clearly on Andrew's own voice.
- **Latency:** `whisper-cli` loads the model on every call (about 200 ms), and a 10 s utterance
  still takes about 430 ms end to end (306 ms p50 on the 2 to 5 s read clips), inside the 1 s
  release budget. A resident model (whisper.cpp's library in-process) would cut about 200 ms; it
  is a later optimisation, not needed for the budget.
- **Fallback:** `FallbackEngine` uses Parakeet (311 ms for 10.9 s on CPU) whenever Whisper fails:
  a lost GPU, the RADV driver (which calls itself "not a conformant Vulkan implementation"), a
  missing `whisper-cli`. The GPU then rests for 5 minutes before it is tried again, and the pill
  says the fallback is in use.
- **Rejected:** faster-whisper (CUDA only for the GPU, too slow on CPU); Parakeet as the default
  (less accurate on Andrew's voice, spelled-out numbers).
- **Boundary:** `WhisperCppEngine` runs `whisper-cli` on a private temporary WAV (a 0700 directory
  under `$XDG_RUNTIME_DIR`, a 0600 file, deleted after each call). It never opens a socket; the
  `whisper-server` HTTP listener is deliberately not used.

**Streaming and latency.** Both engines are offline models. The pill's live words come from
Parakeet on CPU, which re-decodes the open segment about every 0.7 s (cheap, and it keeps the GPU
idle while you talk). The text that is pasted comes from Whisper. The sherpa-onnx Silero VAD closes
a segment at each pause of 0.6 s or more and Whisper finalises it in the background, so at release
only the last open segment (usually under 5 s, about 300 ms) is left to decode, however long the
dictation. PR 3b decides this split with a test on the read set. The mic opens at
`begin` and a pre-roll keeps the first word while the engine warms up, as on the Mac.

Budget from release to text in the field, 10 s utterance: last segment ≤ 450 ms + join/cleanup
< 1 ms + `wl-copy` about 15 ms + waiting for the user's modifiers to come up (0 to 200 ms) +
paste chord 4 × 10 ms ≈ **0.5 to 0.7 s**. Each stage is timed and logged locally, numbers only
(section 2.6).

**Model delivery.** About 1.06 GB: the Whisper turbo q5_0 GGML file (574 MB, from
`huggingface.co/ggerganov/whisper.cpp` pinned to revision `5359861c`), Parakeet (482 MB archive
from the k2-fsa release) and Silero VAD (0.6 MB), each with a pinned SHA-256, installed by
`jarvis-voice models install` (progress in the pill on first run) into
`$XDG_DATA_HOME/jarvis-voice/models/`. The downloads are the only network access dictation makes
and carry no user content. `whisper-cli` itself comes from a whisper.cpp build with Vulkan
(packaging, section 6).

### 2.3 Text into the focused app

| Option | Works on KWin 6.7 | Permission | Sandbox | Verdict |
|---|---|---|---|---|
| **uinput in-process** (`python-evdev` `UInput`, a virtual keyboard named `jarvis-voice-paste`) | Yes: a kernel device, so KWin treats it as a real keyboard for every app | Write access to `/dev/uinput`: **can inject any key into any app**. Present here via `input` plus the ACL; elsewhere the package ships a udev rule (`KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"`) | No | **Primary** |
| RemoteDesktop portal (`NotifyKeyboardKeysym`) | Supported by the KDE backend | A consent dialog; KWin shows a "remote control" indicator while the session is open; whether KDE honours `persist_mode` restore tokens is unknown (spike S2) | Yes | **Fallback** for Flatpak or no uinput, if S2 shows it doesn't ask on every paste |
| wtype | **No**: KWin doesn't advertise `zwp_virtual_keyboard_manager_v1` (verified) | — | — | Rejected |
| ydotool | Yes (the same uinput) | Same, plus a `ydotoold` daemon running as root or a user service | No | Rejected: no gain over doing uinput in-process |
| KWin `org_kde_kwin_fake_input` | Yes | KWin-private, granted to an executable matched by a desktop file (`X-KDE-Wayland-Interfaces`) | No | Rejected: fragile, and KDE itself moved to the portal |
| Typing the characters | — | — | — | Rejected: layout-dependent keycodes, no Unicode, and about 2 s for a 200-character utterance |

**Paste chord.** Ctrl+V doesn't paste in Konsole (it wants Ctrl+Shift+V), and on a non-QWERTY
layout `KEY_V` is not V. So the default chord is **Shift+Insert**, with the text put on both
CLIPBOARD and PRIMARY (`wl-copy` and `wl-copy --primary`). Qt, GTK, Firefox and Chromium/Electron
paste CLIPBOARD on Shift+Insert; Konsole and most terminals paste PRIMARY on it. Insert is not a
letter, so the layout doesn't matter. Settings offers Ctrl+V and Ctrl+Shift+V as alternatives.
**The dictated text stays on the clipboard by default** (decided 2026-11-03), as on the Mac: the
old contents are never restored, so a failed or misplaced paste can always be redone with Ctrl+V.
Side effect: PRIMARY (the middle-click selection) is overwritten too. A test pins both: after a
dictation, CLIPBOARD and PRIMARY hold the text.

**Clipboard.** `wl-copy`, which uses `ext_data_control` and so needs no focus. `QClipboard` can't
be used: on Wayland it needs a focused surface, and the pill never takes focus.

**Order and failure messages, never silent.** Copy first (if `wl-copy` fails, the pill says so
and shows the text). Wait until the monitor sees every modifier up (at most 500 ms, so the user's
Right Ctrl can't turn Shift+Insert into another shortcut). Then the chord as real key events (down
Shift, down Insert, up Insert, up Shift, 10 ms apart). If uinput isn't writable: the portal
fallback; if that is unavailable or refused: "Copied. Press Ctrl+V to paste", and Settings
explains how to enable pasting. Paste turned off in Settings: "Copied".

**Our own events never retrigger.** (1) The monitor skips every event from a device named
`jarvis-voice-paste`, the analogue of the Mac's `eventTag`. (2) While a chord is being sent, the
monitor ignores the trigger key on every device. (3) A test asserts that no paste chord contains
any trigger key. Our chord uses left Shift only, so even an unfiltered event would be "other
input", which can't start dictation.

### 2.4 The pill

A QML `Window` with `LayerShell.Window.layer: LayerOverlay`, anchored bottom-centre,
`keyboardInteractivity: KeyboardInteractivityNone` (it can never take focus), exclusive zone 0
(it reserves no space), and `Qt.WindowTransparentForInput` (clicks go through). KWin puts it on
the active output. Content comes from `presentation.hud(state, live_text, message,
asset_progress)`, a port of `HUDContent.make`: Listening, Transcribing, the last words
(`DictationText.tail`, 80 characters), Copied, Didn't catch that, error messages, and the 2.5 s
dismiss. It uses `InstPill` from Instrument's QML target.
**Confirmed:** the protocol and the QML module are present on this KWin. **To confirm in PR 1.5:**
that showing it doesn't move focus in the four checklist apps. Fallback if the layer-shell module
fails to load: a desktop notification for messages only.

### 2.5 Local-only by construction

As on the macOS `DictationController`: dictation has its own mic process
(`pw-record`/`parec` from `jarvis_live.client.capture.build_command`, 16 kHz s16), its own engine,
and writes nothing to the outbox, the session state dir or transcript files.

- **Import guard test:** `jarvis_desktop.dictation` and everything it imports must not reach
  `httpx`, `websockets`, `jarvis_live.client.transport` or `jarvis_live.client.daemon` (checked
  through `sys.modules` after a fresh-interpreter import, the same idea as the client's
  transport-only-network test).
- **Egress test:** a fixture makes any non-`AF_UNIX` `socket.connect`/`getaddrinfo` fail the test,
  then runs a full dictation (fake mic, real controller, fake engine) and asserts that no file
  appears under `$XDG_STATE_HOME/jarvis-live/`. A mirror test shows that the fixture does bite.
- **Egress lint:** `scripts/lint/check_egress.py` gains Python rules for `linux/` (`httpx`,
  `websockets`, `urllib.request`, `socket.create_connection`, `QNetworkAccessManager`,
  `QWebEngine*`), allowed only in the files that go through the gate (`net.py`, `panel/`).

**Cleanup.** `filler.py` (pure, opt-in, default off): removes standalone "um", "uh", "erm",
"hmm" and immediate stutter repeats ("I I think" becomes "I think"), and fixes the spacing and
capital letter around what it removed. Table-driven tests, including words that must survive
("umbrella", "uh-huh" kept, "hmm" inside quotes). **Server polish** (an LLM rewrite) would need a
new REST endpoint, which is a `protocol/` change with fixtures and Swift tests. It is not part of
phase 1: open question E.

### 2.6 Controller and the done test

`controller.py` ports `DictationController`'s state machine (`idle`, `listening`, `finishing`;
generation counter; 300 s cap; 15 s finish timeout; double-tap `tap_may_start`) with injected
ports: `Mic`, `Engine`, `Clipboard`, `Paster`, `Clock`, `Hud`. That makes it unit-testable with
fakes and no Qt.

Done-test instrumentation: per dictation the app appends `{t, audio_ms, release_to_paste_ms,
outcome}` to `$XDG_STATE_HOME/jarvis-voice/dictation.jsonl` (no text, ever). `jarvis-voice stats`
prints count per day, p50/p95 latency and outcomes; crashes and restarts show in
`journalctl --user -u jarvis-voice`. That makes "within about 1 s, 20+ a day for a week, no crash
or restart" checkable rather than remembered.

**Process.** `jarvis-voice` is one Qt process (tray, dictation, pill) run as a systemd user
service (`Restart=on-failure`, `WantedBy=graphical-session.target`). Starting it from XDG
autostart is the alternative.

## 3. Phase 2: Tray app

It drives the existing capture daemon through `client/control.py` and has no pipeline of its own.

- **SNI item** with `QSystemTrayIcon` (Qt registers it with `org.kde.StatusNotifierWatcher`). SNI
  shows an icon, tooltip and menu but no text, so the Mac's capsule with elapsed time becomes icon
  variants plus a tooltip ("Recording 12:04 · Meeting · STREAMING"). `presentation.tray_icon(...)`
  (a port of `MenuBarIconSpec.make`) picks the glyph, the **status dot** (connected / reconnecting
  / offline-local, from `status.connection`), the **LAB/LIVE badge** (from `GET /healthz`
  `environment`, as the Mac's `ServerChannel`), and the **recording indicator** whenever the mic is
  open, for a session *or* a dictation.
- **Menu** (`presentation.menu(...)`, a port of `MenuText`): Start/Stop Session; Mode (Meeting =
  mic plus the default sink's monitor, which is `--meeting --system`; Solo = mic); Local only
  (applies to the next start and is disabled while recording); Add Marker; Open Local Transcripts;
  Copilot panel (phase 3); Resume Session (section 3.1); Settings; Quit. **Quit stops and ends a
  running session** (decided 2026-11-03), the same `stop` as the menu item, waiting at most 30 s;
  if the server can't be reached the outbox stays resumable and the pill says so before exit.
  The session still has to be resumable afterwards (section 3.1).
- **Start** calls a small public function extracted from `capture_cli._start` (spawn the daemon
  detached, wait until connected), so the CLI and tray share it. **Stop** sends `stop`. Status is
  polled every second; events arrive through `subscribe`.
- **Daemon changes (in `server/`, scope `server`):** a `marker {label}` control command (streaming
  sends `marker` through `ProducerConnection.send_marker`; local appends to the session's
  `markers.jsonl`). Status gains nothing new: `connection`, `mode` and `session` already cover the
  tray.
- **Network gate:** the tray's only network call of its own is `/healthz`. It goes through
  `net.py` and is skipped while a local-only session records. The egress test covers a local-only
  session started from the tray.
- **Global shortcuts** `Ctrl+Alt+J` (toggle session) and `Ctrl+Alt+M` (marker) through the
  GlobalShortcuts portal: KDE asks once and the user can rebind them in System Settings. A host
  (non-Flatpak) app first registers its app id `dev.apj.JarvisVoice` with
  `org.freedesktop.host.portal.Registry` (xdg-desktop-portal 1.22 has it). evdev is not used for
  these, because it can't stop the focused app from also getting the keys.
- **Device token in the Secret Service** (`secretstorage`, attributes `{application: jarvis-live,
  server: <url>}`, which is KWallet through ksecretd here). `client/config.py` precedence becomes
  flag, env, Secret Service, then `client.toml`. The lookup is an optional import and is skipped
  silently when there is no session bus, so headless boxes keep using `client.toml` unchanged. The
  Settings window writes to the Secret Service, never to the toml.
- **Done:** start a Meeting session from the tray and see it live in the web dashboard (the tray
  item "Open in dashboard" uses `status.dashboard_url`).

### 3.1 Resuming sessions any time

Requirement (2026-11-03): Quit and Stop end the session, and any session can be resumed later.

What exists: an **unfinished** session (crash, power loss, Wi-Fi drop, `stop` that couldn't reach
the server) is resumable now: `capture start --resume` reopens the outbox and replays from the
server's acks. The tray offers that at launch ("Resume unfinished session …").

What doesn't: an **ended** session can't take more audio. The server rejects a producer with
`session_not_live` once the status leaves `live` (`api/stream.py::_producer`), and it also ends
a live session by itself after `idle_end_s` (600 s) plus the producer grace period. So "resume
any time" after an end needs a server change. Options (open question J):

1. **Reopen in place (recommended).** New REST call `POST /v1/sessions/{id}/resume` (owner only,
   `done`/`failed` → `live`; refused while finalizing or deleted). The client resumes through the
   normal `hello` resume path with its next seqs, `t_ms` continues after a gap so the timeline
   shows the break, and the next `end` re-finalizes and **replaces** the final note (the server
   already replaces notes on regeneration). The tray's Resume Session lists the last 10 sessions
   from `GET /v1/sessions`. This is a `protocol/` REST addition: schema, fixtures, server and
   Swift tests in the same PR, Swift clients free to ignore it. Local-only sessions resume purely
   on disk (new frames appended to the same outbox), with no server call.
2. **Continue as a linked session.** `session_create_request` gains `continues: uuid`; the
   dashboard shows the chain. Simpler on the server (no reopening), but two notes per meeting.
   Also a protocol change.
3. **No end on Quit.** Quit stops capture without `end`, so the session stays resumable for about
   10 minutes; after that the server ends it. No protocol change, but not "any time".

If 1 is taken, it is two PRs at the start of phase 2: `feat(protocol): resume an ended session`
(schema, fixtures, server, Swift conformance tests) and `feat(server): capture resume of ended
sessions` (client `start --resume ID`, tray menu).

## 4. Phase 3: Copilot panel

**Decision: a small Qt window around the web dashboard's live session view, not a native panel.**

| | Native QML panel | **Web view (QtWebEngine)** |
|---|---|---|
| Code | Re-implements the feed reducer, draft-to-final swap, markers, speakers, Now dock, suggestion ageing, notes/actions/decisions/Related, auto-scroll and unseen marks: the Mac needs about 600 lines of Swift for this, plus `PresentationLogic` | One window class, plus a compact layout in `web/` |
| Matches the Mac | Only as far as it is kept in step | The web view already has `NowDock`, `CopilotPanel`, `Timeline`, `FeedFilter` and `JumpPill`, built on Instrument CSS and tested with vitest and Playwright |
| Local-only sessions | Could show on-device drafts | **Shows nothing**: the server has never heard of the session. The panel says "Local only: nothing leaves this machine" |

- `panel/panel.py`: `QWebEngineView` with a persistent profile in
  `$XDG_DATA_HOME/jarvis-voice/web` (sign in once with the normal web login), loading
  `<server>/sessions/<id>?view=panel`. Links: `obsidian://` and any other origin are intercepted
  (`acceptNavigationRequest`) and opened with `QDesktopServices.openUrl`. The always-on-top toggle
  tries `Qt.WindowStaysOnTopHint` first; if KWin ignores it on Wayland (spike S3), it falls back
  to a KWin script loaded over D-Bus (`org.kde.KWin /Scripting`) that sets `keepAbove` on our
  window.
- **web change** (scope `web`, so it releases with `server`): `?view=panel` gives the live
  session view the compact panel layout, about 320 to 480 px wide like the Mac's narrow layouts
  (no shell, no side nav, feed with the Now dock at the bottom, kinds folded), auto-scroll to the
  newest line, and "On top" as a `postMessage` that a `QWebChannel` shim turns into the window
  flag. Tests come first (vitest for the layout choice, Playwright for the panel route).
- Draft lines appear only if the client sends `draft_segment`s. Today the Linux client doesn't,
  so the panel shows finals (open question G).

## 5. Code layout

**New `linux/` component; small additions to `server/src/jarvis_live/client/`.**

Why not extend `jarvis_live.client`: the server image and the headless install must stay GUI-free
(no PySide6, sherpa-onnx or 640 MB model); the desktop app has its own release and AUR package;
CLAUDE.md puts each platform in its own top-level component (`macos/`, `android/`); and keeping
dictation in another package makes the "dictation never reaches the server" boundary structural.

```
linux/
  pyproject.toml            # jarvis-desktop; deps: jarvis-live (path ../server in dev), sherpa-onnx, evdev,
                            #   secretstorage; PySide6 is a dev/CI dep only (the package uses the distro's)
  README.md  TESTING.md
  src/jarvis_desktop/
    trigger.py  dictation_text.py  filler.py  presentation.py      # pure, tested like the Mac's
    controller.py                                                  # dictation state machine, injected ports
    keys/evdev_monitor.py
    insert/clipboard.py  insert/uinput.py  insert/portal.py
    audio/mic.py
    stt/engine.py  stt/sherpa.py  stt/models.py
    ui/Pill.qml  ui/Settings.qml  ui/hud.py  ui/settings.py
    tray/tray.py  tray/session.py  tray/shortcuts.py  tray/secrets.py
    panel/panel.py
    net.py                  # the only place allowed to touch the network (healthz, model download)
    stats.py  app.py        # entry point `jarvis-voice`
    instrument/             # vendored by the Instrument sync (managed)
  tests/                    # pytest; pytest-qt for QML smoke tests under QT_QPA_PLATFORM=offscreen
  packaging/
    aur/PKGBUILD  aur/.SRCINFO
    jarvis-voice.desktop  jarvis-voice.service  60-jarvis-voice-uinput.rules
```

Root `Makefile`: `test-linux` (`cd linux && uv run ruff check . && uv run ruff format --check . &&
uv run mypy src && uv run pytest -q`) joins `make test`; `lint-design` gains
`check_qml_design.py`. CI: a `linux.yml` workflow on ubuntu (pytest with PyPI PySide6, offscreen)
plus `makepkg --printsrcinfo` and `namcap` in an `archlinux` container.

## 6. Release and packaging

- **release-please:** a new package `"linux": { "release-type": "python", "component": "linux" }`
  tagged `linux-vX.Y.Z`, starting at `0.1.0`, with `packaging/aur/PKGBUILD` as a `generic`
  extra-file for `pkgver`. It is not linked to `server`: the app only needs the published client
  API, and `pyproject.toml` declares `jarvis-live>=<the version that adds marker/secret service>`.
- **AUR first:** `jarvis-voice-desktop`, built from the `linux-vX.Y.Z` tag. The same checkout
  builds `server/` as the `python-jarvis-live` client (core deps only) and `linux/`. Depends on
  `pyside6`, `qt6-declarative`, `layer-shell-qt`, `qt6-webengine` (phase 3), `python-evdev`,
  `python-secretstorage`, `python-httpx`, `python-pydantic`, `python-websockets`, `wl-clipboard`,
  `pipewire` (or `libpulse` for `parec`), and **whisper.cpp built with Vulkan** for `whisper-cli`
  (an AUR package such as `whisper.cpp-vulkan` if one is current, otherwise built in the PKGBUILD
  with `-DGGML_VULKAN=1`, needing `vulkan-headers` and `shaderc`; the PKGBUILD PR settles which).
  sherpa-onnx (the fallback): the `python-sherpa-onnx` AUR package if it is current, otherwise a
  pinned wheel vendored into `/usr/lib/jarvis-voice/` (open question C).
  Installs the udev rule and the user unit, and prints the `input` group note.
  A `linux-v*` tag triggers a workflow that renders `.SRCINFO` and attaches the PKGBUILD to the
  GitHub release; pushing to the AUR stays manual at first.
- **Flatpak (optional, later):** global shortcuts and RemoteDesktop work through portals, but
  **evdev and uinput don't work in the sandbox**, so the Flatpak would be chord-trigger plus portal
  paste only, which is not the WisprFlow experience. **AppImage:** no sandbox, so evdev and uinput
  work. But it needs a bundled Qt matching layer-shell-qt (or none) and the udev rule installed by
  hand. Neither is in this plan's scope beyond this note.

## 7. TDD order and work breakdown (one PR each)

Every step is red then green per `TDD.md`; `make test` before done.

**Prerequisites**
- P0 (lordmuffin/instrument) `feat: qml target and Qt components` (section 1.1). Blocks the UI PRs
  only; 1.1 to 1.4 can go first.
- P1 `chore(linux): scaffold linux component` — `pyproject.toml`, `make test-linux`, CI workflow,
  release-please package, one trivial test (TDD.md phase 1 harness).
- P2 `chore(lint): qml design lint and python egress rules` — `check_qml_design.py` with its
  tests, `check_egress.py` Python rules with tests.

**Phase 1**
1. `feat(linux): dictation trigger` — `trigger.py`; RED: pytest twins of every
   `DictationTriggerTests` case, plus autorepeat.
2. `feat(linux): dictation text and filler cleanup` — `dictation_text.py` (twins of
   `DictationTextTests`), `filler.py` table tests.
3. `feat(linux): on-device STT engine` — done in #297, #298 and the Whisper switch: model
   install with pinned SHA-256, `Engine`, `ParakeetEngine`, `WhisperCppEngine`, `FallbackEngine`,
   `eval pull|record|score`, and the measurements that chose Whisper turbo (section 2.2).
   3b: VAD segmentation and the Parakeet-partials / Whisper-final split.
4. `feat(linux): dictation controller` — the state machine with fakes: hold/tap/double-tap flows,
   cancel, 300 s cap, finish timeout, empty result is "Didn't catch that", **import guard and
   egress tests**, stats log has no text.
5. `feat(linux): evdev trigger and uinput paste` — monitor over fake `InputEvent` streams
   (chords, mouse, hot-plug, own device skipped), chord builder (no trigger key in any chord,
   modifier wait), clipboard via a fake `wl-copy` on `PATH`, fallback ordering and messages.
6. `feat(linux): dictation pill` — `presentation.hud` (twins of the `HUDContent` tests), QML smoke
   test offscreen, layer-shell properties set when the module is present.
7. `feat(linux): jarvis-voice app, settings and user service` — settings model (key, mode, paste
   chord, cleanup), `stats`, unit file verified with `systemd-analyze --user verify`.
8. `feat(linux): RemoteDesktop portal paste fallback` — only if spike S2 says it's usable.
9. `feat(linux): AUR package` — PKGBUILD, `.SRCINFO`, namcap in CI, udev rule.
   → **Phase 1 manual checklist; the week-long done test starts here.**

**Phase 2**
10. `feat(server): capture marker command and secret-service token` — `marker` control command
    (stream and local), config precedence with a fake Secret Service (`jeepney` test bus or an
    injected lookup), headless path unchanged.
11. `feat(linux): tray` — `presentation.tray_icon` and `presentation.menu` twins, session
    start/stop/marker against the real daemon with the fake recorder (the client's existing test
    fixtures), healthz gate test in local-only.
12. `feat(linux): global shortcuts and secret service settings` — portal client with a fake D-Bus
    portal, Settings writes the token to the Secret Service.
12a. `feat(protocol): resume an ended session` — `POST /v1/sessions/{id}/resume` schema and
    fixtures, server endpoint, Swift conformance tests (J).
12b. `feat(server): capture resume of ended sessions` — `start --resume ID`, tray Resume Session.
12c. `feat(linux): local transcripts` — parakeet over the outbox audio, local Markdown (G1).
12d. `feat(server): draft control command` and `feat(linux): send drafts` — `draft_segment`s in
    streaming mode (G2).
   → **Phase 2 done test:** Meeting session from the tray, live in the dashboard.

**Hold to ask** (`docs/plans/voice-push-to-talk.md`, Linux): **built, not yet reachable.**
- `feat(server): the headless client can hold to ask` — `ask_begin` / `ask_end` / `ask_played`
  control commands, `voice_state` and `ask_reply` on `subscribe`, `status` says `system`.
- `feat(linux): hold to ask` and `feat(linux): speak ask replies through headphones only` —
  `AskTrigger` and `Keys` in `trigger.py`, `ask.py`, `route.py`, `speech.py` (Piper, the `speech`
  extra), the pill's ask wording in `presentation.py`, `ask_enabled` / `ask_key` in `settings.py`.
- Reaches a key with PRs 5 to 7 (the monitor feeds `Settings.keys()`, the pill shows `ask_pill`).
  Hardware rows: `linux/TESTING.md`.

**Phase 3**
13. `feat(web): compact panel view` — vitest + Playwright for `?view=panel`.
14. `feat(linux): copilot panel window` — navigation interception (`obsidian://` goes outside),
    the on-top bridge, local-only placeholder.

## 8. `linux/TESTING.md` manual checklist (what CI can't run)

`linux/TESTING.md` exists with hold to ask's rows; dictation's rows below join it with PR 9.

Same shape as `macos/JarvisVoiceMac/TESTING.md` (PASS / FAIL / NOT RUN, build and date at the top).

- **Trigger and paste** in Konsole, Firefox, an Electron app (VS Code or Slack) and a Flatpak app
  (e.g. Flathub Firefox or Telegram), each in hold, tap and double-tap: the text lands at the
  cursor; Ctrl+C / Ctrl+click / Right-Ctrl+arrow never start dictation; Esc cancels; the pill never
  takes focus (typing continues in the app while it shows); pasting never starts a second dictation.
- **Fallbacks:** remove `/dev/uinput` access (`setfacl -b`), and "Copied. Press Ctrl+V" appears;
  log in without `input`, and the chord trigger is offered.
- **Latency:** `jarvis-voice stats` p95 release-to-paste under 1 s over 20 dictations of about 10 s.
- **Meeting mode** on a real call, once with headphones and once with speakers: no duplicated
  Them text in the dashboard.
- **Wi-Fi off for 60 s** mid-session: the tray dot goes to reconnecting, then back; the dashboard
  has no gap (outbox resume).
- **Local-only session** from the tray: the server logs show no request from this device for the
  session's duration (and no `/healthz`).
- **60 minutes** of session plus periodic dictation with steady RSS (`ps -o rss` every 5 min,
  growth under 10 %).
- **Shortcuts** Ctrl+Alt+J / Ctrl+Alt+M from another focused app.
- **Week-long:** 20+ dictations a day for 7 days, with `stats` and `journalctl` showing no crash
  or restart.

## 9. Protocol

No change, except possibly section 3.1 (resume an ended session, question J). Dictation is on-device and never touches the protocol. The tray reuses `POST
/v1/sessions`, the producer WebSocket and `marker` (already in v1) through the existing client.
The panel is the web dashboard. The fixtures keep passing through the client's existing
conformance tests. Server polish (open question E) would be the first thing to need a change, and
it is out of scope.

## 10. Spikes (inside the PRs named, before their code)

- S1 (PR 5): does KDE's GlobalShortcuts portal emit `Deactivated` on release? This decides hold
  mode in the chord fallback.
- S2 (PR 8): does KDE's RemoteDesktop portal accept `persist_mode=2` and restore without a dialog,
  and does the "remote control" indicator stay up? If it asks on every paste, PR 8 is dropped and
  the fallback is copy-only.
- S3 (PR 14): does `Qt.WindowStaysOnTopHint` keep a window above on KWin 6.7 Wayland?

## 11. Risks

- **R1 Keylogger-grade permissions.** evdev and uinput can read and inject everything. This is
  documented, the app opens only `EV_KEY` devices, logs no key data and no text, and Settings shows
  which mechanism is in use. It is already true of this account through the `input` group.
- **R2 System PySide6 coupling.** The layer-shell QML plugin must match the Qt that PySide6 is
  built against, so the packaged app runs on Arch's Python 3.14 and Qt 6.12. A Qt update rebuilds
  both at once in Arch, but a partial upgrade breaks the pill (it falls back to notifications).
  Dev and CI use PyPI PySide6, where the pill is a plain window.
- **R3 Paste chord coverage.** Shift+Insert plus PRIMARY is the best single chord found. An app
  that binds Shift+Insert to something else would need the per-app Ctrl+V setting. The checklist
  covers the four app types.
- **R4 Qt and asyncio.** The control client and the hold timer are asyncio, the UI is Qt. Plan:
  PySide6's `QtAsyncio` event loop. If it proves flaky, asyncio runs in one worker thread and
  talks to Qt through signals. Decided in PR 7, with a test that a hold timer fires while QML
  renders.
- **R5 Accuracy on a real mic.** Measured (section 2.2): it changed the default engine to
  Whisper turbo. The read set is 40 sentences on a MacBook mic; re-run `eval record` on the
  desktop's own mic once dictation works there.
- **R6 Memory over a week.** Parakeet stays resident for the pill (about 1 GB RSS) plus Qt and
  WebEngine; Whisper runs as a short-lived process per segment, so its VRAM (about 1 GB) is held
  only while it decodes. The 60-minute RSS test and the week's journal cover it.
- **R7 Disk.** `/home` is 94 % full (103 GB free). The 1.06 GB of models is fine, but a local-only
  outbox (1.92 MB/min/channel) is capped by the existing `max_outbox_mb`.
- **R8 Phase 3 shows nothing for local-only sessions**, unlike the Mac panel, unless G is taken up.
- **R9 GPU dependence.** The default engine needs the RX 9070 XT through RADV, shared with games
  and local LLMs. Mitigated by `FallbackEngine` (Parakeet on CPU, a pill notice, a 5-minute rest
  before retrying the GPU); the week-long done test logs which engine served each dictation.
- **R10 The mic that disappears.** Found 2026-11-03: a USB hub dropped the Revelator mic, the
  default fell back to a Bluetooth speakerphone, and this machine's Bluetooth adapter delivers
  pure zeros over HFP ("adapter firmware bug?" in WirePlumber). Dictation must name the mic it
  uses and treat an all-zero take as a dead mic, not as silence ("Mic sent no signal: check
  <name>"), never pasting nothing silently. `eval record` already does (#298); the controller
  PR adds the same check, and `TESTING.md` gets "unplug the mic mid-dictation".

## 12. Open questions (need your call before I code)

**All answered 2026-11-03; the plan is approved.** Decisions:

- **A** NVIDIA out of scope, only the RX 9070 XT.
- **B** Fallback chord `Meta+Space`.
- **C** First-run model download with a pinned SHA-256; sherpa-onnx from the AUR if current,
  otherwise a pinned vendored wheel.
- **D** The dictated text stays on the clipboard by default.
- **E** Server polish deferred out of this plan.
- **F** Quit stops and ends the session; sessions stay resumable any time (J).
- **G** Taken: local transcripts and drafts become two PRs at the end of phase 2 (G1 the app runs
  parakeet over the session's outbox audio and writes local Markdown; G2 a `draft` control
  command sends `draft_segment`s in streaming mode). Matching the Mac's panel and "Open Local
  Transcripts" is in the brief, so this is the recommended reading.
- **H** One web login in the panel's own profile.
- **I** Right Ctrl.
- **J** Option 1: reopen in place with `POST /v1/sessions/{id}/resume` (section 3.1).

The questions below are kept for the record.

B. **Fallback chord** when evdev isn't available: `Meta+Space` (often free on Plasma), or another?
C. **Model delivery:** first-run download with a pinned checksum (recommended), or a separate AUR
   model package? And sherpa-onnx from the AUR or a vendored wheel?
E. **Server polish:** defer it out of this plan (recommended), or plan a `POST /v1/dictation/polish`
   protocol change (fixtures, server and Swift tests) as a phase 1.5, opt-in per dictation?
G. **Local transcripts and drafts.** The Linux daemon runs no STT, so "Open Local Transcripts"
   would only open audio, local-only sessions have no text, and the panel gets no draft lines. Do
   you want a phase 2 PR where the desktop app runs the same parakeet engine over the session's
   outbox audio, writes local Markdown transcripts and, in streaming mode, sends `draft_segment`s
   through a new `draft` control command? It is the biggest remaining gap with the Mac, at about
   two PRs. Otherwise the menu item opens the session folder and says it holds audio only.
H. **Panel sign-in:** is one web login in the panel's own profile fine, or should a device be able
   to open a viewer view with its device token (a server change)?
I. **Default trigger key:** Right Ctrl (recommended), or another right-hand modifier?
J. **Resuming an ended session** (section 3.1): reopen in place with `POST /v1/sessions/{id}/resume`
   (recommended; one meeting, one note, replaced on the next end), continue as a linked new
   session, or settle for the 10-minute window? Options 1 and 2 are protocol changes.
