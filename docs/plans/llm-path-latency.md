# LLM path stalls: findings and proposed changes

Status: **cause named and reproduced. Normal load measured: the SLO is missed by a wide margin.
Stopped for approval before any Flash change.**
Task 11 in `Tasks.herd`. Measured 2026-10-31 on `qwen3.8-flash-next`, from inside the cluster.

The task is not done: the SLO is met on an idle engine only.

## Answer

There are two stall causes, and only the first is reproduced. Under normal load, a third wait is
larger than either: **new requests queue for a place on a full engine.** See
[Normal load](#normal-load).

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

The normal-load run below supports this: with agents running, 1 of 59 long gaps ended in a burst.

## Normal load

Run 14:15 to 16:15, agents running, probe and LiteLLM both on `agent-d`. It reached the 2-hour cap
with 97 gateway and 96 direct requests, so **it falls short of the 200 pairs asked for.** Each
request took so long that the sample could not grow. Seconds:

| Prompt | Path | n | Timed out | First text p50 / p95 / p99 | Total p50 / p95 / p99 |
|---|---|---|---|---|---|
| Short, 60 | Direct | 50 | 1 | 20.55 / 71.78 / 117.82 | 26.85 / 145.78 / 147.88 |
| Short, 60 | Gateway | 50 | 4 | 18.05 / 96.73 / 98.31 | 20.87 / 110.33 / 134.05 |
| Long, 200 | Direct | 46 | 0 | 6.02 / 104.47 / 110.17 | 14.96 / 107.64 / 113.50 |
| Long, 200 | Gateway | 47 | 0 | 13.51 / 74.46 / 119.92 | 18.99 / 82.46 / 153.97 |

These percentiles understate the wait. They leave out the 5 requests that hit the probe's 120 s read
timeout, and no request could show a first text later than that.

- **The SLO failed on every measure.** Gateway short: first text p95 96.73 s against 0.8 s, total
  p95 110.33 s against 2.5 s, worst gap p99 20.01 s against 1 s, errors 4 of 97 (4.1%) against 0.5%.
  None of the 193 requests met the first text, total and gap targets together.
- **The queue is the largest part.** Gateway short requests wait a median 18.05 s for first text,
  then a median 2.96 s for the rest. 75 of 97 gateway requests saw a request waiting (`waiting_max`
  above 0) while they ran.
- **The engine was full, not just busy.** Over 2 hours of 10 s samples: 7.4 requests running on
  average, against `--max-num-seqs 8`, with requests waiting in 90% of samples and KV cache above
  90% in 59%.
- **With no queue, it still misses.** Gateway requests that saw no waiting: first text p50 1.21 s and
  p95 4.20 s, total p50 4.61 s (long, n=18). Short had only n=4 (first text p50 0.53 s, worst 3.00 s).
- **Load varies.** First text p50 was 17 to 69 s for 105 minutes, then 1.27 s in the last 15, with
  nothing waiting. Any before-and-after comparison must note which of these it landed in.
- **A 60 s client timeout would fail often.** 43 of 193 requests took 60 s or more, including the
  timeouts. 60 s is the default in `LiteLLMClient`.

Gateway against direct, the condition for reopening a LiteLLM replacement:

| Measure | Gateway | Direct | Difference, 95% CI | Fisher p |
|---|---|---|---|---|
| Errors | 4 of 97 | 1 of 96 | -2.2 to +9.1 points | 0.37 |
| Any stall | 84 of 97 | 77 of 96 | -4.2 to +16.9 points | 0.25 |
| Mid-stream gap | 32 of 97 | 25 of 96 | -5.9 to +19.5 points | 0.34 |
| First text stall | 73 of 97 | 73 of 96 | -12.8 to +11.3 points | 1.00 |

All four intervals include zero, so the condition is not met. They are wide: this sample cannot rule
out a gateway error rate several points worse than direct.

**Not covered: the voice-shaped arm.** This run was streaming, thinking off, on Flash. Voice sends
`fast-chat`, non-streaming, with a JSON schema and thinking as sent today. That arm was not run, so
step 1 is only part done, and this clears nothing about the `fast-chat` route.

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

Under normal load today, none of these targets is met. See [Normal load](#normal-load).

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

**Step 3 alone cannot reach the SLO under this load.** It shortens the gaps, but the median 18 s
wait for first text is the queue, and a smaller chunk does not give a queued request a place.

## Proposed next steps, for approval

Nothing here has been changed. One change per PR, with probe numbers before and after. Step numbers
match `Tasks.herd`.

| # | Change | Restarts Flash? | Rollback |
|---|---|---|---|
| 1 | Normal load. **Part done:** the streaming arm ran, 97 of 200 gateway requests. The voice-shaped arm did not | No | None needed |
| 2 | Scrape vLLM into Prometheus and feed Task 9 (no `vllm:*` series exist) | No | Revert the scrape config |
| 3 | Lower `--max-num-batched-tokens` to 1024, then measure agents and voice | **Yes, 8-13 min** | Restore 8192, with a second cold load |
| 4 | `--scheduling-policy priority`, for the capacity queue | **Yes, 8-13 min** | Remove the flag, with a second cold load |
| 5 | Reserved path for interactive callers (Task 12) | Depends on the profile | Task 12's plan |
| 6 | Cap agent concurrency below the engine's limit, to hold a place for interactive calls | No, if the cap lives in `pi-durable` | Restore the old limit. Whether such a limit exists there is unchecked |

Step 4 is unverified: the gateway has `drop_params: true`, which may strip the priority field, and
priority is untested with chunked prefill and MTP here. It also cannot help a request that is already
running, since those all share the same steps.

**This is the question from point (f): the cause is load on a shared model, not the gateway.** The
engine has to fix two things for a voice reply: get it a place at once, and keep steps short once it
has one. Step 3 only does the second. Step 6 only does the first, since one client still freezes a
stream, and KV cache near full may block it. Together they might reach the SLO, near its edge.

**My recommendation: decide the reserved path now rather than after step 3.** The normal-load numbers
already show step 3 cannot close the gap alone, so an 8-13 minute cold load is not needed to learn
that. Step 3 may still be worth it for agents and for the worst gap, but it no longer decides Task 12.

For Task 13: this evidence does not support replacing LiteLLM, and the condition for reopening that
is not met.

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
  row took over 10 minutes. In the normal-load run, 5 requests did stop at the 120 s read timeout.
- Stall thresholds: 2 s for first text, 1 s between chunks.
