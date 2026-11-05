# Plan: speak to Jarvis during a recording

Status: **on for both channels in base, through `interactive`** (R7, `feat(server): voice on live`).
Lab has run it since 2026-11-04. Live gets it with the release after that PR, which waits for
Andrew's device test on lab. §5 is decided: keep the dashboard tap. Pi sessions stay off on both
channels. On live, spoken tasks, reminders and PR comments need `JARVIS_LIVE_ACTIONS_ENABLED`,
which live does not set yet, so there they answer "Workflow actions are off here" (see below).
Source: session 89087a2c, 2026-10-31 ("go off and create a cloud session or a pie session or
whatever and just do work on my behalf in my workflows").

## Goal

While recording, say "Jarvis, start a Pi session to find out why Flux is failing" and have that
request become work, without opening anything first.

## How it works

```
mic -> STT -> final segment -> VoiceLoop -> wake phrase? -> wait for you to finish
                                                               |
                                            LLM: cloud / pi / action / none
                                                               |
       cloud -> draft work item     pi -> pi.session proposal     action -> proposals
                                                               |
                         reply: a copilot suggestion on every client, plus a Gotify push
                                                               |
                         you approve -> the existing gates run it
```

No client changes and no `protocol/` change: every client already streams the mic, and the reply
is a `copilot` suggestion, which every client already renders. Jarvis answers in text. Speaking
the answer is talkback (`docs/designs/voice-talkback.md`), which is still waiting on that
document's Phase 0 and open questions.

| Spoken target | What it means here | Where it lands | What starts it |
|---|---|---|---|
| Cloud session | A headless Claude Code run: the existing work item dispatch | Draft work item | Approve, then Run in Claude Code, from the dashboard sign-in |
| Pi session | A new, separate Pi Durable session briefed with the task | `pi.session` action proposal | Approve from the dashboard sign-in |
| Workflow | The existing actions: task, vault note, reminder, PR comment | Action proposals | Approve, from the dashboard or a device |

**Assumption:** "cloud session" means the existing dispatch path, the only cloud agent run this
repo can start. It is not Claude Code on the web.

## Rules

1. **Voice proposes, it never authorizes.** `copilot-actions.md` §4.2 and §10 and
   `copilot-to-claude-code.md` §4 and §5 rule out auto-run, and speech is not authentication:
   anyone in the room, or a speaker near the mic, can say "Jarvis". §5 records the decision to keep it.
2. **Only your own voice.** Mic channel only, and never a voice diarization has labelled as
   someone else. Words on the system channel, such as a caller saying "Jarvis, ...", are ignored.
3. **`pi.session` exists only behind the wake phrase.** It is dropped from anything an LLM emits
   as a proposal (copilot, finalizer, voice), and only the voice pass builds one, so transcript
   text cannot talk the copilot into proposing an agent.
4. **`pi.session` needs the dashboard sign-in.** D3 let a device token approve actions because
   they are low risk. A Pi agent has `bash`, so it follows the work item rule instead.
5. **Same eligibility as work items:** the owner's and admins' sessions, never local-only or
   restricted ones.
6. **No Pi sessions on lab, by policy.** `policy/lab.rego` refuses `voice_pi_enabled` there: lab
   runs code no release has vouched for, and there is no sandbox Pi. It is off on live too until
   someone turns it on.

## Wake phrase

"Jarvis Live" and "Jarvis Voice" are product names you say all the time, so a bare word match
would fire constantly. A request starts at "hey/ok Jarvis" anywhere, or at "Jarvis" opening an
utterance or sentence, but never at "Jarvis Live/Voice/app". A false hit costs one LLM call
that answers `none`.

You pause while you think (the source session has a 15 s gap), so a request runs until
`voice_settle_s` passes with no more of your speech, up to `voice_max_s`. "Scratch that" inside
a request makes the LLM answer `none`.

## Settings

| Setting | Default | |
|---|---|---|
| `JARVIS_LIVE_VOICE_ENABLED` | `false` | The loop itself |
| `JARVIS_LIVE_VOICE_PI_ENABLED` | `false` | Offer Pi sessions |
| `JARVIS_LIVE_VOICE_MODEL` | empty | Empty: the copilot model |
| `JARVIS_LIVE_VOICE_SETTLE_S` | `10` | Quiet that ends a request |
| `JARVIS_LIVE_VOICE_MAX_S` | `90` | Longest request |
| `JARVIS_LIVE_DPI_MODEL` | `qwen3.8-flash-next` | Model for new Pi sessions |
| `JARVIS_LIVE_DPI_WORK_CWD` | `/var/lib/dpi/work` | Their working directory |

## Files

- `server/src/jarvis_live/voice/`: `wake.py` (pure), `schema.py`, `service.py`, `loop.py`
- `actions/`: the `pi.session` kind, `pi.py` (executor), the sign-in rule in `service.py`
- `homelab/client.py`: `start_session`, the same Idempotency-Key and requestId calls Jev makes
- `workitems/rows.py`: `store_changes`, shared by the ideas and voice passes
- `copilot/`: `announce`, the reply path
- `orchestrator.py`, `app.py`, `config.py`, `actions/wiring.py`
- `web/`: label, description, edit fields and result for `pi.session`
- `server/deploy/k8s/`: settings

## Tests

| Acceptance criterion | Proved by |
|---|---|
| Live voice interaction during recordings | `test_voice_wake.py`, `test_voice_loop.py` (real orchestrator: segments in, reply out, others' speech ignored) |
| Can create a cloud session | `test_voice_loop.py`: a draft reaches the dispatcher once approved from the web; `test_dispatch_gate.py` still passes unchanged |
| Can create a Pi session | `test_homelab_client.py` (request shape, idempotency), `test_actions_pi.py` (approve, session created once) |
| Can do work in my workflows | `test_voice_loop.py`: a spoken reminder reaches the queue and runs once approved |

## Before this works on the cluster (homelab-gitops)

1. Admit jarvis-live to `dpi-backend`: `apps/pi-harness/pattern-a-statefulset/networkpolicy.yaml`
   admits only `pi-durable`, `jev` and `netbird`, so calls from `jarvis-live` are dropped today.
   That also affects the existing `/v1/homelab/ask`.
2. Add the `DPI_TOKEN_JARVIS` field to the `pi-durable` 1Password item.

Then set `JARVIS_LIVE_VOICE_PI_ENABLED`.

## 5. The decision: keep the tap

The request was "just do work on my behalf". This build stops one step short: nothing starts
until you approve it, because a recorded rule says so and a voice is not a credential.

**Decided by Andrew, 2026-11-05: keep it.** A spoken request is filed and waits for approval from
the link in the push, on live as on lab. There is no spoken confirmation and no auto-run class.
Rule 1 stands unchanged.

The options that were not taken, kept for a later decision:

- **Spoken confirmation.** Jarvis reads the request back and you confirm. This needs talkback,
  and it still is not authentication: whoever spoke first can confirm too.
- **Auto-run for a low-risk class** such as read-only Pi sessions, which pi-durable supports
  (`readOnly`). This is the smallest real relaxation of the rule, and it would mean amending
  `copilot-actions.md` §4.2.

## On live: what needs more than this

- **Workflow steps.** A spoken task, reminder or PR comment is filed through the action queue,
  which runs only with `JARVIS_LIVE_ACTIONS_ENABLED`. Lab's overlay sets it. Base and live do not,
  and live's running config map has no such key, so there `_file_actions` answers "Workflow
  actions are off here. Nothing was started." Turning it on in base also turns on the copilot's
  and the finalizer's proposals on live, where the vault kinds have a real write credential.
  That is Andrew's call, in its own PR.
- **Cloud sessions** need only work items, which base turns on: they file a draft work item on
  live. Dispatch is off on live, so it stays a draft to copy.
- **Pi sessions** need the homelab steps above, and an edit to `policy/live.rego`.

## Out of scope

Proposals in the macOS and Android apps, and any way to approve by voice. Spoken replies and a
push-to-talk control are now `voice-push-to-talk.md`: with the key held, words opening with
"Jarvis" are filed through this same pass, and its reply is spoken. Without the key, replies stay
copilot notices and are never spoken.
