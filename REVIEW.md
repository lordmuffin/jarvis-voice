# Review: LLM gateway options

Reviewer: Grayfield. Target: `docs/plans/llm-gateway-options.md` at `8fd0b3e`. Date: 2026-10-31.

## Verdict

**Keep LiteLLM: upheld. The reasoning: rejected.**

The plan blames the network path, and the pod has already moved off it. The stall that remains is in the engine. Task 11 reproduced it 21 minutes before this plan was committed, on a branch the plan cites as having no result. Any gateway, and the bypass, sits in front of the same engine.

The plan is not at DONE. Fix the four blockers below. After that, Andrew can decide from it.

| Decision | Plan says | Review says |
|---|---|---|
| Gateway | Keep LiteLLM, provisional | Keep LiteLLM. Reopen only on the condition in [Finding 3](#3-the-bifrost-arm-cannot-win) |
| Bifrost scratch test | Finalist, arm D | **Dropped by Andrew, 2026-10-31** |
| Voice direct to vLLM | "Not yet" | **Dropped by Andrew, 2026-10-31** |
| Next spend | Bake-off, then alert | Engine metrics, a real voice measurement, then Task 11 step 3 |

Dropping the Bifrost arm departs from the brief, which asks to run the top two or three. Andrew made that call on 2026-10-31.

## Evidence used

- `[P]`: the plan.
- `[T11]`: `docs/plans/llm-path-latency.md` on local branch `fix/llm-path-stalls`. Commit `b6e6a17`, 18:57 UTC. The plan's commit is 19:18 UTC.
- `[H]`: `homelab-gitops` main, read through repos-mcp.
- `[J]`: this repo.
- `[R]`: read-only checks during this review, about 19:20 to 19:30 UTC. `kubectl get` and `top`, proxy logs, Prometheus. No inference was sent, because a Task 11 `llm-probe` pod was running.

## Blockers

### 1. The bottom line is out of date

`[P]` says "New evidence points at the network path" and that Task 11's probe result is "still outstanding". `[T11]` already has a named, reproduced cause:

- One long prompt freezes every stream on the engine. `--max-num-batched-tokens 8192` at 2,420 tokens/s gives a 3.4 s step.
- Idle, 0 of 550 requests stalled.
- With one client sending 24k prompts, **direct stalled 10 of 10 and the gateway stalled 10 of 10**. Total p50 was 10.89 s direct and 10.84 s through the gateway.
- LiteLLM adds about 6.5 ms.
- `[T11]` treats the network fault as probably separate. It is not a second name for the same stall.

That sample is small and the load was synthetic. Task 11's run under normal load has still not happened, and it remains the deciding number. It does not rescue the network story: the plan's own rule says "if A stalls, no gateway can fix it", and A has already stalled under synthetic load.

"Traffic is small" also misleads. 0.016 requests per second describes the gateway. The engine had requests waiting in 55% of samples `[T11]`. Saturation is real. It is one hop past the gateway.

**Why does a document that exists to pick a gateway still not name what the engine does under load?**

### 2. The decision table can close on the wrong result

| Row | Problem |
|---|---|
| "Stalls stop after a network or placement fix: keep LiteLLM, close" | No load condition. An idle run passes this today while voice would still freeze. Require normal agent load. |
| "A misses the SLO: move to priority scheduling" | This is the row the evidence predicts, and it names a weak fix. Priority cannot help a request that is already running, and `drop_params: true` may strip the field `[T11]`. Name Task 11 step 3 and the reserved path first. |
| R1 to R8 | No requirement that a priority field survives the gateway. If row 4 fires, LiteLLM's ✅ column is unproven for the one feature that matters. |

**Which row fires if the next run is idle at 4 AM, and who catches that it was idle?**

### 3. The Bifrost arm cannot win

Row 2 needs A to meet the SLO, tuned LiteLLM to miss it and Bifrost to meet it. LiteLLM's overhead is 6.5 ms. Idle total p95 is 1.00 s on both paths, against a 2.5 s target. Replacing a 6.5 ms hop cannot recover a miss that the hop did not cause.

The plan's own rules also make this expensive. 200 per arm, idle and busy, short and long, four arms, two placements: **6,400 requests**. Half are busy. Serial, at the 10.8 to 55 s measured under load, that is 10 to 49 hours on the shared model whose contention is under test.

One signal does survive, and it is not about speed. The proxy returned 12 HTTP 500s in 75 minutes after the move `[R]`, from `Timeout on reading data from socket` and `MidStreamFallbackError`. 10 of the 12 came from `pi-durable`. The other 2 came from a pod that was not identified. So LiteLLM may turn a frozen engine into a failed stream where direct would have kept waiting. That is a timeout setting first. Every gateway has one.

**Reopen condition:** under normal load, gateway error or stall rate exceeds direct beyond the confidence interval, and it survives tuning the timeout. Otherwise this is a closed question.

**What result from arm D would change the decision, given a 6.5 ms hop?**

### 4. The voice measurement does not measure voice

| Plan | Code and deployment |
|---|---|
| Rule 5: voice body has "thinking off, because that's what `jarvis-live` sends" | **False for voice.** `voice/service.py:160` passes no `think`, and `client.py:80` defaults to `think=True`. Only the finalizer sets it. R1's "turns thinking off this way" has the same error. |
| Rule 6: use `qwen3.8-flash-next` | Voice sends `fast-chat` (`VOICE_MODEL` empty, then the copilot model) `[J]`. That alias has zero endpoints `[R]`, so every call depends on fallback to `heavy`. Task 12 measured first text at 6 to 16 s. |
| Task 11's probe | Streaming, thinking off, on Flash. Voice is non-streaming, so first text is invisible to it. Only total time counts. |
| `[T11]` clears the callbacks and the pre-call path | That clearance is for `qwen3.8-flash-next`. The `fast-chat` route was not in those arms. |

The 6.5 ms figure, the SLO table and the arms all describe a request voice does not make.

There is a worse path waiting, and Task 12 walks into it. When Ollama returns and the gaming PC sleeps, `fast-chat` costs about 40 s across two 20 s attempts `[H]`. `voice_timeout_s` is 30 `[J]`. Voice can give up before the fallback fires.

Voice is also off on live: `VOICE_ENABLED` is `false` in base and only lab sets it true `[J]`. The copilot is what uses `fast-chat` on live. Nobody should move 18 client groups for a feature that has not shipped.

**What number, on the wire voice actually uses, has anyone ever measured?**

## Findings

### 5. The proposed alert stays green through the actual fault

The plan recommends one alert, proxy reachability. During a prefill freeze `/health/liveliness` answers in milliseconds. No `vllm:*` series exist `[T11]`. So the failure that blocks this work pages nobody and graphs nothing, after the alert ships.

The 3 AM section covers the proxy only. The Spark is the larger single point of failure: one active chat model, every client, 8 to 13 minutes to cold load, `heavy`'s fallback dead.

Keep it small. Blackbox exporter and `Probe` CRDs already exist `[H: apps/observability/home-status/]`, so this needs no new component.

1. Page: a latency-bound probe of the proxy from another node, for total outage. Reachability alone misses a 10 s answer.
2. Page: a one-token completion through `heavy`, at a low rate. That is the only signal that proves inference works end to end. Check it does not become load.
3. No page: scrape vLLM (Task 11 step 2), and feed Task 9.

**At 3 AM with the engine frozen, what fires?**

### 6. The placement fix has no mechanism

The plan treats the move as "off the bad path". Check what moved.

- Check the physical host map in the private homelab repo before calling this a move off the bad path. A new node name is not a new failure domain.
- Retransmits fell on nodes the pod was not running on:

| Node | Retrans/s, ~17:26 UTC | ~19:26 UTC |
|---|---|---|
| `server-b` | 6.2 | 0.4 |
| `server-c` | 6.0 | **3.8** |
| `server-d` | 5.9 | 0.1 |
| `server-e` | 5.5 | 0.1 |
| `server-a` | 2.4 | 0.1 |
| `agent-d` | 1.4 | 0.0 |

Two 15-minute samples `[R]`. A bad node does not explain this. The move also restarted the pod, so placement and restart are confounded. `server-c` has not recovered, and nothing says why.

A hypothesis with no mechanism cannot be alerted on or put in a runbook. It comes back without warning. The plan's "supports the network hypothesis" is too strong. It supports "something changed at about 18:05".

Second-order costs of the fix:

- If the proxy's node fails, the pod waits out the default 300 s node eviction with nothing paging.
- `agent-d` also takes Forgejo CI bursts `[H: docs/forgejo.md]`. The proxy still requests 100m CPU. Tuning item 2 now matters more than the plan ranks it.
- Failover lands on `agent-a`, `-b` or `-c`. None was measured.

Also stale: tuning item 1 says "No scheduling constraint exists today". PR #832 added one.

**If the stalls return next Tuesday, what does the on-call check first?**

### 7. The next DNS stall is already scheduled

One of three CoreDNS replicas runs on `andrew-gamingpc` `[R]`. `[H]` says that node "sleeps unpredictably", and PR #832 banned the proxy from it for that reason. The proxy still resolves through it. When the node sleeps, a share of lookups go to a dead replica until endpoints update, with a 5 s client timeout. `[T11]` lists that 5 s timeout as one explanation for the steady 5.5 s stall.

The plan measured DNS loss per replica and never noted where the replicas run. Keep CoreDNS off that node. It is one affinity rule.

**Why is the proxy too fragile for a node that can still hold a third of its DNS?**

### 8. "Nothing by design" is wrong for LiteLLM tuning

The plan prices tuning at "hours per change" and says nothing breaks. The keep option has real costs.

- Reloader restarts the pod on every ConfigMap change `[H]`. Grace is 30 s with no `preStop` `[R]`. Aliases allow 300 s `[H]`. Long agent streams die on each config PR. Six tuning items means up to six interruptions for every client, and six breaks in Task 11's before and after runs.
- 6 pods in 14 days `[R]`. Restarts are routine, and this is a single replica.
- Memory peaked at 865 Mi of a 1 Gi limit in 7 days `[R]`, and sits at 689 Mi after 79 minutes. The plan quotes 736 Mi. An OOM takes out every client. No alert covers it. No OOM kill was found.
- Nobody owns upgrades. The pin is 1.104.2, upstream is 1.106.6, and the package has a supply-chain incident on record. Keeping it means setting a cadence.

Batch the config changes into one window, with Task 11's runs either side. Raise the memory limit first.

**Who owns the next LiteLLM upgrade, and what triggers it?**

### 9. The Bifrost cost is a proxy-side estimate

2 to 4 days covers porting. The client table has 18 rows. Four carry "not checked" or "not verified", the LXC clients sit outside Flux, and one was configured by hand. You cannot price a cutover for clients you have not enumerated. There is no parallel run, no rollback and no soak in the number. If the arm is dropped, delete the estimate. If it stays, fix it.

**Which client finds out last?**

### 10. The bypass entry should be a no

Direct stalled 10 of 10 under load. The bypass costs the rule, the fallback under `coder`, central logs and alias indirection, and buys nothing against the known cause. The plan says "Not yet" and keeps it a finalist, even though its own table marks it ❌ on R4 and the brief says to drop failures. The bypass is also not the reserved path. That is Task 12's question, and a reserved path can stay behind the gateway.

**What does voice get from skipping the proxy, in milliseconds?**

## Minor

- R5 is hard, yet embeddings returned 500 and the Whisper role has no endpoint. Other dead functions were downgraded because they were dead. It changes no outcome, because Kong fails R4 and R7 anyway.
- `[M]` window: "All were taken in one 20 minute window" is now contradicted by the 19:17 run in the same doc.
- 10 of 20 against 0 of 20 gives Fisher p of 0.0004 if independent. That is a real difference, at one time of day. It does not show the cause.

## Upheld

- Kong, Portkey and Envoy AI Gateway dropped. Reasons hold on the evidence cited.
- Vendor numbers discounted. Microseconds cannot decide a problem measured in seconds.
- Control placement for any bake-off, and the warning about `llm-probe` running on `server-d`.
- No migration, and no measured comparison run before the gate.

## Required before DONE

1. Rewrite the bottom line around `[T11]`: engine freeze reproduced, network fault separate and unexplained.
2. Change the decision table: add the load condition, and put Task 11 step 3 and the reserved path ahead of priority.
3. Remove the Bifrost and bypass finalists and arm D, and add the reopen condition. Andrew dropped both on 2026-10-31. Keep arm A as the direct measurement floor.
4. Correct rule 5, rule 6 and R1. Add a voice-shaped arm: `fast-chat`, non-streaming, JSON schema, thinking as sent today, through the gateway, under normal load.

Fix regardless of gateway, in order:

1. Task 11 step 1, normal load since the move.
2. vLLM scrape and the two page-level alerts.
3. Decide `voice_timeout_s` against `fast-chat`'s 40 s path before Task 12 restores Ollama.
4. CoreDNS off `andrew-gamingpc`.
5. Memory limit, CPU request and the 500s' read timeout, in one change window.

## Not verified here

- Normal load since the move. Nobody has this number.
- The cause of the 12 HTTP 500s, or whether direct would have survived the same requests. One 75-minute window, overlapping Task 11's work.
- Whether `fast-chat`'s fallback adds measurable time with zero endpoints. Task 12's 6 to 16 s may be mostly thinking or engine wait.
- Whether voice with no `chat_template_kwargs` actually reasons on Flash. Code path confirmed. Runtime behaviour not tested here, since no inference was sent.
- Why retransmits fell across nodes, or why `server-c` did not. The node-eviction figure is the Kubernetes default, not observed.
- All `[T11]` figures are taken from that document and were not re-run.

> "Interesting idea. Now tell me the failure mode, who gets paged, and whether they'll have enough context to fix it without calling you. I'll wait."
