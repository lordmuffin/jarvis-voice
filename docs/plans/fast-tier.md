# Fast tier: findings and proposed changes

Status: **cause found. Recommendations accepted 2026-11-01, steps below approved. Step 1 is done (#247). The pve question is answered, see Decisions.**
Task 12 in `Tasks.herd`. Read on 2026-11-01 from `main` of both repos and the cluster.

**The comparison is not done.** `feat/probe-voice-arm` is still running and has not reported, so
this task sent no inference to the engine. Its recommendation for the tier is provisional. The
reserved path recommendation is firm, as asked.

## Answer

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
- **My recommendation is a small model of our own for interactive callers,** behind the gateway on
  the pve RTX 2070. It is the only option that can reach 2.5 s under load without a Flash restart.
  A place on the engine is not enough. See [Reserved path](#reserved-path).

## Decisions

All four recommendations below were accepted on 2026-11-01. Steps 4 and 5 still depend on step 3's comparison, as [the steps](#proposed-steps-for-approval) say.

| # | Decision | My recommendation |
|---|---|---|
| 1 | `voice_timeout_s` against the 40 s path (decision 6 in the gateway plan) | **Keep 30 s, and shorten the route instead.** `fast-chat` gets `timeout: 5` and `num_retries: 0`. `ollama` must not return before that lands |
| 2 | Reserved path (decision 10) | **A separate small model**, alias `interactive`, falling back to `heavy` |
| 3 | What "first text" means for the copilot | **Total p95 under 2.5 s.** The copilot does not stream, so 0.8 s cannot apply. See [the done test](#the-done-test) |
| 4 | Alert window | **12 hours**, relying on Task 21's decision 3 (Prometheus off the gaming PC) |

**Answered 2026-11-01: the move to `jev` was not deliberate.** The gaming PC runs Kubernetes workloads
unless Andrew is gaming, so it stays best-effort. Speech goes back to pve, and `ollama` to the gaming
PC, after step 2. pve then runs Whisper plus `interactive`, 4 + 3 of 7 GiB, in a new profile, and Jev
stays on `jev-cpu`. This replaces the `jev` beside `interactive` layout in
[The comparison](#the-comparison-provisional). The interim is `speech-jev`, which exists today.

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

Fallback is now measured. Thinking and engine wait are not, and need the probe.

| Part | Size | Status |
|---|---|---|
| Fallback, `ollama` at 0 | **About 2.3 s, every request** | Measured on the production image, on a stub |
| Thinking, if Flash reasons | About 1.6 s (490 characters, about 120 tokens, at 75 tok/s) | Estimate. Whether it reasons at all is the voice arm's question |
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

**Not tested: whether voice with no `chat_template_kwargs` reasons on Flash.** That needs a model
call, which would be a second probe on the engine. Arm V records it, so I will read it from there.
A stub also cannot show that vLLM honours the setting, so that part rests on the voice arm too.

## The comparison (provisional)

To run after `feat/probe-voice-arm` reports, using the same probe. Nothing below is a measurement of
that option.

| | Restore the tier | Copilot on Flash, thinking off | **Small model on pve** |
|---|---|---|---|
| Change | pve to `speech-jev`, PC to `ollama` | One config value | New `interactive` alias, new workload on the 2070 |
| Avoids the agents' queue | Yes | **No** | Yes |
| Depends on the gaming PC | **Yes, and the copilot's speed with it** | No | No |
| Speech | Moves back to pve, if that works | Unchanged | Unchanged |
| Reply quality | `qwen2.5:7b`, known | Best | 3B, **unknown, needs evals** |
| Reaches 2.5 s under load | When the PC is awake | No, not without a reserved path and step 3 | Probably, near the edge |
| Restarts | Profile flips, then a proxy restart for the timeouts | Copilot config | Proxy, and pve's new pod. No Flash |

**Provisional recommendation: the small model, using the `interactive` alias.**

- **It is the "deliberately uses something else" branch of the goal.** The copilot stops relying on a
  machine that may not be there, which the constraint rules out anyway.
- **There is room for one model.** pve's budget is 7 of 8 GiB and `jev` uses 3. A second 3B at 3 GiB
  makes 6. A 7B does not fit beside `jev`, and neither does `speaches`, so this choice does keep
  speech on the gaming PC.
- **The rate looks workable.** The same 3B on that card, from `jev`'s logs: 2,961 tok/s prefill (n=95,
  prompts under 310 tokens) and 138 tok/s decode (n=5,504). A 2,500-token prompt and a 150-token reply
  would be about 0.8 + 1.1 = 1.9 s. Those are estimates: prompts here are short, the reply size is
  unknown, and JSON schema enforcement may slow it.
- **Keep it apart from `jev`.** Jev's own pod guards `pi-durable`'s bash, so it must not share with a
  chat caller. Use a separate pod, on the free slice, with `fallbacks: {"interactive": ["heavy"]}`.
  That obeys the rule: fast to heavy only, never the reverse.
- **A new alias, not a re-pointed `fast-chat`.** Otherwise `auto` would send Open WebUI's simple chats
  down the reserved path. `fast-chat` stays as the best-effort tier it was.

If the 3B fails the copilot evals, the next option is a small model on the Spark, which has 0.06 to
0.10 of its memory budget free. It would have its own queue and KV cache, but shares GPU time with
Flash, and how that behaves is unknown. It should not need a Flash restart, as the 10-30 profile
change did not cause one, but verify that first.

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
| Reaches 2.5 s alone | No | No | Probably |

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

One PR per step. Nothing here has been done.

| # | Change | Repo | Restarts | Rollback | When |
|---|---|---|---|---|---|
| 1 | `think=False` for copilot, voice and ideas, with tests. Talkback correction | `jarvis-voice` | `jarvis-live`, lab first | Revert | Now. The probe sends its own body, so this does not disturb it |
| 2 | `fast-chat`, `fast-coder`: `timeout: 5`, `num_retries: 0`. Correct the cooldown comment. Pin it in `test_router_config.py` | `homelab-gitops` | **The proxy, so every LLM client** | Revert | After the voice arm. **Before `ollama` returns.** Could share Task 22's window |
| 3 | Run the comparison on 11's probe, and read arm V for the thinking question | `jarvis-voice` | None | None needed | After the voice arm reports |
| 4 | New pve workload and profile, `interactive` alias, copilot evals on it | `homelab-gitops` | Proxy, new pod. No Flash | Remove the alias and the workload | After 3, if it agrees |
| 5 | Set `JARVIS_LIVE_COPILOT_MODEL` to `interactive` on lab, replay, then live. Voice follows, since `VOICE_MODEL` is empty | `jarvis-voice` | `jarvis-live` | Restore `fast-chat` | After 4 |
| 6 | `BestEffortTierDown` and its route, with `promtool` tests | `homelab-gitops` | None | Revert | After Task 21 step 1 |

**If step 3 changes the recommendation,** steps 4 and 5 change with it. Steps 1, 2 and 6 hold whatever
it shows.

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

- No inference reached the engine, and no cluster object changed.
- The LiteLLM numbers come from a local stub. They measure the gateway, not the network or vLLM.
- The history is at 10-minute resolution, with large gaps.
- The KV reading is 30 quiet Sunday minutes, not normal load.
- The copilot's persisted timings were not read. See above.
