# Jarvis Voice for the Linux desktop

`jarvis-voice`: dictation anywhere, a tray app and a Copilot panel for KDE Plasma 6 on Wayland.
It drives the headless capture client in [`server/src/jarvis_live/client/`](../server/src/jarvis_live/client/)
and is not a second client. The plan, with the decisions and the STT benchmark, is
[`docs/plans/linux-desktop.md`](../docs/plans/linux-desktop.md).

Status: scaffold only. Nothing user-facing yet.

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

## Speech models and scoring

```sh
uv run jarvis-voice models install   # Parakeet-TDT-0.6B-v2 int8 + Silero VAD, about 480 MB, SHA-256 pinned
uv run jarvis-voice eval score       # run Parakeet on the pulled eval set
```

Models go to `$XDG_DATA_HOME/jarvis-voice/models/`. `eval score` prints disagreement with the
server's transcript and per-clip decode latency, and writes each hypothesis to
`<eval>/results/<engine>.jsonl` (owner-only). The server's references are often cut short and
spell numbers out, so disagreement overstates errors: use it to compare engines, not as a WER.
Tests marked `model` run only where the model is installed (`uv run pytest -m model`).

## Release

A release-please component tagged `linux-vX.Y.Z`. Packaging (an AUR PKGBUILD first) arrives with
phase 1.
