# Jarvis Voice for the Linux desktop

`jarvis-voice`: dictation anywhere, a tray app and a Copilot panel for KDE Plasma 6 on Wayland.
It drives the headless capture client in [`server/src/jarvis_live/client/`](../server/src/jarvis_live/client/)
and is not a second client. The plan, with the decisions and the STT benchmark, is
[`docs/plans/linux-desktop.md`](../docs/plans/linux-desktop.md).

Status: scaffold. The speech models, the eval tools and the parts of hold to ask that need no
UI are built; the key monitor, the pill and the app's main loop are not, so nothing listens for a
key yet (plan section 7, PRs 4 to 7).

## Develop

```sh
cd linux
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

`make test` at the repo root runs the same checks (`lint-linux`, `test-linux`).

## STT evaluation data

`jarvis-voice eval pull` downloads your own mic utterances from a Jarvis Live server, with the
server's final transcript of each as the reference text, to score on-device engines on your real
voice (plan section 2.2):

```sh
# Create a device token on the dashboard's Devices page and save it to a file (mode 0600).
uv run jarvis-voice eval pull --server https://live.apj.dev --token-file ~/.config/jarvis-live/token
```

- Takes finished **solo** sessions you own (`--mode meeting|any` for more), mic channel, speaker
  "me", 2 to 20 s utterances (consecutive segments merged across pauses up to 0.6 s), newest
  first, up to `--max-clips` (200). Restricted sessions are skipped unless `--include-restricted`.
- Downloads only each utterance's bytes (HTTP Range), not whole recordings.
- Writes `manifest.jsonl` and `clips/*.wav` (16 kHz mono) to `$XDG_DATA_HOME/jarvis-voice/eval/`,
  and refuses any directory inside a git checkout: the repo is public and these are recordings of
  your voice. Re-running adds only new clips.
- The reference is the server's STT, not a human transcript, so a score against it measures
  agreement with the server.
- The token goes only in the `Authorization` header; it is never printed or written.

## Reading an eval set in your own voice

```sh
uv run jarvis-voice eval record            # about 10 minutes for 40 sentences
uv run jarvis-voice eval score --eval-dir ~/.local/share/jarvis-voice/eval-read
```

It shows each of the 40 dictation-style sentences in `src/jarvis_desktop/eval_prompts.txt`:
Enter to start, Enter to stop, then Enter to keep or `r` to redo (`s` skips, `q` quits, and the
next run resumes). Takes are trimmed of silence and saved owner-only to
`$XDG_DATA_HOME/jarvis-voice/eval-read/` with the sentence as an exact reference, so `eval score`
reports a real WER. `--source` picks the mic (`pactl list short sources`).

On a Mac it records the default input (the built-in mic) with ffmpeg (`brew install ffmpeg`;
the terminal needs Microphone access in System Settings, Privacy & Security). `--source`
takes an AVFoundation input name, listed by `ffmpeg -f avfoundation -list_devices true -i ""`.
Copy the folder to the Linux box to score it there.

## Speech models and scoring

```sh
uv run jarvis-voice models install   # Whisper turbo + Parakeet + Silero VAD + the Piper voice, about 1.12 GB, SHA-256 pinned
uv run jarvis-voice eval score       # Whisper turbo on the pulled eval set (--engine parakeet for the fallback)
```

The default engine is Whisper large-v3-turbo through whisper.cpp built with Vulkan; it needs
`whisper-cli` on `PATH` or named by `JARVIS_WHISPER_CLI`. Dictation falls back to Parakeet on CPU
when Whisper fails. See `docs/plans/linux-desktop.md` section 2.2 for the measurements.

Models go to `$XDG_DATA_HOME/jarvis-voice/models/`. `eval score` prints disagreement with the
server's transcript and per-clip decode latency, and writes each hypothesis to
`<eval>/results/<engine>.jsonl` (owner-only). The server's references are often cut short and
spell numbers out, so disagreement overstates errors: use it to compare engines, not as a WER.
Tests marked `model` run only where the model is installed (`uv run pytest -m model`).

## Hold to ask

In a streaming capture session (`jarvis-live capture start`, not `--local-only`) on a server with voice
on, hold the ask key, ask a question out loud, and release. Jarvis answers from the session: the
reply shows in the pill and, through headphones only, is spoken. The plan is
[`docs/plans/voice-push-to-talk.md`](../docs/plans/voice-push-to-talk.md); the manual checks are
in [`TESTING.md`](TESTING.md).

**Not reachable yet.** The ask flow (`ask.py`), the key handling (`trigger.py`), the pill's
wording (`presentation.py`), the route gate (`route.py`) and the speaker (`speech.py`) are built
and tested with fakes, but there is no evdev monitor, pill or app loop to drive them (see Status).
The app that adds them builds `AskController(SocketControl(socket_path()), on_change,
speaker=default_speaker())` and feeds key events through `Settings.keys()`.

### The key

- **Right Alt** by default, held alone for at least 0.2 s, like dictation's hold. If dictation's
  key is Right Alt, the ask key is **Right Meta**. On layouts where Right Alt is AltGr, pick
  another key.
- Release ends the ask. There is no cancel: once begun, an ask always ends with the release, and
  Esc or other keys don't stop it. A key pressed with another one before 0.2 s (a chord) never
  begins one.
- The two keys don't mix: the ask key while dictating cancels the dictation and asks nothing;
  dictation's key while asking does nothing and the ask carries on.
- Settings are in `$XDG_CONFIG_HOME/jarvis-voice/settings.toml` (no Settings window yet):

  ```toml
  dictation_key = "right_ctrl"
  dictation_mode = "hold"
  ask_enabled = true        # false turns hold to ask off
  ask_key = "right_meta"    # right_ctrl | right_alt | right_shift | right_meta; omit for the default
  ```

  An ask key equal to dictation's turns asking off rather than sharing the key.
- No session, a local-only session or voice off on the server: the pill says why, and nothing is
  sent.

### Spoken replies: headphones only

A reply is spoken only when PipeWire's default sink is headphones; anything else gets the text,
with the reason in the pill ("Not spoken: no headphones."). The check reads `pw-dump` and fails
closed:

| Spoken | Never spoken |
|---|---|
| The headphone jack, when it reports something plugged in | Built-in or other speakers, HDMI and DisplayPort |
| A USB headset or headphones (form factor headset or headphone) | Anything without a form factor or port type: USB audio interfaces, line outs, virtual sinks |
| Bluetooth headphones or headsets on A2DP or HFP | Bluetooth LE Audio (BAP), car and hands-free, `pw-dump` missing |

- If the default sink changes, or stops being headphones, while Jarvis speaks, speech stops within
  0.1 s ("Stopped: headphones disconnected."). The playback stream is pinned to the headphones and
  never moved, so pulling them out never sends Jarvis to the speakers.
- **A session started with `--system` is text only** ("Not spoken: this session records what the
  computer plays."). System audio on Linux is the default sink's monitor, which hears every
  stream, ours included, so speaking there would put Jarvis in the transcript's `system` channel.
- **A USB audio interface (the Revelator, say) is text only**: PipeWire can't tell headphones on
  it from monitors on its line outs. Use the on-board jack or Bluetooth headphones for speech.
- **Known gap:** a Bluetooth speakerphone that calls itself a headset (the Anker PowerConf S3
  does) reads as headphones and would speak out loud. The server's echo filter still drops
  Jarvis's words from the transcript, but don't use one for hold to ask.
- The app tells the server when a reply started and finished playing (`ask_played`), or why it
  was skipped, so `jarvis-live ask-report` can show latency and the echo filter knows when to look.

### Installing the voice

Speech uses Piper (`piper-tts`) on the CPU with the
`en_US-lessac-medium` voice, synthesised in the app and played through `pw-cat`:

```sh
uv sync --extra speech               # piper-tts 1.8.0, hashed in uv.lock (GPL-3.0, not vendored)
uv run jarvis-voice models install   # includes the voice, about 60 MB of the 1.12 GB, SHA-256 pinned
```

`pw-cat` and `pw-dump` come with PipeWire (on Arch and CachyOS, `pipewire-audio` and `pipewire`). Without Piper or the voice, every reply is text only ("Not spoken: speech is
unavailable."). The voice is loaded when a session starts, so the first reply doesn't wait for it.

## Release

A release-please component tagged `linux-vX.Y.Z`. Packaging (an AUR PKGBUILD first) arrives with
phase 1.
