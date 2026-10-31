# Plan: headless Linux capture client

Status: **proposed**, not implemented. Scope: `server/` (package `jarvis_live`), `README.md`,
a systemd user unit and an install script. **No `protocol/` change**, so no Swift change
(CLAUDE.md only requires Swift tests when `protocol/` changes). Legacy `src/jarvis_voice/` and
`macos/` are not touched.

Goal: SSH to any Linux box, run one command, speak, and see the session in the web dashboard.

## 1. What exists today

| Piece | Where | State |
|---|---|---|
| Wire protocol v1 | `protocol/v1/` | 15 schemas, 19 valid and 17 invalid fixtures, `frame_mic_seq7.hex`. Server side pinned by `server/tests/test_protocol_fixtures.py`. |
| Python protocol models | `server/src/jarvis_live/protocol.py` | Pydantic models for every message, `encode_frame`/`decode_frame`, `BYTES_PER_MS`, `MIN_PAYLOAD`. Client-usable as is. |
| Python producer | `server/src/jarvis_live/replay.py` | `ReplayProducer`: create session, ticket, WS, `hello` with resume, send frames, read acks. Reads a WAV, **no capture, no durable outbox, no reconnect loop**. Used by tests and `jarvis-live replay`. |
| CLI | `server/src/jarvis_live/cli.py` | argparse, entry point `jarvis-live`. Mostly server admin (`create-device`, `serve`, `migrate`, needs DB) plus the client-ish `replay`. This is the scaffolding to extend. |
| Swift client | `macos/JarvisLiveKit/.../Transport/LiveConnection.swift`, `Outbox/Outbox.swift`, `ReconnectPolicy.swift` | The reference behaviour, summarised in section 2. |
| Capture tools on a typical box | `parec`, `pw-record`, `pactl` | Present on this machine; both PulseAudio and PipeWire (via pipewire-pulse) serve `parec`. |
| `systemd/` | repo root | Legacy system-level units (`jarvis-voice-agent.service` etc.). No user units yet. |

Two things the brief assumes that are not true yet:

1. **Task 6 (local-only) has not shipped.** There is no `docs/plans/local-only-audit.md`, no
   transport-level gate, and no egress test. What exists is a macOS UI toggle
   (`AutoLocalOnly` in `Network/AwayMode.swift`, `AppModel.localOnly`) and an unused
   `sessions.local_only` column in `0001_initial.py`. `SessionCreateRequest` has no local-only
   field, so a local-only Mac session never reaches the server. See section 5.
2. **`cli.py` is not a client.** It is the server's admin CLI, and `jarvis-live` installs
   fastapi, sqlalchemy, asyncpg, alembic, numpy and webrtcvad. See open question A.

## 2. Reference behaviour to port (from the Swift client)

- **Outbox** (`Outbox.swift`): per session, per channel `<ch>.pcm` plus `<ch>.index.jsonl`
  (`{seq,t_ms,offset,len}`, appended after the payload so a torn write leaves an unindexed tail
  that is truncated on reopen), plus `acked.json`. Seqs must be contiguous per channel. Only the
  server's cumulative `ack` frees data. Compaction once fully acked and over 4 MiB. No per-frame
  fsync: survives a process crash, not power loss.
- **Connection** (`LiveConnection.swift`): on every (re)connect fetch a fresh ticket, send
  `hello` with the outbox's acked seqs as `resume`, treat `hello_ack.acked` as authoritative,
  replay from `acked+1`, then stream. Backpressure: pause while more than 5 s of sent audio is
  unacked. `end`: flush all frames, send `end`, wait for everything acked, then up to ~10 s for
  `final_note`/close. If the server is unreachable `end` gives up but the outbox stays resumable.
  401/403/404 on REST are fatal; everything else retries.
- **Backoff** (`ReconnectPolicy.swift`): 0.5 s doubling to 30 s, plus up to 25 % jitter.
- **Known divergence to resolve:** `replay.py::_first_seq` restarts at `min(resume, server_acked)+1`
  and from 0 if either side is null, while Swift restarts at `server_acked+1`. Both are safe
  (the server ignores re-sent frames) but I will follow Swift and leave `replay.py` behaviour
  alone, since its tests depend on it.

## 3. Design

### 3.1 Code layout (extends `jarvis_live`, no third client)

```
server/src/jarvis_live/client/
  outbox.py      # port of Outbox.swift, same on-disk layout
  transport.py   # resumable producer connection (the only module that touches the network)
  capture.py     # parec/pw-record subprocess -> 100 ms frames
  daemon.py      # long-running process: capture + transport + control socket
  control.py     # unix-socket JSON-lines API used by stop/status/tail
  config.py      # ~/.config/jarvis-live/client.toml + env overrides
```

`cli.py` gains a `capture` command group that imports `jarvis_live.client` lazily (as the
existing admin commands do with the DB), so `serve`/`replay` are unaffected.
`transport.py` reuses `protocol.py` models, `encode_frame`, and the URL/ticket logic from
`ReplayProducer`, which I will extract into a small shared helper rather than copy. `replay.py`
keeps its public API and tests.

### 3.2 Commands

```
jarvis-live capture configure --server URL --token TOKEN [--mode stream|local]
jarvis-live capture start  [--title T] [--meeting] [--system [SINK]] [--local-only] [--source NAME] [--foreground]
jarvis-live capture stop   [--no-wait]
jarvis-live capture status [--json]
jarvis-live capture tail   [--session ID] [--json]
```

- **start**: loads config, creates the session (`POST /v1/sessions`, `mode` solo, or meeting with
  `--meeting`), spawns the daemon detached (`start_new_session`, so it survives the SSH
  disconnect), waits until the daemon reports connected, prints the session id and the dashboard
  URL, and returns. `--foreground` runs the daemon in the terminal for debugging and for systemd.
  If a session is already running it refuses and says so. If the outbox has an unfinished
  session from a crash it offers to resume it (`start --resume [ID]`).
- **stop**: asks the daemon over the control socket to flush, send `end`, wait for acks and
  `final_note`, print the note path, exit. 30 s timeout like Swift; on timeout the outbox is
  left resumable and `stop` says so.
- **status**: daemon state, session id, uptime, connection state (connecting, connected,
  reconnecting n), unacked audio ms, last ack age, mode (STREAMING or LOCAL ONLY, always
  printed in capitals), source device, dashboard URL. Exit code 0 running, 3 not running.
- **tail**: prints finalized `segment` events as `[mm:ss] me|them: text`, plus `copilot`
  suggestions and `final_note` when they arrive. Default: attach to the daemon's control socket,
  which relays the events it already receives as producer (no extra ticket). `--session ID`
  instead opens a `viewer` ticket and works from any machine. Needs verification that a viewer
  gets a useful stream (`stream.py::_viewer`); see step 6.
- **Channels**: mic via `parec`. `--system` adds channel 1 from the default sink's monitor
  source (`<sink>.monitor`), which satisfies `channels: ["mic","system"]` (system-only is
  invalid per the fixtures and is rejected up front).

### 3.3 Capture

Shell out to `parec --format=s16le --rate=16000 --channels=1 --latency-msec=20 [--device=...]`,
falling back to `pw-record --rate 16000 --channels 1 --format s16 -` when `parec` is absent.
The sound server resamples and downmixes, so the client has no audio library, no C extension,
no GUI dependency, and no numpy use on the hot path. Read stdout, cut into exact 100 ms frames
(3200 bytes, inside the 640..6400 protocol range), pad nothing except a final short tail
(zero-padded to 20 ms, same as `split_frames`).

- `t_ms` comes from samples emitted, not wall clock, so it cannot drift. If the capture process
  dies and is restarted, seq stays contiguous (the server rejects gaps with `seq_gap`) and
  `t_ms` jumps forward by the wall-clock gap so the transcript timeline stays honest.
- Capture-process supervision: restart on exit with the same backoff policy; after N rapid
  failures mark the daemon `failed: no audio source` and surface it in `status`.
- A box with no audio source (typical VM) fails `start` early with a specific message and the
  output of `pactl list short sources`; `--source` selects a device.

### 3.4 Daemon, control, state

- State dir `$XDG_STATE_HOME/jarvis-live/` (outbox under `sessions/<id>/`), runtime dir
  `$XDG_RUNTIME_DIR/jarvis-live/` (control socket `ctl.sock`, mode 0600, and `daemon.pid`).
  Fallback to `/tmp/jarvis-live-<uid>/` when `XDG_RUNTIME_DIR` is unset (plain SSH on some
  hosts).
- Control protocol: JSON lines over the unix socket; commands `status`, `stop`, `subscribe`.
  Pure local IPC, never a TCP listener.
- Config precedence: CLI flag, then env (`JARVIS_LIVE_SERVER`, `JARVIS_LIVE_TOKEN`,
  `JARVIS_LIVE_TOKEN_FILE`), then `client.toml` (written mode 0600). The device token comes from
  `jarvis-live create-device` as today. The token is never printed by `status` and is passed to
  nothing but the `Authorization` header.

### 3.5 systemd user unit

`jarvis-capture.service` installed to `~/.config/systemd/user/`:

```
[Service]
Type=simple
ExecStart=%h/.local/bin/jarvis-live capture start --foreground --resume-or-new
Restart=on-failure
RestartSec=5
EnvironmentFile=-%h/.config/jarvis-live/env
```

`loginctl enable-linger $USER` is needed for a permanent endpoint on a headless node, so the
installer prints that instead of running it unasked. A unit that auto-starts a session on boot
records continuously, which is its own decision: the unit is opt-in (`systemctl --user enable
--now`), and `start --resume-or-new` is documented as "this node is a standing capture
endpoint". The daemon rolls to a new session after `--max-session-hours` (default 4) so one
session does not run for a month. Lab and live are chosen by `--server`; nothing is hardcoded.

### 3.6 Install

One command, no PyPI (releases are tagged `server-vX.Y.Z`):

```
curl -fsSL https://raw.githubusercontent.com/lordmuffin/jarvis-voice/main/scripts/install-linux-client.sh | sh
```

The script is short and readable: ensures `uv` (or uses `pipx` if present), runs
`uv tool install "jarvis-live @ git+https://github.com/lordmuffin/jarvis-voice@server-vX.Y.Z#subdirectory=server"`,
checks for `parec` or `pw-record` and says which package to install if absent, drops the unit
file, and prints the `configure` line. The repo is public (CLAUDE.md), so no credentials are
involved. The README gets a "Linux headless client" section after the existing Server
installation section: install, configure, start, tail, stop, systemd.

## 4. Tests (TDD order, `make test` must pass)

All Python, under `server/tests/client/`, no Docker needed except where noted.

1. **Protocol conformance** (the "same fixtures" constraint). For every file in
   `protocol/v1/fixtures/valid`: server-to-client messages decode through the client's event
   parser to the right model; client-to-server messages built by the client (`hello` fresh and
   resume, `draft_segment`, `marker`, `end`, session and ticket requests) validate against
   `schemas/*.json` with `jsonschema`. Every `invalid/` fixture is rejected by the parser.
   `frame_mic_seq7.hex` round-trips byte-exact. A guard test fails if a new schema appears in
   `protocol/v1/schemas` without a client mapping, mirroring
   `test_every_schema_has_a_model_and_both_fixture_kinds`.
2. **Outbox**: contiguous-seq enforcement, torn-write recovery, ack persistence, compaction,
   reopen after crash. Same cases as the Swift outbox tests.
3. **Transport against the real in-process server** (the `ServerHandle` fixture `test_cli.py`
   already uses; needs Postgres like `test_integration.py`): stream, drop the connection
   (`ws.transport.abort()` as in `abort_after_frames`), resume, assert zero lost and zero
   duplicated stored frames; 401 is fatal; reconnect backoff via an injectable clock; `end`
   waits for `final_note`.
4. **Capture**: a fake `parec` script on `PATH` emitting a known sine, asserting frame sizes,
   seq/t_ms, restart-with-gap behaviour, and the no-source error.
5. **Daemon/CLI**: start, status, tail, stop against the fake capture and the test server;
   stale socket and pid cleanup; refusing a second start.
6. **Local-only egress** (section 5).
7. **Unit file lint**: `systemd-analyze --user verify` where available, otherwise a parse test.
   Install script: `shellcheck` in CI.

Manual acceptance (the DONE WHEN): on the Spark and one VM, `ssh box`, `jarvis-live capture
start`, speak, confirm segments appear in the dashboard and in `tail`, `stop`, confirm
`final_note`. Recorded in the PR description, not just asserted.

## 5. Local-only

Task 6 has not landed, so there is nothing to "honor" yet. Proposal: the Linux client ships its
own small, self-contained gate with the contract task 6 describes, and task 6 later aligns the
macOS and Android clients to the same shape.

- **Mode is explicit.** `configure` writes `mode = "stream"` or `"local"`; the daemon refuses to
  start if the mode is absent or unrecognised (no silent default to streaming). That is the
  conservative reading of "ambiguous means local-only" for a client with no network-place
  detector: it never uploads without a clear instruction, and it fails loudly instead of
  quietly recording locally when the user expected a dashboard session.
- **Hard gate at the transport layer, not per call site.** `transport.py` is the only module
  allowed to import `httpx` or `websockets`, enforced by a test that greps the package's
  imports. In local mode the daemon is constructed with a `NullTransport`; there is no code path
  from capture to the network to forget to check.
- **What local mode does on Linux:** captures to the outbox and nothing else. No session is
  created on the server, no REST calls, no health probe, no telemetry, no tickets. There is no
  on-device STT in this client, so there is no draft transcript (the Mac produces one with
  Apple speech); `tail` prints "local-only: no transcript" and `status` says
  `LOCAL ONLY (nothing leaves this machine)`.
- **Egress test.** A pytest fixture patches `socket.socket.connect`/`getaddrinfo` so any
  non-`AF_UNIX` use fails the test, then runs a full local session (start, capture the fake
  source, status, tail, stop). The one allowed outbound set is empty; the unix control socket
  is the only allowed IPC. A mirror test proves the same fixture does fail when a stream-mode
  session is started, so the guard is shown to bite.
- **Visibility.** `status`, `start` output, and the systemd journal line at startup all state
  the mode.

## 6. Open questions (need your call before I code)

A. **Dependency weight.** `jarvis-live` is one package and lists the whole server stack as hard
   dependencies. Options: (1) install as is: `uv tool install` pulls asyncpg, sqlalchemy, numpy
   and webrtcvad-wheels onto every capture node. Zero risk to the server image, but heavy, and
   `webrtcvad-wheels` needs a wheel for each architecture (the Spark is aarch64). (2) Move the
   server deps into a `server` extra and leave a slim core (httpx, websockets, pydantic,
   jsonschema stays dev). Light client, but `server/Dockerfile` (`uv sync --frozen --no-dev`)
   and `uv.lock` change and the deploy CI must be re-verified. **Recommend (2),** as a separate
   preliminary PR with its own test, since a one-command install onto a VM is the point.
B. **Local-only sequencing.** Should this wait for task 6 (the suggested order is 1, 6, ..., 10),
   or proceed now with the self-contained gate in section 5? **Recommend proceeding now**: the
   gate is client-local and cheap to realign later. If you would rather wait, this plan stands
   but section 5 is rewritten against the task 6 report.
C. **Local sessions: can they ever be uploaded?** The Mac keeps local-only files "waiting to be
   uploaded". For Linux I plan no upload command in v1 (an upload feature on a work-call-safe
   path is exactly the leak task 6 worries about). Say if you want `capture upload ID` anyway.
D. **Standing-endpoint unit.** Is auto-starting a session at login/boot what you want for "permanent
   capture endpoint", or should the unit only keep a daemon alive and idle until `start` is run?
   Recording continuously on a node is a bigger privacy decision than the rest of this plan;
   the plan defaults to opt-in with a 4 h session roll.
E. **Distribution.** The installer pins a `server-v*` tag from GitHub. The Forgejo memory note
   says Forgejo is the source of truth for the homelab repo, but CLAUDE.md says this repo is
   public on GitHub with release-please there. I assume GitHub for this repo; tell me if the
   install URL should point at `git.apj.dev` instead.

## 7. Work breakdown (one PR each, Conventional Commits, scope `server`)

1. (if A2) `chore(server): split server deps into an extra`.
2. `feat(server): client outbox` with tests 2.
3. `feat(server): resumable producer transport` with tests 1 and 3 (conformance plus resume).
4. `feat(server): parec capture` with tests 4.
5. `feat(server): jarvis-live capture start|stop|status|tail` with tests 5, local-only gate and
   test 6.
6. `feat(server): systemd user unit and installer`, README section, tests 7, manual acceptance.

Each step is red then green per `TDD.md`, with `uv run ruff check . && uv run ruff format --check .
&& uv run mypy src && uv run pytest -q` in `server/` and `make test` at the root before done.
release-please will cut a `server-v*` minor from the `feat:` commits; lab deploys on merge to
main as usual and the client is not part of the server image's runtime path, but if A2 is taken
the image build is, so that PR is verified through the Server Image workflow before the rest.

## 8. Risks

- Audio-stack variety: `parec` against PipeWire-pulse behaves, but a bare ALSA box has neither;
  the error path must say how to fix it rather than hang.
- `XDG_RUNTIME_DIR` is absent in some non-login SSH sessions, so the control socket path has
  the fallback in 3.4.
- Unattended recording is the real hazard of the systemd mode; mitigated by opt-in, the session
  roll, and a mode line in every status output.
- Disk fill while offline: the outbox holds 1.92 MB per minute per channel. Add a cap
  (`--max-outbox-mb`, default 2048) that stops capture with a visible `failed` state rather than
  filling the disk.
