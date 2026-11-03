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

## Release

A release-please component tagged `linux-vX.Y.Z`. Packaging (an AUR PKGBUILD first) arrives with
phase 1.
