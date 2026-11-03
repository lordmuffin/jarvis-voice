# Fast tier: findings and proposed changes

Status: **step 3 measured 2026-11-01, rechecked from its stored rows 2026-11-02. The 3B fails, so steps 4 and 5 do not go ahead, on any model yet. Steps 1 and 2 are done (#247, homelab #838). One live bug found, and step 3a fixed it: on live in `server-v0.32.2`, with no lab soak. The REPLAN in `Tasks.herd` overrides this plan where they differ: the copilot names Flash, and [its done test](#the-done-test) is p95 under 15 s with 0 cycles lost. **Not yet shown:** the first replay met both figures, p95 14.75 s and 0 lost, on an idle engine, so it failed the load gate. See [The replay](#the-replay).**
Task 12 in `Tasks.herd`. Read on 2026-11-01 from `main` of both repos and the cluster.

**Step 3's result is in [The comparison](#the-comparison).** Most other sections were written before
it. Where step 3 contradicts them, step 3 wins. [The recheck](#recheck-2026-11-02) corrects two of its
figures, and the verdict stands.

## Answer

**Step 4 does not go ahead, on the 3B or any other model. Step 5 waits for it.** There is no model
to name yet.

**R2, 2026-11-03: no candidate passes the voice eval, so there is still no model.** The nearest is
`gemma4-e4b`, one case short on an eval-only request. Its 2070 speed is unproven. What you have to
decide is in [R2](#r2-the-model-for-pves-2070).

- **The 3B fails on quality.** It passed 18 of 51 eval runs against Flash's 37 of 51, on the same
  fixed request. It missed owners and whole actions.
- **The 3B fails on time.** p95 was 4.35 s to the caller on a request that carries no actions. A
  complete reply is about 400 tokens, and the 2.5 s target leaves room for about 225.
- **The copilot on Flash is no answer either.** Thinking off, it took p95 29.52 s on a quiet engine.
- **Live bug, separate from all of this: the copilot's request got no actions back from Flash.**
  0 of 22 replies carried `actions_upsert`, with thinking on or off. Step 3a marks the top-level schema
  keys required: 51 of 51 in the test, then 17 of 17 from the real request. It reached live in
  `server-v0.32.2` with no lab soak. See [the schema bug](#the-schema-bug).
- **I caused one outage.** The probe ran `jev-llm` out of memory and it restarted once. See
  [the incident](#incident-jev-llm-out-of-memory).

The findings from before step 3 follow.

- **`ollama` is at 0 by choice, not by accident.** On 2026-10-24 at 21:10 UTC the GPU dashboard
  switched the gaming PC to the `whisper` profile. It has stayed there for 8 days. Gaming mode is not
  the cause.
- **Nobody was told, and the switch was not free.** The card holds one pod, so `whisper` means no
  `ollama`, and the copilot has been falling back since then.
- **Restoring `ollama` would take away speech-to-text.** `whisper-cpp` on that card is the only GPU
  speech backend running today. See [Why ollama is at 0](#why-ollama-is-at-0).
- **Fallback costs about 2.3 s on every request, not just the first.** That is 14-38% of the 6-16 s
  measured. The rest is thinking and the queue, and the probe is needed to split them.
- **The 60 s cooldown the config describes never happens.** LiteLLM skips it for a model with one
  deployment. On a hung backend, every request costs about 42 s, not just the first two attempts.
- **A small model of our own remains the only route to 2.5 s under load without a Flash restart,**
  and a place on the engine is not enough. See [Reserved path](#reserved-path). Step 3 rules out the
  3B on the pve RTX 2070 as that model.

## Decisions

All four recommendations below were accepted on 2026-11-01. Step 3 has reported, and steps 4 and 5 do not go ahead: see [the steps](#proposed-steps-for-approval).

| # | Decision | My recommendation |
|---|---|---|
| 1 | `voice_timeout_s` against the 40 s path (decision 6 in the gateway plan) | **Keep 30 s, and shorten the route instead.** `fast-chat` gets `timeout: 5` and `num_retries: 0`. `ollama` must not return before that lands |
| 2 | Reserved path (decision 10) | **A separate small model**, alias `interactive`, falling back to `heavy` |
| 3 | What "first text" means for the copilot | **Total p95 under 2.5 s.** The copilot does not stream, so 0.8 s cannot apply. **Replaced by the REPLAN:** see [the done test](#the-done-test) |
| 4 | Alert window | **12 hours**, relying on Task 21's decision 3 (Prometheus off the gaming PC) |

**Answered 2026-11-01: the move to `jev` was not deliberate.** The gaming PC runs Kubernetes workloads
unless Andrew is gaming, so it stays best-effort. Speech goes back to pve, and `ollama` to the gaming
PC, after step 2. pve then runs Whisper plus `interactive`, 4 + 3 of 7 GiB, in a new profile, and Jev
stays on `jev-cpu`. The Whisper half stands. The `interactive` half now waits on a model, so the
interim runs longer. This replaces the `jev` beside `interactive` layout in
[the comparison](#the-comparison). The interim is `speech-jev`, which exists today.

## Why ollama is at 0

The Deployment's `replicas: 0` is rendered from `active_profile: whisper` in
`bare-metal/andrew-gamingpc/workloads.yaml`. Commit `0f47a75a`, author `dgx-spark-dashboard`, set it.
It is **a GPU profile: not gaming mode, and not an accident in the sense of a fault.**

The card takes one pod. `manifests/05-resourcequota-gpu.yaml` enforces this, and the quota is at 1 of
1 today, held by `whisper-cpp`.

Probably the switch was made to keep speech alive. This order is inferred, not documented:

| Date (UTC) | Change | Effect |
|---|---|---|
| 09-17 | Spark moved to Flash profiles | `vllm-whisper` at 0, and no Spark speech tier |
| 10-22 | pve moved to profile `jev` (`6ed742ae`) | `speaches` and `kokoro` at 0 |
| 10-24 21:10 | Gaming PC moved to `whisper` (`0f47a75a`) | `whisper-cpp` at 1, `ollama` at 0 |

All three STT tiers in `base/configmap.yaml` were checked: `spark` and `pve` have no pods, and
`gamingpc` is the only one running. It served requests in the last 24 hours.

### How often it happens

From Mimir, 10-minute samples, 2026-08-25 to today:

| Cause of `ollama` at 0 | Episodes | Time |
|---|---|---|
| `whisper` profile, this episode | 1 | 8 days and counting, 156 h of it in monitored time |
| `whisper` profile, trying it out, 09-16 and 09-17 | 2 | 12.7 h, then minutes |
| Gaming mode | 1 (10-23 13:40) | 0.7 h |
| Wanted 1, none available | Not itemised | About 17 h |

Overall, 0 available for 187 of 660 monitored hours (28%), nearly all of it this episode. Gaming mode
is rare: the `gaming` taint appears in one sample in about two months, though a session under
10 minutes could fall between samples.

**How often the PC sleeps is not established.** Prometheus and kube-state-metrics run on that node,
so when it stops, so does the evidence. Mimir has data for 668 of about 1,296 hours since 09-08, with
one hole of 22 days. A hole means the PC was off or monitoring was down, and the two cannot be told
apart. Task 21 reached the same limit. This machine's journal only goes back to its boot on 10-29 at
21:00 UTC, which matches the end of the last hole, and shows no suspend since.

## What the copilot's wait is made of

Fallback is now measured. Step 3 replaces the thinking estimate below: Flash wrote 423 tokens at p50 with reasoning off and still took 17.78 s, so the time is the engine's, not reasoning.

| Part | Size | Status |
|---|---|---|
| Fallback, `ollama` at 0 | **About 2.3 s, every request** | Measured on the production image, on a stub |
| Thinking | **Unbounded in practice.** With it on, 12 of 12 copilot requests hit a 60 s timeout | Measured in step 3. The 1.6 s estimate that stood here was wrong |
| Queue for a place | Median 18 s under normal load | Measured in Task 11, on Flash |
| Prefill freezes | Up to 3.4 s per step | Measured in Task 11 |

So the 6-16 s is mostly the shared engine. **Falling back lands on the same engine as the agents,**
because `heavy` and `qwen3.8-flash-next` are one vLLM. Restoring speed needs a way around that queue.

**Not read:** the copilot's own per-cycle timings. They are in `copilot_items`, kind `timing`, in the
live database, and my read was refused, so I did not pursue it. They would show the change at
10-24 directly. Prometheus has no use here: `copilot_roundtrip_seconds` shows no increase.

## LiteLLM, tested

Production image `1.104.2` by digest, the real config and real callbacks, against a local stub. No
engine, no cluster traffic. The stub answers in 50 ms. A closed port stands in for a Service with no
endpoints, and a port that accepts and never answers stands in for a hung backend. A sleeping node
drops packets instead, which should cost the same 20 s per attempt but was not tested.

| Test | Result |
|---|---|
| `heavy`, for comparison, n=40 | p50 58 ms |
| `fast-chat`, no backend, as configured, n=40 | **p50 2,329 ms**, max 2,839 ms |
| Same, `num_retries: 0` on the deployment, n=20 | **p50 72 ms** |
| Hung backend, as configured, 3 in a row | **51.3, 42.8, 42.3 s**: no cooldown |
| Hung backend, `timeout: 3`, `num_retries: 0`, 4 in a row | 9.3, 10.0, 3.07, 3.07 s |
| Hung backend, client timeout 30 s, as voice | **Gave up at 30.03 s**, before the fallback |

- **The 2.3 s is the retry.** Removing it takes the path to 14 ms over `heavy`.
- **The cooldown is skipped by design.** `cooldown_handlers.py` avoids cooling down a single-deployment
  model group. It would need a very high rate of total failure. The comment in
  `apps/ai/litellm-proxy.yaml` saying later requests go "straight to the fallback" is wrong.
- **This clears the `fast-chat` route.** `ModelNotMappedError` appears twice per attempt, but it is
  noise. With retries off, the route adds 14 ms, callbacks and pre-call checks included.
- **Unexplained:** the first requests after a boot were slow, 9-19 s. It did not recur after that,
  and may be this test's lack of internet access. I did not chase it.

## `voice_timeout_s`

**Decision: keep 30 s, and fix the route.** Raising it to 45 s or more would make someone wait for
45 s to hear that nothing was understood, against a 2.5 s target.

- **Today this is not urgent.** With no backend the path is 2.3 s, because the failure is immediate.
- **It matters when `ollama` is back and the PC stalls.** Each request then costs about 42 s, with no
  cooldown to end it, so voice gives up at 30 s and falls to `from_words`.
- **The window is bounded, but not by LiteLLM.** The Service loses its endpoint once the node is
  marked NotReady, normally within a minute, and it is back to the fast failure. A cold model load
  has the same effect and no such bound.
- **With `timeout: 5` and no retry,** the worst case is 5 s, leaving 25 s for `heavy`.

A risk to check when `ollama` returns: a 5 s timeout might cut off a cold model load, so each attempt
restarts it. Preload the model at startup if that happens.

## Thinking

**The fix:** pass `think=False` from the copilot, voice and ideas callers, with tests. All three share
the model and already open with `/no_think`. `complete_json` keeps its default.

**The setting survives `drop_params`.** Sent through the proxy, seen at the stub:

| Route | `chat_template_kwargs` at the backend | `response_format` |
|---|---|---|
| `heavy` | Present, unchanged | Present |
| `qwen3.8-flash-next` | Present, unchanged | Present |
| `fast-chat`, fallback to `heavy` | Present, unchanged | Present |
| `fast-chat`, `ollama` up | **Dropped** | Sent as `format` |

The drop on the Ollama route is harmless as far as I know, since `qwen2.5` is not a reasoning model.

**Tested in step 3, and #247 was worth it.**

- **With no `chat_template_kwargs`, Flash reasons, and `/no_think` does not stop it.** Every voice
  reply in arm V reasoned, 310 of 310 in its main result, spending about 79% of completion tokens.
  See `llm-path-latency.md`, [Reasoning](llm-path-latency.md#reasoning).
- **`enable_thinking: false` is honoured through the gateway.** The copilot's body, alternating
  requests on Flash, 19:32 to 19:51 UTC, n=12 each. On: 12 of 12 timed out at 60 s. Off: 12 of 12
  answered, none reasoned, p50 27.4 s, max 45.5 s, 479 tokens at p50.
- **That is why #247 earned its place,** and it is not enough. It took the copilot from timing out
  to answering in about 27 s. It did not cause the [schema bug](#the-schema-bug), which is there
  with thinking on as well.

## The comparison

Run 2026-11-01, a Sunday, with `scripts/llm_probe.py copilot`. Times are UTC. The real `COPILOT_SYSTEM`
prompt and `CopilotDelta` schema, thinking off, a replayed invented meeting
(`server/tests/evals/fixtures/planning_meeting.json`). Flash through the gateway, and the same 3B on
the same card by calling `jev-llm` directly, one request at a time. Nothing routes that way, and it
is a floor only.

**The normal-load gate failed, so this is a light Sunday engine, not normal load.** Run period 19:02:40
to 19:28:20, labelled `normal`. On gateway rows, the only ones that reach the sampled engine, 2.5% of
requests saw a queue against the 40% floor. `alone` passed, at 0.0% against the 40% ceiling. Engine p50
was 3 running and KV 26%. Failing here on a quiet engine means
Flash would do worse under load. For the 3B on its own card, the load on Flash does not apply.

**The run stopped at 81 of 200 rows,** at the [incident](#incident-jev-llm-out-of-memory). Prompts on
`jev-llm` were cut to a 200 s window, about 1,700 tokens, because its 2,048-token slot refuses a
2,500-token prompt: `request (2596 tokens) exceeds the available context size`. Flash took the full
2,769 at p50.

| | Restore the tier | Copilot on Flash, thinking off | Small model, 3B |
|---|---|---|---|
| Change | pve to `speech-jev`, PC to `ollama` | One config value | New `interactive` alias, new workload on the 2070 |
| Avoids the agents' queue | Yes | **No** | Yes |
| Depends on the gaming PC | **Yes** | No | No |
| n | Not measured, it has no backend | 40 | 41 |
| Failed | | 0 | **11:** 10 cut off at the slot's limit on both attempts, 1 the out-of-memory restart |
| Caller total p50 / p95 / p99, s | | 17.78 / **29.52** / 37.41 | 2.36 / **4.35** / 5.19 |
| First attempt p50 / p95, s | | Same as above | 2.10 / 2.64 |
| At a 2,500-token prompt, projected p50 / p95, s | | Measured at 2,769 | 2.42 / 3.93 |
| Requests over 2.5 s | | 40 of 40 | 19 of 41 |
| Rate | | 24 tok/s, whole request | 3,005 tok/s prefill, 132 tok/s decode, grammar on |
| Evals, schema fixed | | **37 of 51** | **18 of 51** |
| Reaches 2.5 s | No | **No** | **No** |

"Failed" is what the caller saw. The client retries invalid JSON once, 13 of 41 retried, and the caller
waits for both attempts, which is why p95 is 4.35 s and the first attempt's is 2.64 s. Schema enforcement
cost little: 132 tok/s decode against the 138 in `jev`'s logs.

**The rate estimate held. The reply size did not.** The earlier 1.9 s assumed a 150-token reply. Prefill
at 2,500 tokens takes about 0.8 s, which leaves 1.7 s, or **225 tokens**. Replies are bigger than that,
on both models:

| Reply, fixed schema, 51 replies each | p50 | p95 | Max | Within 225 tokens |
|---|---|---:|---:|---:|
| 3B | 408 | 679 | 2,442 | **1** |
| Flash | 359 | 780 | 931 | 9 |

So the 3B's 4.35 s flatters it. That run sent [the broken schema](#the-schema-bug). Its reply text was
not kept, but the same request in the evals got no new actions from the 3B. At the measured rates, the 3B's last 36 replies with the schema fixed would take p50 3.64 s, and 3 of 36
beat 2.5 s. That is arithmetic from token counts, not a timed run on the card.

A longer run would not rescue this. Without a cut-off, on a 4,096 slot on CPU, full-length prompts gave
replies of 53-57 or 560-667 tokens, nothing between. The short ones were a topics-only reply. 560-667
tokens is 4.2-5 s of decode on the 2070.

### Quality

Reply quality was the known unknown. **The 3B fails.**

The evals: the 3 original copilot cases, 6 new ones and 8 rolling cycles over one meeting, 17 cases,
with an answer key for action, owner and due date. All invented text. Each was run three times per
model with the fixed schema, and these ran on a local CPU, on the same llama.cpp build and the same
GGUF, because of the incident. The 4,096 slot helped the 3B: the pod's would have cut it off.

| Case | 3B | Flash |
|---|---:|---:|
| 1:1 status meeting | 3 of 3 | 3 of 3 |
| Vendor call, two owned actions | 0 | 3 |
| Solo brain dump | 3 | 3 |
| Long meeting, first 5 minutes | 0 | 2 |
| Long meeting, 8 minutes | 0 | 0 |
| Named speakers | 0 | 3 |
| Small talk, nothing committed | 0 | 3 |
| State carried, one new action | 0 | 3 |
| Correction updates the item in place | 2 | 3 |
| Rolling cycle 1, spare batteries | 0 | 3 |
| Rolling cycle 2, unit count | 3 | 3 |
| Rolling cycle 3, floor walk | 0 | 2 |
| Rolling cycle 4, agency temps | 1 | 3 |
| Rolling cycle 5, stock update module | 1 | 0 |
| Rolling cycle 6, feedback for Helen | 0 | 0 |
| Rolling cycle 7, Rosa's manager | 2 | 3 |
| Rolling cycle 8, vendor support | 3 | 0 |
| **Total of 51** | **18** | **37** |

By run, the 3B passed 5, 6 and 7 of 17, and Flash 12, 12 and 13.

- **The 3B loses owners and actions.** Of its failures, 25 were a wrong or missing owner, 25 a missing
  action, 5 too many actions and 3 an invented owner. Flash had 3, 11, 0 and 3.
- **It finds work in small talk,** in 3 of 3 runs. A copilot that files actions nobody took is worse
  than one that misses some.
- **It ran away once:** 2,442 tokens on a single reply.
- **The evals are not perfect.** Two cases fail on both models in every run, and Flash fails two more
  that the 3B sometimes passes, so some of that is the cases. Flash is the reference, and its 73% says
  the cases are hard. One case's key was wrong, and is fixed: see [the recheck](#recheck-2026-11-02).

### The schema bug

**Fixed in step 3a, and on live in `server-v0.32.2`, with no lab soak.** The copilot's request got no actions back
from Flash. Every top-level key in `CopilotDelta` was optional, and the structured output grammar lets a
model skip them. Flash skipped `actions_upsert`:

| Request, Flash | Replies | With `actions_upsert` | Evals passed |
|---|---:|---:|---:|
| As sent before 3a | 15 | **0** | 1 of 17 |
| Thinking on, as before #247 | 7 | **0** | 1 of 17, 10 timed out at 240 s |
| Top-level keys required, by a wrapper | 51 | **51** | 37 of 51 |
| **Step 3a, the real client, one run** | 17 | **17** | **13 of 17** |

- **#247 did not cause it.** Thinking on shows the same gap, though from only 7 replies.
- **It reached live.** `server-v0.32.1` and everything before it send this request. I have not looked
  at live sessions, so what users saw, and since when, is not established. The copilot's fall to Flash on 10-24 would be a natural
  start, since `ollama` has a different model and takes the schema as `format`, but that is a guess.
- **The evals missed it** because the live ones need `--live`, and only ran with no `think` argument
  until the step 3 PR. The action eval still ran with thinking on until 3a. Both now send what a copilot
  cycle sends, and a test holds them to it.
- **The 3B has it too.** As sent, it gave no new actions in any eval that expected them, and often
  only topics and a title.
- **The fix changes only what is sent.** All eight keys are required in the JSON schema, `title`
  included, which stays nullable. Validation is as tolerant as it was, so a route that does not enforce
  the schema can leave a key out and get its default. `FinalNote`, `VoiceRequest`, `IdeasDelta` and the
  nested models send what they sent. It is LLM output, so `protocol/` does not change.

**The live run, 2026-11-02, 17:04 to 17:08 UTC.** Once through the 17 cases, through the gateway to Flash,
thinking off, one request at a time, a 3 s pause between cases. The queue stayed at 0 throughout, so this
is an idle engine. Grading ignores the 8 s round-trip check, as 37 of 51 did. With that check, 7 of 17 pass.

- **13 of 17 sits at the top of the earlier runs,** which gave 12, 12 and 13, and every reply held all
  eight keys. 16 of 17 held actions, and the one empty list is the small talk case, which is correct.
  No retries, and none cut off.
- **Two cases moved.** Rolling cycle 5 passed, after 0 of 3. Solo brain dump failed, after 3 of 3: it filed
  the passport as a note, not an action, which is a real miss. It is one run, so neither is a trend.
- **The other three failures are the known ones:** 8 minutes, and rolling cycles 6 and 8.
- **Replies are longer, and that is step 3b's problem.** On the 15 cases that replied in both, tokens
  rose 28%, and 14 of the 15 got longer.

| Tokens per reply, Flash | n | p50 | p95 | Max | Within 225 |
|---|---:|---:|---:|---:|---:|
| As sent before 3a | 15 | 287 | 653 | 653 | 6 |
| Keys required, by a wrapper | 51 | 359 | 780 | 931 | 9 |
| Step 3a, the real client | 17 | 353 | 864 | 864 | 3 |

**Time.** p50 9.4 s, p95 22.0 s and max 22.0 s, at 39 tok/s. That is the idle engine's speed, not a new
result. It is no comparison with the 29.52 s above, which had longer prompts, and it still misses 2.5 s on
17 of 17. In the 51 required runs the engine gave about 8 tok/s, p95 was 127.7 s, and **19 of 51 took
over 60 s, which is `copilot_timeout_s`.** Those cycles would have been skipped on live.

#### What to watch on lab

**No soak ran.** Lab's logs held health checks only from #260's merge to the release, so `server-v0.32.2`
took 3a to live unsoaked. The first real session on live, a54a7906, gave 11 of 11 cycles ok, mean 8.2 s,
none over 15 s, 0 skipped and 0 invalid JSON. That is one quiet session, and actions and the title were
not checked.

R1's replay checked these rows on an idle engine: results in [The replay](#the-replay). They still need a
run under load. The first check is the one most likely to fail.

| Watch | Where | Pass |
|---|---|---|
| Cycles lost to the 60 s timeout | Log `copilot cycle skipped`, `timing` rows in `copilot_items` with `ok` false, `jarvis_live_copilot_roundtrip_seconds` with outcome `error` | **0 lost.** This is half of [the done test](#the-done-test) |
| Actions arrive | `snapshot` rows in `copilot_items`, for a session where work was promised | Actions appear. This is the fix |
| Retries | Log `invalid CopilotDelta JSON` | None or nearly none. Each one doubles the wait |
| Round trip | `rt_s` in the `timing` rows, or the session's timing summary | **p95 under 15 s,** the other half |
| Titles | Session titles | An untitled session still gets one, and a titled one keeps its own. The reply now always carries `title` |
| The model in use | `x-litellm-model-group` on a real request, and the Spark's active profile | Every reply from `qwen3.8-flash-next`. `ollama` no longer matters to the copilot. If the profile stops serving the alias, cycles fail: it [has no fallback](#the-copilot-on-flash) |

To roll back, redeploy lab's previous image with the *Server Image* workflow, or revert the PR.

### Recommendation

**Do not build `interactive` on the 3B. Fix the schema first, whatever the tier.**

- **The decided path stands, and the model does not.** The reserved path is still a separate small
  model, now for voice first. The 2.5 s test stays with voice: the copilot's moved in the REPLAN. The 3B
  simply is not that model.
- **Restore the tier is not the answer.** `qwen2.5:7b` has no backend to measure, and the gaming PC
  stays best-effort. The other two options are measured above.
- **Flash is the copilot's model, by name,** with thinking off. It is slow, and it answers well with
  3a. See [The copilot on Flash](#the-copilot-on-flash).
- **GPU move 2 is cancelled.** `ollama` stays at 0, so the worry that `fast-chat` would change model
  without saying so has gone, and the copilot no longer goes through `fast-chat` at all.

### What happens next

**The REPLAN took the decision away from this bar.** The copilot names Flash, and its done test is p95
under 15 s with 0 cycles lost, so no small model has to pass anything for the copilot to ship. Step 3b
stays as a record: [nothing passed](#step-3b-what-was-measured), and that blocks nothing.

**The bar as 3b measured it.** One model, on the 17 cases, three runs each:

1. **Quality:** at least 2 of 3 on each of the 11 cases Flash passes 3 of 3.
2. **Size:** replies at p95 within what 2.5 s allows on that model's card. On the 2070, that is 225
   tokens at 132 tok/s.

**What is left of it:**

- **The 11 cases still count.** If the replay fails on Flash, the copilot moves to pve's model only if
  that model passes them. R2 reports them.
- **The 225 tokens no longer applies to the copilot.** It came from the copilot's old 2.5 s. Voice keeps
  2.5 s on its own request, and R2 sets its own eval for that, with the 2070's speed still to be shown.

Both levers were tried, in this order:

1. **A smaller reply.** The caps and dropping `suggestions` and `proposals` halved the 3B's reply and
   still missed 225 at p95. Only a compact grammar fitted, and it lost the notes.
2. **A better small model.** Three candidates, all stopped in their second run.

**A small model on the Spark is out of the sequence,** because the REPLAN gives the next lever to pve's
whole 7 GiB card. Nothing was loaded there. If it comes back, it has to pass the 11 cases, fit the 0.06 to
0.10 of the memory budget that is free, and answer at a usable speed beside Flash. That speed, and
whether it needs a Flash restart, remain unknown.

### Step 3b: what was measured

**No model and no reply passed the bar.** Quality and size pulled against each other, and the best 3B
variants reached 6 of 11 while still over 225 at p95. Nothing here changes what lab or live sends.

All runs were local, on CPU, from 2026-11-02 17:40 UTC to 00:16 on 11-03, through the real client, with
one 4,096 token slot. The figures are from `tests.evals.reply_size report`, unless a line says it tallied
the rows' failure text. They are about quality and tokens only: no time here means anything for a card.
Rows hold whole replies, so they are not committed.

**The 3B, all 9 variants to the end, 51 runs each:**

| Variant | What changes | Passed | Bar cases | p50 | p95 | Max | Within 225 | Notes, suggestions per reply |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `today` | Control, the request a cycle sends | 17 | 5 | 395 | 967 | 2,442 | 2 of 52 | 2.4, 2.9 |
| `caps` | At most 2 notes, 2 decisions, 3 topics, 1 suggestion, 1 proposal | 22 | **6** | 303 | 404 | 446 | 8 of 51 | 1.5, 1.0 |
| `owners` | `owner` and `due` required in each action | 14 | 3 | 386 | 699 | 2,442 | 3 of 52 | 2.0, 2.8 |
| `no-sugg-prop` | No `suggestions` or `proposals` | 23 | **6** | 208.5 | 1,994 | 1,994 | 33 of 54 | 2.0, 0 |
| `no-sugg-prop-caps` | Both of those | 22 | **6** | 197 | **306** | 334 | 32 of 51 | 1.4, 0 |
| `slim` | That, and `owners` | 18 | **6** | 205 | 321 | 391 | 30 of 51 | 1.4, 0 |
| `slim-min` | That, and the prompt asks for one-line JSON | 12 | 2 | 145.5 | 283 | 1,977 | 49 of 52 | 1.0, 0 |
| `slim-owner` | That, owner first, and a prompt line on owners | 13 | 3 | 131 | 1,922 | 3,374 | 49 of 54 | 0.8, 0 |
| `no-sugg-prop-caps` + compact | No whitespace, by a llama.cpp grammar | 9 | 1 | 95 | **151** | 153 | 51 of 51 | **0**, 0 |

Passed is out of 51 and bar cases out of 11. Within 225 counts every attempt, so a retry adds a reply. `today` repeats the
step 3 result on the real request, at 5 of 11. Actions held at 1.6 to 2.2 per reply throughout, so no variant
lost them by count. The loss was in which actions and whose.

**What each costs the copilot:**

- **Dropping `suggestions` and `proposals` takes them out of every cycle.** No slower cycle exists, so
  this needs one built. None of the 17 cases grades either key, and `actions.py`, the eval that grades
  proposals, did not run on any variant, so the higher scores show nothing about what was given up.
- **Caps cost context.** Notes fell from 2.4 to 1.5 per reply and topics from 4.9 to 3.0.
- **Requiring `owner` invents owners.** Tallied from the rows, invented owner failures rose from 3 to 21.
  The prompt says not to invent them, and a required key pushes against that.
- **Compact JSON is the only reply that fits, and it fails.** It wrote no notes at all, and bar cases fell
  to 1. The grammar allows notes, and all three models that returned content under it wrote none. I do not know
  why. The model is not told about it, and a real request would need llama.cpp's `grammar`, which the
  real client does not send.

**Size was not the 3B's only problem.** Tallied from the rows, 4 of the 11 bar cases passed 0 of 27 runs
across all 9 variants: named speakers, small talk, state carried, and rolling cycle "Order the spare
batteries". They fail on wrong or missing owners, missing actions and, in small talk, an action nobody
promised. All 9 variants together reach only 7 of the 11, so no reply shape here gets the 3B past the bar.

**Candidates.** Each was stopped in its second run, once a bar case failed twice. The report prints 0 of 11
for all of them only because a case needs three runs to count, so read Passed instead.

| Model, Q4_K_M | Variant | Passed | p50 | p95 | Within 225 | Stopped on |
|---|---|---:|---:|---:|---:|---|
| `qwen35-4b`, 2.5 GiB | `today` | 6 of 18 | 503.5 | 768 | 2 of 18 | 1:1 status meeting |
| | `slim-min` | 8 of 20 | 200.5 | 386 | 15 of 20 | Solo brain dump |
| | `slim-owner` | 6 of 18 | 179 | 405 | 16 of 18 | 1:1 status meeting |
| | `no-sugg-prop-caps` | 4 of 19 | 260 | 557 | 6 of 19 | Vendor call |
| | That + compact | 5 of 18 | 52.5 | 170 | 18 of 18 | 1:1 status meeting |
| `ministral3-3b`, 2.0 GiB | `today` | 4 of 18 | 338 | 855 | 2 of 18 | 1:1 status meeting |
| | `slim-min` | 4 of 18 | 242.5 | 532 | 6 of 18 | 1:1 status meeting |
| | `slim-owner` | 2 of 18 | 230 | 416 | 7 of 18 | 1:1 status meeting |
| | `no-sugg-prop-caps` | 6 of 20 | 255.5 | 461 | 6 of 20 | Solo brain dump |
| | That + compact | 4 of 19 | 105 | 152 | 19 of 19 | Vendor call |
| `lfm25-26b`, 1.6 GiB, `--reasoning off` | `today` | 3 of 18 | 2,458 | 3,243 | 0 of 29 | 1:1 status meeting |
| | `no-sugg-prop-caps` | 9 of 23 | 2,168 | 2,695 | 0 of 31 | Named speakers |
| | That + compact | 0 of 18 | 60 | 180 | 36 of 36 | **Void:** every reply empty |

**Ranked by these rows, not by any vendor's figures:** `qwen35-4b`, then `ministral3-3b`. Neither beat the
3B, whose 22 of 51 is the only full result. Both invented or dropped actions on bar cases that the 3B passes,
such as the 1:1 and the solo brain dump. These ranks rest on partial runs of 18 to 23 cases. The session that
chose the candidates left no reason for them, so all I can say is that they are small enough for the 3 GiB the
2070 then had to spare.

**`lfm25-26b` is not ranked, because reasoning never turned off.** Its first three series, on the default
server, were excluded as reasoning, and these are the rerun meant to fix that. It did not:

- **`--reasoning off` is not enough for this template.** The server logged `thinking = 0`, and its sample
  prompt still ends in `<think>`. The log holds no generated text, so this is inferred from the rows.
- **The rows agree.** Its stored replies hold a median 0.2 characters per token, where the others hold 3 to 4.
  On `no-sugg-prop-caps`, 12 of 31 attempts held no content and 13 hit the slot's limit.
- **So its token figures are not reply sizes,** and its size is unmeasured. Its 9 of 23 is the best rate
  here, but it stopped on real misses in finished replies, so it does not pass either. R2's brief turns
  reasoning off at the server: this template needs more than that.
- **Its compact row measures nothing.** The grammar forces JSON inside the open block, so all 36 replies
  came back empty, and that is a failure of my run and not of the model.

**Pins.** `ghcr.io/ggml-org/llama.cpp@sha256:33868c035b21dc63f7c60b7438774283fd99215bc319114eb03de5df4ce7cd6b`.

| Model | Repository, `Q4_K_M` | SHA256 |
|---|---|---|
| `q25-3b` | `Qwen/Qwen2.5-3B-Instruct-GGUF` | `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d` |
| `ministral3-3b` | `mistralai/Ministral-3-3B-Instruct-2512-GGUF` | `9ed150d4367e68df0ac8e1540f6ddc65b42d0ee26378329d1ecbca60f93fc5f8` |
| `qwen35-4b` | `lmstudio-community/Qwen3.5-4B-GGUF` | `25082a7dd3776cc3c741c6347d3bd04523f05796607b3fbc32fa3a25dfa1418c` |
| `lfm25-26b` | `LiquidAI/LFM2.5-2.6B-GGUF` | `02a8b7e17487d326e46d68ce0ba24211e1b80a14c4cd0597fa73c1cd697f52ed` |

**Limits and what stays open:**

- **Every result is about these small models.** They were picked for 3 GiB. R2 has 7 GiB, so this says
  nothing about a larger model.
- **No variant ran on Flash.** The rule was CPU only. Whether `no-sugg-prop-caps` would shorten Flash's reply
  is unmeasured, and R1's replay left only 0.25 s of margin on an idle engine. Trying it would be a later
  PR, with `actions.py` run and a slower cycle for what it drops. Nothing here builds it.
- **`lfm25-26b`'s reply size** needs a template that does not open the block. That is untested, and it is
  R2's to try only if R2 picks a model with the same template.
- **Nothing here needs a decision.** The Spark question above is parked with the REPLAN, and whether the
  copilot leaves Flash waits on R1's replay under load and R2's model.

### What step 4 must measure

Step 3 cannot measure these, because the workload does not exist. If a model clears the bar, step 4
owes:

| Unknown | Why not now | How step 4 measures it |
|---|---|---|
| JSON schema cost on a dedicated pod | `jev-llm` showed 132 tok/s, but with the 2,048 slot, and on Jev's pod | `llm_probe.py copilot` against the new pod, comparing `timings` with and without `response_format`, at a 2,500-token prompt |
| Contention from Whisper on the shared card | Whisper is not on pve yet, and the plan puts both on one 2070 | The same probe during a `jarvis_live/replay.py` session, which streams a WAV through the server to Whisper, then with Whisper idle. Report decode rate and total p95 for each |
| Memory under long prompts | [The incident](#incident-jev-llm-out-of-memory) | A soak of at least an hour at one prompt per 25 s, with `--cache-ram` bounded. The probe now records RSS in its rows and stops at `--memory-stop-mib`: see [the recheck](#recheck-2026-11-02) for what is unproven |
| Slot size | 2,048 refused the prompt | At least 4,096 per slot. Set `--parallel 1` |

The build itself stays as planned, for whichever model clears the bar:

- **Keep it apart from `jev`.** Jev's own pod guards `pi-durable`'s bash, so it must not share with a
  chat caller. Use a separate pod, with `fallbacks: {"interactive": ["heavy"]}`. That obeys the rule:
  fast to heavy only, never the reverse.
- **A new alias, not a re-pointed `fast-chat`.** Otherwise `auto` would send Open WebUI's simple chats
  down the reserved path. `fast-chat` stays as the best-effort tier it was.

### Incident: jev-llm out of memory

**My probe caused this.** `jev-llm` guards `pi-durable`'s bash, and I took it down once.

| Time | Event |
|---|---|
| 19:02 | Run starts. RSS, flat at 1,500 MiB for 8 days, starts climbing at about 90 MiB/min, about 45 MiB per long request |
| 19:28:20 | Reaches the 4 GiB limit. OOMKilled, exit 137. The probe stops itself on connection refused |
| 19:28:44 | Listening again, after about 24 s. Restart count 1 |
| 19:28:54 | One Jev triage logged `LiteLLM unreachable: timed out`. Retried at 19:30:14 and passed |

- **Impact:** one failed triage, which its retry recovered. I found no denied bash in the `pi-durable`
  logs I searched, and `pi-durable-0` did not restart. Jev-sized calls during the run were p50 343, p95
  387 and max 718 ms, against a baseline of 346, 384 and 389 ms.
- **The cause of the growth is not proven.** It lines up with long prompts, and llama.cpp's host prompt
  cache is my suspect, but its log shows no cache lines. It started and stopped with my run, and I stopped
  all traffic to `jev-llm` afterwards. All later 3B work ran locally.
- **Why the guard missed it:** it watched the queue, as the task asked. Memory was a different failure,
  and I was not watching it. The probe now stops on RSS: see [the recheck](#recheck-2026-11-02).
- **State now:** healthy at 22:35. RSS was 982 MiB. `kubectl top` shows 2,623 MiB, mostly the mapped
  model file: anon is 606 MiB. There were no restarts after that one.
- **For step 4:** at one prompt per 25 s a pod would grow about 108 MiB/min, which reaches a 4 GiB
  limit in about 25 minutes. Hence the soak above.

### Limits of step 3

- **The gate failed, so no result here is a normal-load number.** The 3B's failure does not rest on
  load, and Flash's holds a fortiori.
- **n is 81, not 200.** Enough for these gaps, and not for a tight p99.
- **The 3B's quality ran on CPU.** Same build and model, a larger slot, and quality only: no latency in
  this document comes from that run.
- **The 3B's fixed-schema latency is projected,** not timed on the card, since I sent nothing more to
  `jev-llm`. The projection leaves out grammar start-up and retries, so it leans low.
- **Each case is a single invented meeting.** They are written to be hard, and not drawn from real
  sessions. They compare models. They do not predict a real user's hit rate.
- **Cases were seen before the arms ran,** and the checker changed twice: to accept a resolved ISO date
  on the right weekday, and to [tighten two keys](#recheck-2026-11-02). All arms were rescored both
  times, from stored replies.

### Recheck, 2026-11-02

After the review of #254. No inference and no probe runs. Every figure here comes from the rows stored
on 2026-11-01.

**The 3B verdict stands, and step 4 stays stopped.** Both gate conditions come out as before, and the
3B's eval score does not move. Flash's rises by 2, which widens the gap. The time failure comes from
timings and token counts, and nothing here touches those.

**The gate.** The reported figures counted all 81 rows, but load is sampled on Flash and direct rows go
to `jev-llm`, so they diluted `queued` and counted as `alone` for nothing they did. All 12 `alone` rows
were direct. The copilot report now gates on gateway rows. `voice_report` is unchanged, since both its
paths reach the sampled engine.

| Condition | All 81 rows | Gateway rows, 40 | Limit | Result |
|---|---:|---:|---:|---|
| Queued | 1.2% | **2.5%** (1 of 40) | At least 40% | Fails, as before |
| Alone | 14.8% | **0.0%** (0 of 40) | At most 40% | Passes, as before |

**The evals.** The Priya action matched on the bare word `lakeside`, so `find_action` could return
another owner's action and fail a correct reply. It now needs `count`, and the rolling key lost `unit`.
The rows hold all 102 replies, 51 per model, so I regraded them. As a control, the unchanged checker
gave 18 and 35 again, matching every case and every run.

| Case | 3B | Flash |
|---|---:|---:|
| Long meeting, first 5 minutes | 0 to 0 | **0 to 2** |
| Long meeting, 8 minutes | 0 to 0 | 0 to 0 |
| Rolling cycle 2, unit count | 3 to 3 | 3 to 3 |
| **Total of 51** | **18 to 18** | **35 to 37** |

- **Flash's 0 of 3 on the first 5 minutes was mostly the key.** In all three runs Priya's action was
  there with the right owner, behind a Tomas action that mentions Lakeside. One of the three still
  fails, on the survey, which it really missed.
- **Flash's 0 of 3 on 8 minutes is real.** All three runs miss both the survey and the module price.
- **The 3B's 0 of 3 on both is real.** It leaves Priya's owner out or drops the action. In one run it
  wrote Tomas and Priya into one action with no owner.
- **The bar is unchanged.** The same 11 cases are 3 of 3 for Flash, and the 3B reaches 5 of them.

**The probe.** All this is tested on a local stub only. The copilot arm now:

- Fails closed on a guard read that goes wrong in any way, where `http.client.IncompleteRead` or a bad
  slot used to end the guard's thread and leave the run going.
- Allows for its own request, so `--parallel 1` no longer trips it. On one slot it can only see a queue
  between its requests. While its own request holds the slot, it cannot.
- Stops on a restart, seen in `/slots` as task ids going backwards. That comes after the damage.
- Stops at an RSS ceiling, and refuses a small model off loopback without one. Rows and the report
  carry RSS.

**How RSS is read.** It needs no new access and no new dependency, with one thing unproven. llama.cpp
does not report it, and the probe's service account cannot read pods or their metrics. The Prometheus
Service refused the probe's pod at 22:35 on 11-01, and its NetworkPolicy does not admit that namespace.
The same pod had read it at 19:29, four minutes before the Prometheus pod was replaced, and I cannot
explain that. The existing Ingress answered the query from this machine on 11-02, with samples 13-19 s apart,
so `--memory-url` points there. **A read from inside a pod is untested,** because testing it creates a
pod. If it fails, the probe refuses to start.

**Still needs a live run, and not done:**

- The memory guard from inside a pod, and against a pod growing for real.
- The soak in [what step 4 must measure](#what-step-4-must-measure). The growth is still unexplained,
  so the ceiling only stops a run in time. It does not make one safe.
- A copilot run that clears the gate. No copilot figure here is a normal-load number.
- The live evals with the tightened keys, three runs each. These scores are stored replies, regraded.
  Step 3a ran them once on Flash, see [the schema bug](#the-schema-bug). The 3B has not run them.
- The 3B's fixed-schema latency, which is still projected.

`run`, `voice` and `load` have none of these stops. The script's header says so, and they must stay off
a model that guards anything.

## Reserved path

**Recommendation: the separate small model above. Hold the agent cap in reserve.**

Today there is no limit to build on. `docs/pi-durable.md`, the StatefulSet's env, the running pod and
`dpi-backend`'s source have no LLM concurrency limit. `max_parallel_requests` appears nowhere in
`apps/ai/litellm-proxy.yaml`. Confirmed.

| | Cap in `pi-durable` | Per-alias cap in LiteLLM | **Separate small model** |
|---|---|---|---|
| Where | Fork change, new image | One config value | New alias and workload |
| Agents over the cap | **Wait in order** | **Get a 429** | Unaffected |
| Flash restart | No | No | No |
| Own KV cache | No | No | **Yes** |
| Freed from 3.4 s steps | No | No | **Yes** |
| Reaches 2.5 s alone | No | No | Not with the 3B, measured. Unproven for any other model |

**LiteLLM can cap aliases that share a backend. It was tested, and it is not a queue.** With
`max_parallel_requests: 2` on `qwen3.8-flash-next` and 6 streams at once, 2 ran and 4 received 429
("all max_parallel_requests slots in use") after 2-2.6 s. The slot is held for the whole stream. On the
same backend, `heavy` ran all 6, so a cap is per alias with no shared pool. The cap would go on
`qwen3.8-flash-next` and `local-default`. It needs no Flash restart, only a proxy restart.

The catch is the demand. At 7.4 in use, a cap of 6 keeps sending the extra requests a 429, so agents
depend on client retries with up to 8 s of backoff. `pi-ai` retries 429s, but I did not establish how
many times, so a queue would become errors at some rate. **Do not use this cap for agents.**

**A free place alone is not enough.** KV cache above 90% in 59% of samples matters, for four reasons:

1. **A blocked head stops the queue.** vLLM 0.29.0 `scheduler.py`: when the request at the head of
   the waiting queue cannot get blocks, the loop breaks, with free places or not. A short prompt behind
   a long one waits. A cap upstream helps by keeping that queue empty.
2. **The newest request is preempted first.** Under FCFS, when a running request needs blocks, vLLM
   takes `self.running.pop()`. That is usually the short interactive request that just arrived.
3. **Count does not bound memory.** In today's logs, KV was 15% with 1 running and 41% with 2 (n=121
   and 50, the same few sessions). Five such agents fill it, so a cap of 6 can still saturate.
4. **The step stays 3.4 s.** With a place but no chunk change, a 30-token reply takes about 41 s.

Task 11 reached the same point: a cap and step 3 together might reach the SLO, near its edge, at the
cost of a Flash restart. The small model avoids all four.

**What a cap would cost agents.** Reasoning, not measured: prefill is limited by tokens per step, not
requests, so a cap of 6 should cost little prefill and up to about 19% of decode at peak (6 of 7.4),
with longer queues in `pi-durable`. If you want a cap anyway, say for fairness or step 3, use the
`pi-durable` one, so that agents wait rather than fail.

**Effect on step 3:** with this path, step 3 is no longer needed for interactive callers. Weigh it for
agents alone.

## The copilot on Flash

R1 of the REPLAN. Set `JARVIS_LIVE_COPILOT_MODEL` to `qwen3.8-flash-next` in `base/`, so lab and live both
get it. Thinking stays off, and no serving argument changes.

**What it changes.** The copilot already reached Flash, through `fast-chat`'s fallback. It now skips the
failed attempt, which looks to cost about 0.6 s since homelab #838. It is the same engine and the same queue, so **this does not
make the copilot faster under load.** It makes the choice visible, and `ollama` can no longer change the model.

**Other callers follow it, because their settings are empty:**

| Caller | Channel | Effect |
|---|---|---|
| Voice, `VOICE_MODEL` empty | **Lab only:** `VOICE_ENABLED` is false in base | Same model and queue, minus the failed attempt. It still has 30 s, and the voice arm's p95 sat at that limit, so spoken requests can still give up and fall to `from_words`. New actions read `voice:qwen3.8-flash-next` |
| The ideas pass, `IDEAS_MODEL` empty | **Lab and live** | The same, with 120 s. R1's brief did not name this one |
| Copilot action proposals | Where actions are on | `proposed_by` reads `copilot:qwen3.8-flash-next` |

**`scripts/llm_probe.py` still defaults to `fast-chat` as "the alias voice sends".** That is now wrong for
the voice arm's rerun, so pass `--model`.

**The gateway keeps both settings.** From lab's pod on `server-v0.32.2`, 2026-11-02, the real client and the
real `CopilotDelta`, on a 736-token prompt. This confirms the route, and is not a load figure:

| Request | n | Group and model answering | Retries, fallbacks | Reasoning tokens | Completion | Time |
|---|---:|---|---:|---:|---:|---:|
| As the copilot sends it | 2 | `qwen3.8-flash-next` | 0, 0 | **0** | 167, 164 | 7.5, 6.6 s |
| Control, `chat_template_kwargs` removed | 1 | `qwen3.8-flash-next` | 0, 0 | **1,495** | 1,734 | 85.5 s |

The control shows the setting is what turns reasoning off, and that a silent drop would have been seen.
All eight keys were sent as required.

**What this gives up: the alias has no fallback.** `fast-chat` falls to `heavy`, and `heavy` to
`qwen-coder-32b`. `qwen3.8-flash-next` is in neither `fallbacks` list, and takes the global `num_retries: 1`.
`flash-embed`, active today, and `flash-solo` serve it. On any other Spark profile `heavy` would keep
answering and this alias would not, so every copilot cycle would be skipped until the profile comes back.
While Flash is simply down, little is lost, since `heavy` is the same engine. Adding a fallback is a
proxy change that reaches every client of this alias, including `pi-durable`, so R1 does not make it.

**Also behind `main`:** the Grafana title moves to 15 s here, and the homelab keeps a copy of that
dashboard. Its `docs/jarvis-live.md` still names `fast-chat`.

### The replay

**Is p95 under 15 s with 0 cycles lost? On this run, yes: p95 14.75 s, 0 of 24 lost. The done test is
still open,** because the engine was idle. This run failed the load gate, so it says nothing about
agents being busy, and it only just cleared on an empty engine.

`jarvis_live/replay.py` against lab on 0.33.0 (`329478f`), session `45a6f296`, at real-time speed. The
invented planning meeting from `planning_meeting.json`, 13 minutes, synthesised to speech and sent
through real Whisper (`gamingpc`, all 71 segments) as one mic channel, untitled.

**Load period: Monday 2026-11-02, 23:24:42 to 23:37:53 UTC, 17:24 local, after the working day.**

| Gate, from Flash's metrics | Result | Limit | |
|---|---:|---:|---|
| Cycles that saw a queue | **0 of 24, 0%** | At least 40% | **Fails** |
| Cycles alone on the engine | **22 of 24, 92%** | At most 40% | **Fails** |

The engine averaged 0.4 running, peaked at 2, never had a request waiting, and KV averaged 1.6%, peak 8%.
These are 57 samples, 15 s apart, from Prometheus, not the probe's per-request sampler. A cycle was bucketed
with the probe's rule, on the samples from 15 s before it started to 15 s after it ended.

| Copilot cycles | Result |
|---|---:|
| Cycles, ok | 24, 24 |
| **Lost, to `copilot_timeout_s` or anything else** | **0** |
| Retries, `invalid CopilotDelta JSON` | 0 |
| JSON failures | 0 |
| p50 | 10.23 s |
| **p95, nearest rank** | **14.75 s** |
| Max | 16.75 s |
| Over 15 s | 1 of 24 |
| Notes refreshed, p50, p95, max | 31.2, 36.8, 38.1 s |

**The margin is thin.** p95 is the second slowest of 24, 0.25 s under the bar with nothing else on the
engine, so one more slow cycle fails it. Under load, Task 11 measured a median 18 s queue for a place, and
step 3 saw 19 of 51 replies pass 60 s. I expect this to fail under load, and only a busy run can show that.

**What to watch on lab, row by row:**

| Watch | Result | |
|---|---|---|
| Cycles lost to the 60 s timeout | 0 of 24 in `timing` rows, the log and the metric | Passes, idle only |
| Actions arrive | 16 in the last snapshot, the first by the 4th. All 8 distinct actions in the key arrived | Passes, with noise below |
| Retries | 0 | Passes, idle only |
| Round trip | p95 14.75 s | Passes, idle only |
| Titles | The untitled session named itself "Scanner Rollout & Returns Backlog". A session opened as "R1 titled check" kept that, over 2 cycles | Passes |
| The model in use | Confirmed on real requests [above](#the-copilot-on-flash), and the pod sends the alias. The replay's own replies were not inspected for it | Passes |

**The actions are complete but noisy.** Twice as many arrived as the key has. The agency booking appears
three times, once due the wrong day, and the pick screen question twice with different owners. "Team" and
"me" stand in as owners on some. One session, not an eval.

**Two things the replay found:**

- **The copilot's SLO is not being judged, at 8 s or 15 s.** R3's probe bug has all three tiers reading
  unhealthy, so `stt_up` is false and `slo.py` skips every live check. During 24 cycles the only breach gauge
  was `finalize`. So raising the limit has changed no alert yet, and the timing summary is the only
  check that applies it. Live runs the same code and tiers, though I did not read live. R3 fixes the probe.
- **Staleness will start alerting when it does.** Its p95 here was 36.8 s against a 35 s limit, on an idle
  engine. It only defers to the round trip when that is breached, which it was not. Either the limit moves
  or expect a push from ordinary sessions. Nothing was changed here.

**Release.** #268 merged 75 s after #267, before this replay, so `server-v0.33.0` went to live first. Asked
during promotion, Andrew let it continue. The hold on release no longer applies to this change, and
rollback to `server-v0.32.2` is the *Server Image* workflow.

**Still owed:** the same replay on a weekday with agents busy. If it passes the gate and misses the bar,
the next lever is pve's model, from R2. `fast-chat`-era numbers for comparison were not taken, so this
run does not show what naming Flash changed. Andrew's own recording did not reach lab, so it is not here.

## R2: the model for pve's 2070

**No candidate passes voice, on today's request or on the eval-only request variant, so R2 names no
model that clears the bar.** The nearest is `gemma4-e4b`. On the variant it failed only one case, by
filing an already-proposed task again with a due date nobody said. Whether it reaches 2.5 s on the card
depends on which speed estimate holds, and they disagree. Under the brief, no candidate went on to the
copilot cases, so none can be the copilot's fallback yet.

Run 2026-11-03, 01:40 to 05:16 UTC, local CPU only, through the real client, one 4,096-token slot per
server. No Flash, no gateway and no pods. Figures are from `tests.evals.voice report` unless a line says
otherwise. Rows hold whole replies, so they are not committed.

### The voice eval

No voice eval existed. `server/tests/evals/voice.py` and `voice_cases.py` send what
`VoicePass._interpret` sends: the real `VOICE_SYSTEM`, `voice_user` and `VoiceRequest`, thinking off.
They grade what the server would act on: `build_payload` decides whether a proposal is filed, and an
off-list repo counts as none, as `IdeasState` stores it. The 25 invented requests cover all four targets,
with speech-to-text slips, a take-back, an instruction to Jarvis said inside a request, a comment on a
repo that is not allowed, and a repeat of something already proposed. Today is a fixed Wednesday,
2026-11-04, so a key can name the date a spoken day falls on.

**The bar was committed in af416ec, before any model ran**, and has not changed. Three runs of all 25:

1. Every case passes at least 2 of 3.
2. The 4 critical cases pass 3 of 3. These are the two take-backs, the instruction to Jarvis, and the
   comment on a repo that is not allowed.
3. At least 90% of all runs pass.
4. No reply is cut off.

Time is judged separately, because CPU times say nothing about a card. Reply p95 at the candidate's
estimated 2070 rates, counting every attempt, must be within 2.5 s. A candidate stops on quality only.

### Candidates

Three Q4 files that fit 7 GiB with a slot, chosen on 3b's evidence. 3b's small models were Qwen 3.5,
Ministral 3 and LFM 2.5. LFM's template would not turn reasoning off, so I left that family out. The
other two families moved up to the largest size that fits, and Gemma 4 came in as a third family:

| Model | Repository and file | Weights | KV, 1 slot / 2 slots | Compute | Total, 1 / 2 slots |
|---|---|---:|---:|---:|---:|
| `qwen35-9b` | `lmstudio-community/Qwen3.5-9B-GGUF`, `Qwen3.5-9B-Q4_K_M.gguf` | 5,356 MiB | 128 / 256 | 116 | **5.47 / 5.59 GiB** |
| `ministral3-8b` | `mistralai/Ministral-3-8B-Instruct-2512-GGUF`, `Ministral-3-8B-Instruct-2512-Q4_K_M.gguf` | 4,919 MiB | 544 / 1,088 | 128 | **5.46 / 5.99 GiB** |
| `gemma4-e4b` | `google/gemma-4-E4B-it-qat-q4_0-gguf`, `gemma-4-E4B_q4_0-it.gguf` | 4,901 MiB | 104 / 208 | 112 | **5.00 / 5.10 GiB** |

Memory is from each server's load log on CPU at `--ctx-size 4096` per slot. The CUDA context is not in
it, and is unmeasured. The jev manifest allows about 0.9 GiB over weights and KV for the 3B, so all three
should fit 7 GiB with two slots. That is unproven until R5 loads one.

**Thinking.** All three honour `enable_thinking: false`, so none needed the server-side switch. Every
reply held 0 reasoning characters, at 3.1 to 3.8 characters per token, where 3b's LFM held 0.2.

**Speed on the 2070 is estimated, not measured.** The only rate measured on the card is the 3B's on
`jev-llm`: 3,005 tok/s prefill and 132 tok/s decode, with the grammar on. I scaled it two ways. Both are
unproven:

| Model | Decode, by CPU ratio | Decode, by file size | Reply p95 at each, today | Decode needed for 2.5 s |
|---|---:|---:|---:|---:|
| `q25-3b`, control | 132, measured | 132 | 1.74 s | 92 |
| `qwen35-9b` | 50 | 49 | 4.73 / 4.98 s | 98 |
| `ministral3-8b` | 54 | 53 | 4.13 / 4.15 s | 89 |
| `gemma4-e4b` | **86** | **54** | **2.34 / 3.70 s** | 80 |

- **The CPU ratio** is each model's decode rate against the 3B's, on the same machine, 12 threads and
  image. It held at 21.7 and 22.1 tok/s for the 3B across the two runs. The file-size scaling assumes
  decode reads every weight once per token.
- **They agree for the Qwen and the Ministral, and both miss.** At 50 to 54 tok/s, their replies of about
  200 tokens at p95 take over 4 s.
- **They disagree for Gemma.** Its file holds per-layer embedding tables that a token reads only in part,
  so file size probably overstates its work. That is my explanation, not a measurement. Only R5's card
  can say whether 86 tok/s holds.

### Results

**Today's request.** Every candidate stopped in its first or second run:

| Model | Passed | Stopped on | Reply tokens p50 / p95 |
|---|---:|---|---:|
| `q25-3b`, control, all 3 runs | 44 of 75, 15 of 25 cases | Not stopped: a control | 153 / 206 |
| `qwen35-9b` | 12 of 17 | Instruction to Jarvis, answered `none` | 123 / 221 |
| `ministral3-8b` | 11 of 17 | Instruction to Jarvis, answered `none` | 123 / 198 |
| `gemma4-e4b` | 23 of 29 | Repo not clear, 2 of 2 | 124 / 175 |

The same misses came up on every model, and two of them are faults in the request, not in a model:

- **Today has no weekday.** "Friday" became 2026-11-07, a Saturday, from all three. `voice_user` sends
  only the ISO date.
- **`ProposalIn` is one flat shape with every field optional.** The grammar lets a model send a
  `git.pr_comment` with no `body`, or a reminder with `text` and no `title`, and the server refuses both.
  All three did this on the PR comment.
- **The repo list's order decides the repo.** When the request names none, every model picked
  `jarvis-voice`, which live lists first, where the prompt asks for null. A probe with the list reordered,
  4 asks per order, showed it is the order, not the word "Jarvis". The 3B took the first repo 12 of 12. Qwen
  and Gemma gave null 8 of 8 when homelab came first. On 2026-11-03 you kept this case gating.

**The variant, `named-day+kinds+repo`.** Eval-only, never sent by the server. It adds the weekday to
Today, sends each spoken kind with the fields the server needs, and rewrites only the prompt's repo line:
a listed repo only if the request names it or says what it is for, otherwise null. Same bar:

| Model | Passed | Stopped on | Reply tokens p50 / p95 | 2070 p95, CPU ratio / file size |
|---|---:|---|---:|---:|
| `q25-3b`, control, all 3 runs | 50 of 75, 17 of 25 cases | Not stopped | 162 / 222 | 1.83 / 1.86 s |
| `qwen35-9b` | 15 of 17 | Instruction to Jarvis, answered `none` | 136 / 218 | 4.59 / 4.94 s |
| `ministral3-8b` | 13 of 17 | Instruction to Jarvis, answered `none` | 114 / 219 | 4.46 / 4.56 s |
| `gemma4-e4b` | **42 of 44** | **Already proposed, 2 of 2** | 129 / 188 | **2.41** / 3.95 s |

- **The variant fixed what it targeted on Gemma.** It passed every case in run 1 except the repeat,
  including the PR comment, both reminders and the unclear repo. In run 2 it passed 18 of 19, missing
  only the repeat again.
- **Gemma's miss is real.** Both replies filed "Review the pricing proposal" again with a due date of
  2026-11-04 that nobody said. That differs from the stored proposal, so the server's dedupe would not
  catch it, and you would get a duplicate to reject.
- **The Qwen and the Ministral still miscount weekdays** with the weekday given: "Friday" became the 7th
  or the 8th.
- **The 3B is a control, not a candidate.** It fits the time and fails quality on both requests. It
  turns spoken cloud sessions into actions, and files a session it was told to scratch.

No candidate passed voice, so none ran the 17 copilot cases. That follows your answer of 2026-11-03.

### What you have to decide

1. **Whether the request changes go to the server.** On today's request no model passes. The weekday and
   the per-kind schema fix faults that every model hit. The repo line is a prompt change, measured only
   here. It would be a `feat(server)` PR with tests, and R2 does not make it.
2. **What to do about the repeat.** Either keep the bar, in which case no model is named, or accept a
   duplicate proposal that you reject by hand. Or make the server's dedupe ignore an unspoken `due` for
   voice, which is a server change I have not tested.
3. **Whether R5 measures `gemma4-e4b` on the card anyway.** Its speed estimates are 86 and 54 tok/s
   decode, against 80 needed, so only the card can settle it. R5 also owes memory under an hour of load and
   the slot count for both callers.
4. **Whether Gemma runs the copilot cases.** It has not, by the stop rule. The copilot can fall back to it
   only after it passes the 11 bar cases.

### Limits

- **Every speed on the 2070 is an estimate.** Prefill and decode on the card, grammar cost and the CUDA
  context are R5's to measure.
- **Stopped runs are partial.** 17 to 44 rows per candidate. Qwen and Ministral stopped on the instruction
  case, where `none` files nothing: the bar calls that critical because the key expects the task. Both
  also missed ordinary cases in the same run, such as the weekday, so they would probably have failed
  anyway. A stopped run cannot show that.
- **The repeat case does not model the server's dedupe.** A verbatim repeat with no `due` would be dropped
  by the server, and the checker fails it. This affects only Gemma's first run on today's request, which
  failed on other cases too.
- **No reference model.** Flash was off limits, so nothing shows that the bar can be passed. The 3B
  control shows the eval separates models: 44 and 50 of 75.
- **25 invented requests**, written before any model ran, and not drawn from real sessions.
- **The machine was shared.** Jev's CPU replica started at 02:19 UTC, after the first run and before the
  variant's. The 3B's CPU rates moved about 2% between the two, and no run overlapped R4's drills. A guard
  would have stopped the eval if R4's state had moved on.

**Pins.** Image `ghcr.io/ggml-org/llama.cpp@sha256:33868c035b21dc63f7c60b7438774283fd99215bc319114eb03de5df4ce7cd6b`.
Models by revision and LFS SHA256:

| Model | Revision | SHA256 |
|---|---|---|
| `qwen35-9b` | `1379f25c6b505a3fc737bd7818cb09389cf807c1` | `cd76ec205963b3b33350093e6904d9de16c4e666fd104e1f632d25c7f15f2a13` |
| `ministral3-8b` | `0102285ad796bd99af90f58de616092e5630e970` | `33e7a72cf5e6e2cfc2f2847075acc013d68bba023e35310cef86b5cf8fdca761` |
| `gemma4-e4b` | `4b4a2c1d584be7264f87aac328a1bc739ce81b6c` | `676c35070db6dbe52f93e9c864ee0fba4eddea94b9c875d9cb10daff453fbaee` |
| `q25-3b`, control | `7dabda4d13d513e3e842b20f0d435c732f172cbe` | `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d` |

## The done test

**Replaced by the REPLAN, 2026-11-02. The copilot is done when p95 is under 15 s with 0 cycles lost, on
a replayed session under normal load.** Task 12's DONE WHEN is this test.

- **p95** is `rt_s` in the session's `timing` rows, nearest rank, as `slo.py` and the session's timing
  summary compute it. The client's retry is inside it, as the caller waits for both attempts.
- **A lost cycle** is one with `ok` false: `copilot_timeout_s` passed, the gateway failed, or the JSON was
  invalid after the retry. It logs `copilot cycle skipped`.
- **How:** `jarvis_live/replay.py` against lab at real-time speed, so cycles arrive as they would live, on
  a weekday with agents busy.
- **Normal load:** note the period, as Task 11 requires, and apply its gate. At least 40% of requests see a
  queue and at most 40% run alone. A run that fails the gate is not a normal-load figure, whatever it shows.

**Why the bar moved.** 2.5 s came from a spoken reply. The copilot does not stream and cycles at most
every 25 s, so what matters is that a note arrives before the next cycle is due and that no cycle is lost.
The 15 s leaves that margin. The earlier reasoning stands for what it covered: 0.8 s to first text
cannot apply, since the first text is the whole reply.

`slo_copilot_roundtrip_p95_s` is 15, tested, and the eval harness reads it rather than keeping its own 8 s.
**Voice keeps a fast target on its own path:** 2.5 s total for the LLM part.

**If it fails on Flash,** the next lever is the copilot on pve's model, if that model passes the 11 bar
cases. That is R2's result to give.

## Alert

**Why nobody was told:** the flip succeeded, so nothing was wrong as far as any check could see.
`KubeNodeNotReady` and the chart's own rules go to the `null` receiver.

Proposed, in Task 21's `apps/observability/llm-path/`, without `page: "true"`:

```yaml
- alert: BestEffortTierDown
  expr: >-
    max by (deployment) (max_over_time(
      kube_deployment_status_replicas_available{namespace="jarvis", deployment="ollama"}[12h]
    )) == 0
  labels:
    severity: warning
    notify: ntfy
    tier: best-effort
```

- **12 hours** clears a gaming session (0.7 h) and would have caught this one on the first morning.
- **`max_over_time`, not `for:`.** After a long Prometheus gap, `for:` starts again, and that could
  alert twice.
- **`max by (deployment)`** keeps one series when kube-state-metrics changes pod.
- **A parked tier counts as down.** That is the fault here: a deliberate flip that told nobody.
- **One message needs its own route.** Add a child route for `tier = "best-effort"` above the `ntfy`
  route, with a long `repeat_interval` such as 720h. Otherwise the default repeats it. The resolved
  message stays on.

**Three limits:**

1. **It depends on Task 21's decision 3.** While Prometheus runs on the gaming PC, a sleeping PC
   raises nothing, because the evaluator sleeps too. A flip to `whisper` or gaming mode does fire.
2. **`infra_alerts` may not be read.** Task 21 found `HomeTileDown` firing 305 of 336 hours there.
   This alert is correct, and may still be ignored until that is fixed.
3. **"Exactly one" means this alert.** The PC also hosts many pods, so a full PC outage can raise
   `HomeTileDown` for others.

It will fire on deploy, because it is true. That is the first test, and is one message. The second
is a `promtool` test for a dip, a restart and a recovery.

## Correction to the talkback design

For `docs/designs/voice-talkback.md` §5.1 and §2.2, in step 1 below:

- **§5.1:** the row "LLM time-to-first-sentence (`fast-chat` via LiteLLM) 400-600 ms" has no measured
  basis. Replace it with the `interactive` alias and "to be measured", and note the state on
  2026-11-01: `fast-chat` has no backend, every request falls back to `heavy` with about 2.3 s added,
  and first text was 6-16 s.
- **§2.2:** the agent calls the `interactive` alias, not `fast-chat`.
- **§5.1, the sum:** "~0.95-1.7 s" assumes that row, so the target stands unproven.

## Proposed steps, for approval

One PR per step. Steps 1, 2, 3 and 3a are done. The REPLAN changes the rest: rows 4 to 6 below say how.

| # | Change | Repo | Restarts | Rollback | When |
|---|---|---|---|---|---|
| 1 | `think=False` for copilot, voice and ideas, with tests. Talkback correction | `jarvis-voice` | `jarvis-live`, lab first | Revert | Now. The probe sends its own body, so this does not disturb it |
| 2 | `fast-chat`, `fast-coder`: `timeout: 5`, `num_retries: 0`. Correct the cooldown comment. Pin it in `test_router_config.py` | `homelab-gitops` | **The proxy, so every LLM client** | Revert | After the voice arm. **Before `ollama` returns.** Could share Task 22's window |
| 3 | Run the comparison on 11's probe, and read arm V for the thinking question | `jarvis-voice` | None | None needed | **Done. Result: 4 and 5 stop** |
| 3a | Make the top-level `CopilotDelta` keys required, so actions come back. Set the live evals to the same request | `jarvis-voice` | `jarvis-live`, lab first | Revert | **Done: 13 of 17, 17 of 17 replies with actions.** On live in `server-v0.32.2`, with no soak. [R1's replay](#what-to-watch-on-lab) checks it under load |
| 3b | Find a model and a reply size that pass [the bar](#what-happens-next), on the evals, before any pod exists | `jarvis-voice` | None | None needed | **Done: nothing passed.** Best 6 of 11 at p95 306 on the 3B. A record, not a gate, since the copilot now names Flash: [results](#step-3b-what-was-measured) |
| 4 | New pve workload and profile, `interactive` alias, copilot evals on it | `homelab-gitops` | Proxy, new pod. No Flash | Remove the alias and the workload | **Blocked on 3b.** The 3B does not go ahead. R5 replaces this for voice |
| 5 | Set `JARVIS_LIVE_COPILOT_MODEL` to `interactive` on lab, replay, then live. Voice follows, since `VOICE_MODEL` is empty | `jarvis-voice` | `jarvis-live` | Restore `fast-chat` | **No longer applies to the copilot,** which names Flash in R1. `VOICE_MODEL` moves alone, in R5 |
| 6 | `BestEffortTierDown` and its route, with `promtool` tests | `homelab-gitops` | None | Revert | **Cancelled by the REPLAN:** `ollama` is not coming back |

Step 3 changed the recommendation, so steps 4 and 5 stop. Steps 1, 2 and 6 hold, as does GPU moves 1
and 2 in `Tasks.herd`. Move 3 is step 4 and stops with it.

## Other things found

- **Speech now has its only GPU backend on a best-effort machine.** If the PC sleeps, live
  transcription has no GPU tier left. It is outside this task, and it is the same class of fault.
- **A failed `whisper-cpp` pod** sits in `UnexpectedAdmissionError` from 3 days ago. It has no effect.
- **The `flash-embed` profile is active, but no embedding pod exists.** Task 11 noted this too. It
  matters here only for how much Spark memory is really free.
- **`ollama`'s `OLLAMA_NUM_PARALLEL: 1` and one loaded model** mean `fast-coder` and `fast-chat` evict
  each other, and each swap is a cold load. So a restored tier would not be consistently fast.
- **Task 21 and this plan agree** on Prometheus's placement, and `llm-path-alerts.md` already expects
  this alert in its directory.

## Limits

- Before step 3, no inference reached the engine. Step 3 sent inference to Flash and `jev-llm`, and
  its limits are [in that section](#limits-of-step-3). No cluster object changed.
- The LiteLLM numbers come from a local stub. They measure the gateway, not the network or vLLM.
- The history is at 10-minute resolution, with large gaps.
- The KV reading is 30 quiet Sunday minutes, not normal load.
- The copilot's persisted timings were not read. See above.
