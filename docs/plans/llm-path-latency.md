# LLM path stalls: findings and proposed changes

Status: **cause named and reproduced. Stopped for approval before any Flash change.**
Task 11 in `Tasks.txt`. Measured 2026-10-31 on `qwen3.8-flash-next`, from inside the cluster.

The task is not done: the SLO is met on an idle engine, not under normal load.

## Answer

There are two causes. Only the first is reproduced.

**1. A long prompt freezes every stream on the model (reproduced).** The engine prefills at a steady
2,420 tokens/s. With `--max-num-batched-tokens 8192`, one chunk takes 3.4 s, and every request on
the engine advances only once per step. One step yields about 2.5 tokens (MTP), so a reply of 30
tokens needs about 12 steps. While any long prompt is prefilling, a short reply takes about 40 s.

**2. Network trouble around `lxc-k3s2-server-d` (not reproduced, probably a separate fault).** Homelab
PR #832 moved LiteLLM off that node at 13:05, after finding 4-11 s network stalls and 12-15% DNS
loss from the pod there. It describes itself as a test of a hypothesis, not a proven fix. The
evidence below fits, but I did not prove it.

**LiteLLM's software is not the cause.** It adds about 6.5 ms. Its placement probably mattered, and
that has been dealt with. Replacing it is not justified.

## Evidence for cause 1

Short prompt, `max_tokens` 60, seconds. Direct and gateway are interleaved pairs.

| Engine state | Path | n | Stalled | First text p50 / p95 / p99 | Total p50 / p95 / p99 |
|---|---|---|---|---|---|
| Alone | Direct | 195 | 0 | 0.17 / 0.18 / 0.19 | 0.96 / 1.00 / 1.12 |
| Alone | Gateway | 196 | 0 | 0.18 / 0.19 / 0.29 | 0.97 / 1.00 / 1.11 |
| 1 client, 24k prompts | Direct | 10 | 10 | 6.88 / 8.89 / 8.89 | 10.89 / 11.34 / 11.34 |
| 1 client, 24k prompts | Gateway | 10 | 10 | 8.40 / 8.70 / 8.70 | 10.84 / 11.30 / 11.30 |
| 3 clients, 24k prompts | Direct | 2 | 2 | 9.82 / 25.09 / 25.09 | 54.93 / 69.18 / 69.18 |
| 3 clients, 24k prompts | Gateway | 2 | 2 | 12.67 / 13.21 / 13.21 | 55.83 / 56.39 / 56.39 |

The load clients went straight to vLLM, with no other traffic on the engine. The n for 3 clients is
tiny because each request took about a minute, but every gap was measured:

- **The prediction held.** 8,192 tokens at 2,420 tok/s is 3.39 s. With 3 clients the measured gap
  median was **3.44 s**, with all 52 gaps between 3 and 4 s. Reply time fits too: 12 steps of 3.4 s
  is about 41 s, plus first text, against 55 to 69 s measured.
- **Idle has no stall at all.** 550 requests across both prompts: none stalled, worst gap 0.097 s.
- **The same shape on real load.** Without this probe, the engine's own histogram has 6% of token
  gaps at 2.5 to 5 s. Run B below clustered at 3 to 5 s.

How often it applies: 146 M prompt tokens over 3 days is about a fifth to a quarter of the time
spent prefilling, and 24% of log samples show under 5 tokens/s with 3 or more running. `pi-durable`
averages about 34,000 tokens per prompt, and is the main caller.

Engine state over the 14 hours of logs:

| State | Share of 10 s samples |
|---|---|
| Requests waiting | 55% |
| 4 or more running | 63% |
| KV cache above 70% | 59% |
| KV cache above 90% | 34% |
| 0 or 1 running | 24% |

There is a second, separate wait: when the engine is full, new requests queue by capacity, for a
mean of 157 s. Lowering the chunk size does not touch this.

## Evidence for cause 2

All probe runs came from pods on `lxc-k3s2-server-d`, until 13:50.

| Run | Time | LiteLLM on | Engine | Requests | Stalled | Notes |
|---|---|---|---|---|---|---|
| A | 12:10-12:29 | server-d | Quieter | 151 | 17 of 67 short | 63 failed DNS. 25 of 29 gaps ended in a burst |
| B | 12:30-12:56 | server-d | Busy | 106 | 93 of 97 short | 71 of 93 gaps ended in a burst |
| Idle | 13:15-13:45 | agent-d | Alone | 550 | 0 | One DNS lookup over 1 s |
| Reproduction | 13:45-13:52 | agent-d | Controlled | 41 | Whenever overlapped | 0 of 23 gaps ended in a burst |

- A burst here means more than 3 chunks inside 20 ms at the end of a gap, so tokens existed during
  the gap. That is what held packets look like. In run A these came with an idle engine, the
  API process idle, and DNS failing at the same moment, so the engine was not the cause.
- After the move, no gap ended in a burst, even from the same node.
- **Retransmits collapsed at the move.** 15-minute samples: `server-d` 6.4/s to 0.1/s, `server-e` 6.2/s
  to 0.1/s, `agent-d` 1.7/s to 0.0/s. They stayed down while the agents ran again from 13:18 to
  13:33, so they do not follow agent load.
- DNS failed in 63 of 151 requests in run A. After the move: 1 slow lookup across the idle run.

This is before-and-after, not an experiment: it is strong, but the mechanism is unknown, and
something else changing around 13:05 cannot be ruled out. Run B mixes both causes, so its numbers are in no way a clean
measure of load:

| Run B | n | First text p50 / p95 / p99 | Total p50 / p95 / p99 |
|---|---|---|---|
| Direct | 50 | 4.94 / 19.82 / 34.16 | 10.57 / 29.34 / 38.24 |
| Gateway | 50 (3 errors) | 6.70 / 14.10 / 19.67 | 14.44 / 39.07 / 53.02 |

**Not yet measured: normal agent load since the move.** That is the number that decides how much of
the old stall is gone, and the first thing to run.

## Hypotheses

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Other traffic on the shared model, chunked prefill | **Confirmed, reproduced** | Above. One client is enough |
| PLE table and page cache | **Not the main cause** | About 7.9 GB of cache for a 48 GiB table, and heavy major faults. But in the reproduction, PLE ops took under 15% of the time and prefill stayed at a steady 2,420 tok/s. It may set that rate. Untested |
| LiteLLM pre-call checks, `ModelNotMappedError` | **Cleared** | Tracebacks are all the `fast-chat` route. 6.5 ms overhead |
| The two callbacks | **Cleared** | 6.5 ms overhead while streaming through them. Stalls match direct |
| Retries, or a fixed timeout | **Cleared for cause 1** | The steady 5.5 s fits a prefill step or the 5 s DNS timeout, not a retry. Which one depended on the moment |
| The proxy pod | **Software cleared, placement probably not** | Liveness never over 0.73 s from the same node. See cause 2 |
| The short prompt lead | **Explained** | A short request is in flight for less time, so it overlaps fewer steps |

## SLO

Through the gateway, per 200 pairs, under normal agent load:

| Measure | Target | Idle today |
|---|---|---|
| First text p95, 1-2 sentence reply | under 0.8 s | 0.19 s |
| Total p95 | under 2.5 s | 1.00 s |
| Worst gap in a reply, p99 | under 1 s | under 0.1 s |
| Errors | under 0.5% | 0 of 391 |

Worst gap is my addition, because p95 alone can hide the freeze that makes voice unusable.

A catch: a 1,800-token prompt idles at first text p95 0.86 s and total 1.91 s, so it misses the first
target with nothing else running. If voice sends that much context, either the target or the
prompt has to change.

## What each change can buy

Decode only advances once per step, so a 30-token reply takes about 12 steps:

| `--max-num-batched-tokens` | Step | Worst gap | 30-token reply during prefill |
|---|---|---|---|
| 8192 (today) | 3.4 s | Fails | About 41 s |
| 2048 | 0.85 s | Passes | About 10 s |
| 1024 | 0.42 s | Passes | About 5 s |
| 512 | 0.21 s | Passes | About 2.5 s, on the line |

These are arithmetic from a measured rate, not measurements. A smaller chunk will cost some prefill
speed, by an unknown amount, and agents will slow down. Chunks may also be constrained by the
hybrid model's block alignment. So tuning may reach the SLO only near its edge, and only for requests
already running.

## Proposed next steps, for approval

Nothing here has been done. One change per PR, with probe numbers before and after.

| # | Change | Restarts Flash? | Rollback |
|---|---|---|---|
| 1 | Measure normal load now: 200 pairs with agents running, probe off `server-d` | No | None needed |
| 2 | Scrape vLLM into Prometheus and feed Task 9 (no `vllm:*` series exist) | No | Revert the scrape config |
| 3 | Lower `--max-num-batched-tokens` to 1024, then measure agents and voice | **Yes, 8-13 min** | Restore 8192, with a second cold load |
| 4 | `--scheduling-policy priority`, for the capacity queue | **Yes, 8-13 min** | Remove the flag, with a second cold load |
| 5 | Reserved path for interactive callers (Task 12) | Depends on the profile | Task 12's plan |

Step 4 is unverified: the gateway has `drop_params: true`, which may strip the priority field, and
priority is untested with chunked prefill and MTP here. It also cannot help a request that is already
running, since those all share the same steps.

**This is the question from point (f): the cause is load on a shared model, not the gateway.** If
step 3 does not reach the SLO, or costs agents too much, a reserved path is the real answer.

Capping `pi-durable` concurrency is no longer proposed as a fix: one client is enough to freeze a
stream. It may still shorten the capacity queue.

For Task 13: this evidence does not support replacing LiteLLM.

## Other things found

- **`server-d`:** retransmits on the control-plane LXC nodes rose about tenfold about 3 days ago, then
  fell back at the 13:05 move. Why that pod on that node caused it is unknown, so it could return if
  the pod lands there again. PR #832 prevents that with a placement rule.
- **DNS search list:** with `ndots:5` a service name takes 55 ms, against 0.5 ms with a trailing dot.
- **`qwen38-27b` and `unsloth/Qwen3.8-27B-NVFP4` are dead routes** under Flash, and return 404.
- **Profile drift:** the catalog says `flash-embed`, no embedding pod exists, and `/v1/embeddings`
  returned 500.
- **Gateway 500s:** 3 of 53 in run B, one `Connection closed`. `MidStreamFallbackError` appears on
  this route, which has no fallback.
- **`jarvis-live` failed liveness and readiness checks** during the session. Not investigated.

## The pause, 2026-10-31

Approved: 45 minutes. `pi-durable` was down 13:14:55 to 13:17:50 and 13:32:59 to 13:54:01, about 24
minutes in all, and restarted three times. It was restored 5 minutes early.

The first attempt failed. `suspend: true` on `pi-harness` lasted 3 minutes, because the parent
`flux-system` Kustomization owns that object and re-applied it. To pause it again, use the annotation
`kustomize.toolkit.fluxcd.io/reconcile=disabled` on the StatefulSet and scale it to 0. That held
through a reconcile. Remove the annotation and scale back to 1 afterwards. Do not suspend the parent,
which would freeze GitOps for the whole cluster.

## Running the probe

See the header of `scripts/llm_probe.py`. Run from a pod that is not on `server-d`, then
`python3 scripts/llm_probe.py report FILE... --by-load`. To reproduce cause 1, pause the agents and
run `load` beside `run`.

- The reproduction used a scratch script. `load` is the same idea ported into the probe and tested
  against a stub, but it has not been run against the Spark.
- `engine_api_beat_s` times `/metrics`, which is not a clean measure of the API event loop. Trust
  `gateway_api_beat_s`.
- vLLM sends SSE keep-alives, so the read timeout may not bound a queued request. Under load, one
  row took over 10 minutes.
- Stall thresholds: 2 s for first text, 1 s between chunks.
