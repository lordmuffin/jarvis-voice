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

## Release

A release-please component tagged `linux-vX.Y.Z`. Packaging (an AUR PKGBUILD first) arrives with
phase 1.
