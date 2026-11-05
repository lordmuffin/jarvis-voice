# Jarvis Voice for Linux: manual test checklist

Real keys, headphones and a live server cannot run on CI. This is the pass/fail record for the
things that can't. Plan section 8 (`docs/plans/linux-desktop.md`) adds dictation's rows when
dictation ships.

Status key: **PASS** / **FAIL** (fill in) · **NOT RUN** (needs hands-on; steps below) ·
**BLOCKED** (needs a piece that isn't built yet) · **N/A**.

- Build under test: branch `feat/linux-hold-to-ask` (linux 0.x, jarvis-live client from the same
  commit)
- Date filled in: 2026-11-05 (automated items only; hardware items still open)

## Setup

1. `cd linux && uv sync --extra speech && uv run jarvis-voice models install` (the Piper voice is
   part of the install).
2. The capture client configured for lab: `jarvis-live capture configure --server <lab URL>
   --token-file <file> --mode stream`, with a device token from lab's Devices page. Lab has
   `VOICE_ENABLED`.
3. Name the outputs you will use: `wpctl status` lists the sinks; `wpctl set-default <id>` picks
   one. For speech use Bluetooth headphones on A2DP or the on-board headphone jack: a USB audio
   interface reads as text only by design (README, "Spoken replies").
4. Start a session: `uv run --project ../server jarvis-live capture start` (Solo, no `--system`,
   no `--local-only`) and keep `jarvis-live capture tail` open in a second terminal: it prints each
   reply as `[ask] ...`.

**Blocked today:** the app has no key monitor, pill or main loop yet (plan section 7, PRs 4 to
7), so the B rows need the PR that adds them. Until then the flow can only be driven from tests.

## A. Automated (no hardware)

| # | Check | Result | Notes |
|---|---|---|---|
| A1 | `cd linux && uv run pytest -q` | **PASS** | Ask flow against a fake daemon socket, trigger and `Keys` cases, settings, pill wording, route gate on recorded `pw-dump` fixtures, the speaker with a fake `pw-cat` |
| A2 | `cd server && uv run pytest -q tests/client/test_ask.py` | **PASS** | The daemon's `ask_begin` / `ask_end` / `ask_played` against `FakeStream` |
| A3 | The real Piper voice | **PASS** | `uv run pytest -m model tests/test_speech.py` with the voice installed; synthesis only, nothing played aloud |
| A4 | `pw-cat` playback with the app's arguments | **PASS** | Silence only, on this machine's default sink |

## B. Hands-on (Linux desktop, lab)

Record the result and a line of evidence for each.

| # | Check | Steps | Pass when | Result |
|---|---|---|---|---|
| B1 | 30 held asks, headphones | Headphones as the default sink. In the Solo session, talk for a minute, then hold the ask key (Right Alt, or Right Meta if dictation has Right Alt), ask "what did I just say about X?", release. 30 times over the session, some short, some 10 s long, one released before speaking. | The pill reads Listening while held, Thinking after release, Speaking with the reply, and the answer is heard in the headphones. The early release shows "Didn't catch that". `capture tail` prints every reply as `[ask]`. **No transcript line holds Jarvis's words** on the dashboard (your questions do, as Me). | BLOCKED |
| B2 | Built-in speaker shows text only | Set the laptop speakers (or a monitor's HDMI output) as the default sink. Hold, ask, release. Then the same on the USB interface. | The pill reads "Text only" with "Not spoken: no headphones.", the reply text is shown, and nothing is heard. | BLOCKED |
| B3 | Unplugging mid-reply stops speech | Headphones on. Ask something with a long answer. While Jarvis speaks, pull the jack (or switch the Bluetooth headphones off). Repeat once by changing the default sink with `wpctl set-default`. | Speech stops at once, nothing plays from the speakers, and the pill reads "Stopped: headphones disconnected." | BLOCKED |
| B4 | A `--system` session is text only | Stop the session, start one with `--system`, headphones on. Hold, ask, release. | "Not spoken: this session records what the computer plays."; nothing heard. | BLOCKED |
| B5 | No session, local-only | Stop the session, press and hold the ask key. Then start one with `--local-only` and hold it again. | "Start a capture session to ask Jarvis." and then "Voice is off in this session."; `capture tail` shows nothing sent. | BLOCKED |
| B6 | Keys stay apart | Hold dictation's key and press the ask key; then hold the ask key past 0.2 s and press dictation's key. Press Right Alt + a letter quickly. | The first cancels the dictation and asks nothing; the second carries on asking and dictates nothing; the chord does nothing. | BLOCKED |
| B7 | Ask report | After B1, run `jarvis-live ask-report --days 1` against lab's database (in the lab pod). | A row for this machine's device name and lab's voice model, with n ≥ 30 and `first_audio` p50 and p95. Put the numbers in `docs/plans/voice-push-to-talk.md`, Results. | BLOCKED |

## Known limits to check while testing

- **No cancel.** Once an ask begins it always ends on release; Esc does not stop it. The
  protocol has no cancel message.
- **Bluetooth speakerphones** that report form-factor headset (the Anker PowerConf S3 does) read
  as headphones and would speak out loud. Don't use one for B1 to B3.
- **First reply after a session starts** may be slower if the voice was still loading; it warms
  on the session's first `voice_state`.
