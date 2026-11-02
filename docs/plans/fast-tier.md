# Fast tier: findings and proposed changes

Status: **step 3 measured 2026-11-01. The 3B fails, so steps 4 and 5 do not go ahead, on any model yet. Steps 1 and 2 are done (#247, homelab #838). One live bug found, see below.**
Task 12 in `Tasks.herd`. Read on 2026-11-01 from `main` of both repos and the cluster.

**Step 3's result is in [The comparison](#the-comparison).** Most other sections were written before
it. Where step 3 contradicts them, step 3 wins.

## Answer

**Step 4 does not go ahead, on the 3B or any other model. Step 5 waits for it.** There is no model
to name yet.

- **The 3B fails on quality.** It passed 18 of 51 eval runs against Flash's 35 of 51, on the same
  fixed request. It missed owners and whole actions.
- **The 3B fails on time.** p95 was 4.35 s to the caller on a request that carries no actions. A
  complete reply is about 400 tokens, and the 2.5 s target leaves room for about 225.
- **The copilot on Flash is no answer either.** Thinking off, it took p95 29.52 s on a quiet engine.
- **Live bug, separate from all of this: the copilot's request gets no actions back from Flash.**
  0 of 22 replies carried `actions_upsert`, with thinking on or off. Marking the top-level schema
  keys required fixes it: 51 of 51. See [the schema bug](#the-schema-bug).
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
| 3 | What "first text" means for the copilot | **Total p95 under 2.5 s.** The copilot does not stream, so 0.8 s cannot apply. See [the done test](#the-done-test) |
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
to 19:28:20, labelled `normal`. 1.2% of requests saw a queue against the 40% floor. `alone` passed, at
14.8% against the 40% ceiling. Engine p50 was 3 running and KV 26%. Failing here on a quiet engine means
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
| Evals, schema fixed | | **35 of 51** | **18 of 51** |
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
| Long meeting, first 5 minutes | 0 | 0 |
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
| **Total of 51** | **18** | **35** |

By run, the 3B passed 5, 6 and 7 of 17, and Flash 11, 12 and 12.

- **The 3B loses owners and actions.** Of its failures, 26 were a wrong or missing owner, 24 a missing
  action, 5 too many actions and 3 an invented owner. Flash had 9, 11, 0 and 3.
- **It finds work in small talk,** in 3 of 3 runs. A copilot that files actions nobody took is worse
  than one that misses some.
- **It ran away once:** 2,442 tokens on a single reply.
- **The evals are not perfect.** Three cases fail on both models, and Flash fails two more that the 3B
  sometimes passes, so some of that is the cases. I left them as they are. Flash is the reference, and
  its 69% says the cases are hard.

### The schema bug

**The copilot's request does not get actions back from Flash today.** Every top-level key in
`CopilotDelta` is optional, and the structured output grammar lets a model skip them. Flash skips
`actions_upsert`:

| Request, Flash | Replies | With `actions_upsert` | Evals passed |
|---|---:|---:|---:|
| As sent today | 15 | **0** | 1 of 17 |
| Thinking on, as before #247 | 7 | **0** | 1 of 17, 10 timed out at 240 s |
| Top-level keys required | 51 | **51** | 35 of 51 |

- **#247 did not cause it.** Thinking on shows the same gap, though from only 7 replies.
- **It reaches live.** `server-v0.31.1` sends this request. I have not looked at live sessions, so what
  users saw, and since when, is not established. The copilot's fall to Flash on 10-24 would be a natural
  start, since `ollama` has a different model and takes the schema as `format`, but that is a guess.
- **The evals missed it** because the live ones need `--live`, and only ran with no `think` argument
  until this PR. As of this PR they show it, and they fail against Flash until step 3a lands.
- **The 3B has it too.** As sent, it gave no new actions in any eval that expected them, and often
  only topics and a title.
- **I did not fix it here.** It changes what live sends, so it gets its own PR and a lab soak. The
  required-keys request above is `schema["required"] = list(schema["properties"])`.

### Recommendation

**Do not build `interactive` on the 3B. Fix the schema first, whatever the tier.**

- **The decided path stands, and the model does not.** The reserved path is still a separate small
  model, and the done test is still 2.5 s. I am not reopening either. The 3B simply is not that model.
- **Restore the tier is not the answer.** `qwen2.5:7b` has no backend to measure, and the gaming PC
  stays best-effort. The other two options are measured above.
- **Flash stays the copilot's model for now, on the fallback,** with thinking off. It is slow, and it
  answers well once 3a lands.
- **GPU move 2 changes that without saying so.** With `ollama` back, `fast-chat` answers from
  `qwen2.5:7b` while the PC is awake, a model these evals have not seen. Run them on it when it returns.

### What happens next

**The bar for step 4.** One model, on the 17 cases, three runs each:

1. **Quality:** at least 2 of 3 on each of the 11 cases Flash passes 3 of 3. The 3B reached 5 of 11.
2. **Size:** replies at p95 within what 2.5 s allows on that model's card. On the 2070, that is 225
   tokens at 132 tok/s.

The evals run locally on CPU, so this costs no cluster change, and it stops a bad candidate before a
pod exists.

Two levers, in this order:

1. **A smaller reply.** Both models write about 400 tokens, so this is the reply, not the model. The
   voice arm reached the same place: the reply has to get shorter. Candidates are a cap on items per
   cycle and leaving `suggestions` and `proposals` to a slower cycle. A shorter reply must still pass the
   evals.
2. **A better small model.** The next option remains a small model on the Spark, as this plan said
   before step 3: 0.06 to 0.10 of its memory budget is free. Its speed under Flash, and whether it needs a
   Flash restart, are unknown.

### What step 4 must measure

Step 3 cannot measure these, because the workload does not exist. If a model clears the bar, step 4
owes:

| Unknown | Why not now | How step 4 measures it |
|---|---|---|
| JSON schema cost on a dedicated pod | `jev-llm` showed 132 tok/s, but with the 2,048 slot, and on Jev's pod | `llm_probe.py copilot` against the new pod, comparing `timings` with and without `response_format`, at a 2,500-token prompt |
| Contention from Whisper on the shared card | Whisper is not on pve yet, and the plan puts both on one 2070 | The same probe during a `jarvis_live/replay.py` session, which streams a WAV through the server to Whisper, then with Whisper idle. Report decode rate and total p95 for each |
| Memory under long prompts | [The incident](#incident-jev-llm-out-of-memory) | A soak of at least an hour at one prompt per 25 s, with RSS tracked and `--cache-ram` bounded |
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
  and I was not watching it.
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
- **Cases were seen before the arms ran,** and the checker changed once, to accept a resolved ISO date
  on the right weekday. All arms were rescored with it.

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

## The done test

Task 12 is done when "copilot p95 first text is within 11's SLO on a replayed session". That cannot
pass as written, for two reasons:

- **The copilot does not stream.** `complete_json` returns the whole reply, so its first text is its
  total.
- **0.8 s misses even on an idle Flash.** Task 11 found a 1,800-token prompt idles at first text p95
  0.86 s. The copilot's prompt is about 2,500 tokens or more: a 2,552-character system prompt, up to
  8 minutes of transcript, and state.

Also, `slo_copilot_roundtrip_p95_s` in `config.py` is 8 s, so the repo has a different target.

**Proposed: total p95 under 2.5 s on the replay, the total target from Task 11.** Then set the 8 s
figure to match, once it has been met. Use `jarvis_live/replay.py` against lab, under normal load,
noting the load period as Task 11 requires. A copilot that streams is a larger change, and nothing
reads the partial JSON.

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

One PR per step. Steps 1, 2 and 3 are done.

| # | Change | Repo | Restarts | Rollback | When |
|---|---|---|---|---|---|
| 1 | `think=False` for copilot, voice and ideas, with tests. Talkback correction | `jarvis-voice` | `jarvis-live`, lab first | Revert | Now. The probe sends its own body, so this does not disturb it |
| 2 | `fast-chat`, `fast-coder`: `timeout: 5`, `num_retries: 0`. Correct the cooldown comment. Pin it in `test_router_config.py` | `homelab-gitops` | **The proxy, so every LLM client** | Revert | After the voice arm. **Before `ollama` returns.** Could share Task 22's window |
| 3 | Run the comparison on 11's probe, and read arm V for the thinking question | `jarvis-voice` | None | None needed | **Done. Result: 4 and 5 stop** |
| 3a | **New.** Make the top-level `CopilotDelta` keys required, so actions come back. Set the live evals to the same request | `jarvis-voice` | `jarvis-live`, lab first | Revert | Now. It does not depend on the tier |
| 3b | **New.** Find a model and a reply size that pass [the bar](#what-happens-next), on the evals, before any pod exists | `jarvis-voice` | None | None needed | After 3a |
| 4 | New pve workload and profile, `interactive` alias, copilot evals on it | `homelab-gitops` | Proxy, new pod. No Flash | Remove the alias and the workload | **Blocked on 3b.** The 3B does not go ahead |
| 5 | Set `JARVIS_LIVE_COPILOT_MODEL` to `interactive` on lab, replay, then live. Voice follows, since `VOICE_MODEL` is empty | `jarvis-voice` | `jarvis-live` | Restore `fast-chat` | **Blocked on 4** |
| 6 | `BestEffortTierDown` and its route, with `promtool` tests | `homelab-gitops` | None | Revert | After Task 21 step 1 |

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
