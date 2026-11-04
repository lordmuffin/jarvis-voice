# Voice on live: the run after server-v0.37.0

Set 2026-11-04, after #311, #313 and #312. This is the run sheet for the remaining steps to voice on live.
The herd lines for each stage are in `Tasks.herd`, under "VOICE ON LIVE". Merging the PR that adds this
file approves starting stages 1 and 2. Every other stage stays held until its gate clears and its line is
uncommented.

## Where things stand

- **#311 merged and released in server-v0.37.0.** The server now sends a new voice request:
  - today's weekday is named
  - each spoken kind has its own shape
  - the repo rule changed
  - voice repeats are deduped

  On the card through `interactive`, `gemma4-e4b` passed 73 of 75 runs, 24 of 25 cases, and every critical
  case 3 of 3. **It misses the af416ec bar by one case**: "two steps" passed 1 of 3, because "Friday at ten"
  became Saturday the 7th. Reply p95 was 2.51 s against 2.5 s, with 0 fallbacks in 75 attempts. The ask eval
  and the copilot cases did not run. See `fast-tier.md`, "The voice request, fixed".
- **#313 merged: lab's `VOICE_MODEL` is `interactive`**, pinned by `lab.rego`. Live keeps `""`, and voice is
  off there, so server-v0.37.0 changes nothing that live does. Gemma was taken for lab with the miss known.
  #313's DONE WHEN also asked for five held asks on lab answered by `interactive`. They have not run yet.
  Stage 1 does them.
- **homelab #859 merged.** The `interactive-llm` pod has a startup probe that runs two short completions
  with `response_format`, and a persisted CUDA JIT cache. No restart has happened since, so the warm-up has
  not yet been seen working.

## Stages

| Stage | Herd branch | Repo | Gate | Uses the card |
|---|---|---|---|---|
| 1 | `chore/lab-voice-check`: 5 held asks and 5 spoken requests on lab, then ask-report | jarvis-voice | **Ready on merge** | Lightly, as lab traffic |
| 2 | `fix/voice-today-tz`: "today" comes from the configured timezone, not UTC | jarvis-voice | **Ready on merge**, beside stage 1 | No |
| 3 | `chore/gemma-ask-evals`: R6's ask eval on the card. The copilot cases run only if Andrew says so | jarvis-voice | Stage 1 done | Yes |
| 4 | `feat/voice-day-table`: give the next seven dates by name, shorten replies if needed, rerun the bar | jarvis-voice | Stage 2 merged, and Andrew decides Gemma must pass first | Yes, after stage 3 |
| 5 | Andrew's hardware test on `interactive` (30 asks per device), then ask-report | — | Stage 1 done | As lab traffic |
| 6 | `feat/voice-on-live` (R7): voice on in base, `VOICE_MODEL` `interactive` | jarvis-voice | Stage 5 done, plus decisions 2 and 3 below | No |
| 7 | `feat/copilot-fallback-interactive`: a plan only | homelab-gitops | R1b fails on Flash, **and** stage 3 shows the 11 copilot bar cases passing | No |
| 8 | `chore/whisper-memory`: read-only | homelab-gitops | Only if Andrew wants Whisper beside `interactive` | No |

**Order:**
- Stages 1 and 2 run together.
- Stage 3 starts when stage 1 is done.
- Stage 4 needs stage 2 merged and Andrew's go, and goes on the card only after stage 3.
- Stage 5 follows stage 1, then stage 6.
- Stages 7 and 8 are held.
- R1b (`chore/copilot-replay-busy`) is unchanged and still waits for a weekday with agents busy.

**Why stage 4 waits for stage 2:** both change `voice/service.py`, and PRs are never stacked.

**Why stage 3 waits for stage 1:** the card takes one eval at a time, and lab's live path shares
`interactive`'s two slots.

**Homelab lines (stages 7 and 8):** these must not start from this repo. Copy the one line, without its
`#`, to a scratch file, and run `herd -r ~/git/homelab-gitops -f <file>`.

## Decisions that are Andrew's

1. Merging every PR, including each release PR.
2. **Whether #311's one-case, 0.01 s miss is good enough for live**, or stage 4 must pass first. Lab is
   decided.
3. Turning voice on in live, and section 5 of `voice-commands.md`.
4. Whether stage 3 runs the 17 copilot cases now, or waits for R1b.
5. Whether the copilot falls back to Gemma, and where that would live: LiteLLM or the server.
6. Whether Whisper should be able to run beside `interactive` on pve. The REPLAN keeps it on the gaming PC.
7. Any change to the af416ec bar, the 11 copilot bar cases or the 2.5 s target.

## Stop rules that hold for every stage

- **Card runs:** any fallback to `heavy`, an `interactive-llm` restart or a SHA256 mismatch makes a run
  invalid, not failed. Stop and report.
- **No load:** one eval at a time on the card, and no load tests. A card eval pauses when real lab traffic
  shows up in the pod's log.
- **No bar moves:** a near pass is reported as measured. Only Andrew moves a bar.
- **Data and keys:** eval rows are never committed. Keys are never printed, are kept 0600 in a scratchpad,
  and are deleted at the end.
- **Public repo:** no hosts, addresses or keys in anything committed here.
- **Deploy rules:** never touch `deploy/*`, the image line or lab's `newTag`. Lab never gets a vault write
  credential or publishing.
