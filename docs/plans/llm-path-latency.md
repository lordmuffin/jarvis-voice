# LLM path stalls: findings and proposed changes

Status: **cause named and reproduced. Normal load measured: the SLO is missed by a wide margin. The
voice arm has run: its normal-load gate failed, so no decision row fires, and it missed its target
anyway. Stopped for approval before any Flash change.**
Task 11 in `Tasks.herd`. Measured 2026-10-31 on `qwen3.8-flash-next`, from inside the cluster. The
[voice arm](#voice-arm) ran 2026-11-01.

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

**Not covered here: the voice-shaped arm.** This run was streaming, thinking off, on Flash, so it
clears nothing about the `fast-chat` route. See [Voice arm](#voice-arm).

## Voice arm

Run 2026-11-01, 16:55:07 to 18:55:40 UTC, a Sunday. This section's times are UTC, unlike the rest of
this document. It stopped at the 200-pair cap, 2 hours in, well inside the 4-hour cap. V is `fast-chat`
through the gateway and A is the same body direct to Flash, interleaved, with the real `VOICE_SYSTEM`,
the `VoiceRequest` schema, invented request text, no `think` argument, and the 30 s `voice_timeout_s`.
The probe and the proxy were both on `lxc-k3s2-agent-d`. `pi-durable` was Running with 0 restarts at
every check from before the start to after the end. The other agents were not paused, and were not
checked one by one.

- **The normal-load gate failed, so no row of the decision table fires.** 1.1% of requests were
  `queued` against the 40% floor. `alone` passed, at 30.2% against the 40% ceiling.
- **The 2.5 s target was missed anyway.** None of 182 gateway requests finished under 2.5 s, and
  total p95 sits at the 30 s timeout. The gate exists to stop an idle run passing. This run was lighter
  than normal load and still missed: gateway requests that ran alone took 13.11 s at the median.
- **1 in 7 requests failed, all by timeout.** A failed request falls back to `from_words`, which can
  only file a request that named its target out loud. No reply was invalid JSON.
- **This is not the gateway.** Direct failed as often.
- **`/no_think` does not stop reasoning on this route.** Every reply reasoned.

### Main result

The 182 pairs that finished before the #838 rollout began. Seconds:

| Path | n | Failed, Wilson 95% | Timeouts | Invalid JSON | Total p50 / p95 / p99, every request | Total p50 / p95 / p99, answered only | Under 2.5 s |
|---|---|---|---|---|---|---|---|
| Gateway, arm V | 182 | 26 (14.3%), 9.9 to 20.1% | 26 | 0 | 17.21 / 30.03 / 30.03 | 15.61 / 28.38 / 29.09 | 0 |
| Direct, arm A | 182 | 28 (15.4%), 10.9 to 21.3% | 28 | 0 | 14.68 / 30.03 / 30.03 | 12.71 / 24.74 / 27.54 | 5 |

"Every request" counts a timeout at the 30 s it cost the caller, so those figures are lower bounds, and
their p95 and p99 are the timeout, not a measured time. The client's one retry never fired, because
only invalid JSON triggers it. Prompts were 581 tokens at p50. Completions were 393 (gateway) and 372
(direct) tokens at p50, and 687 and 647 at p95.

Gateway against direct, on the approved test of non-overlapping Wilson 95% intervals:

| Measure | Gateway | Direct | Overlap? |
|---|---|---|---|
| Failed | 26 of 182, 9.9 to 20.1% | 28 of 182, 10.9 to 21.3% | Yes |
| Over target, standing in for stalls | 182 of 182, 97.9 to 100% | 177 of 182, 93.7 to 98.8% | Yes |

Non-streaming has no gaps to count, so over-target replaces the stall rate. Per pair: 140 neither
failed, 14 gateway only, 16 direct only, 12 both. The gateway added time, not failures: see
[the changes during the run](#changes-during-the-run).

**Decision table: no row fires, because the gate failed.** Had it passed, the row would be row 2, A
misses the SLO, which the streaming run already fired. The non-overlap test for row 1 is not met. V
changes no decision taken so far, and the rerun the table asks for is still owed.

### Load

Requests by the load they saw, both paths, main result:

| Load | n | Failed |
|---|---|---|
| Alone | 110 | 1 |
| 1-3 others | 222 | 34 |
| 4+ others | 28 | 16 |
| Queued | 4 | 3 |

Which period each result landed in. p50 is every request, in seconds:

| Start | Rows | Queued | Alone | Failed | Gateway p50 | Direct p50 | Noted |
|---|---|---:|---:|---:|---:|---:|---|
| 16:55 | 50 | 0.04 | 0.00 | 5 | 18.41 | 16.73 | |
| 17:10 | 48 | 0.00 | 0.00 | 5 | 16.95 | 15.96 | |
| 17:25 | 53 | 0.00 | 0.19 | 4 | 17.87 | 13.68 | |
| 17:40 | 71 | 0.00 | 0.99 | 1 | 12.81 | 10.96 | The quiet period. #247 reaches lab |
| 17:55 | 59 | 0.00 | 0.51 | 2 | 15.23 | 12.22 | #247 reaches live at 18:06 |
| 18:10 | 36 | 0.00 | 0.00 | 18 | 28.13 | 27.54 | The surge below |
| 18:25 | 37 | 0.05 | 0.00 | 16 | 26.84 | 28.63 | The surge below |
| 18:40 | 10 | 0.00 | 0.00 | 3 | 20.63 | 18.89 | Stops at the rollout |
| 18:45 | 32 | 0.00 | 0.00 | 4 | 15.83 | 17.70 | **After #838, reported separately** |

- **The engine was far from full.** Over the run, from the vLLM log: requests waiting in 0.3% of 10 s
  samples against 55% over the 14-hour baseline and 90% in the streaming run, 2.3 of 8 places in use on
  average against 7.4, and KV cache never above 70%.
- **Nearly half the requests failed from 18:10 to 18:40, with almost nothing queued.** 34 of 73 failed there,
  against 20 of 291 in the rest of the main result. The engine had 4.7 requests running on average in
  the worst slice, and whole-request speed fell to about 15 tokens/s. At that speed a reply over 450
  tokens cannot finish inside 30 s, and p50 is nearly 400. That is arithmetic, but it means **voice
  times out on a share of the engine alone, with no queue at all.**
- **The surge came through the proxy, and I cannot name the caller.** Every POST in the vLLM log came
  from the probe or the proxy, so no second client reached the engine directly. The surge shows from
  about 18:02. The proxy's logs for that period went with its pod in the rollout. `copilot_probe.py`
  arrived in the probe's pod at 18:59, after the last row, so it did not overlap.

### Reasoning

| Path | Replies that reasoned | Reasoning tokens p50 | Share of completion p50 | Answer tokens p50 / p95 | Tokens/s p50 |
|---|---|---|---|---|---|
| Gateway | 156 of 156 | 286 | 80% | 67 / 155 | 23.4 |
| Direct | 154 of 154 | 284 | 79% | 77 / 152 | 27.7 |

**`/no_think` in the system prompt did not stop reasoning here,** on either path, on a request that
also carries a JSON schema. About four fifths of the tokens are reasoning nobody reads.

This run cannot say what `think=False` does. Arm V sends no `chat_template_kwargs`, as voice did when
the arm was specified, so whether vLLM honours `enable_thinking: false` on this body still needs a
measurement. `docs/plans/fast-tier.md` expected to read that from this arm. **Since measured, on the
copilot's body: it does.** See [Thinking](fast-tier.md#thinking) in that plan.

Thinking off should help a lot, but this run does not show it reaches the target. Requests that ran with
at most one on the engine managed 34 tokens/s over the whole request, so a 155-token answer is about
4.5 s and a 77-token one about 2.2 s. That is arithmetic from this run, not a measurement. It is also
far below the 75 tokens/s `fast-tier.md` assumes, and this run does not separate why: the JSON schema,
the engine's share, or the time before the first token.

### Changes during the run

**#247, thinking off for the copilot, voice and ideas callers.** Merged 17:42:18. It reached lab at about
17:45 and, in `server-v0.31.1`, live at 18:06, so it was not lab only. **Arm V kept the old body
throughout, so every figure here is the before number for that fix.** It may have changed the engine's
load in the last hour of the main result. This run cannot separate that from the surge.

**Homelab #838, `fast-chat` and `fast-coder` get timeout 5 and no retries.** It restarted the proxy:

| Time | Event |
|---|---|
| 18:44:10 | Config reloaded, new pod created |
| 18:44:32 | New pod Ready, old pod told to stop, with a 30 s grace period |
| 18:44:36 to 18:44:56 | Old pod refuses its readiness checks |
| After 18:45:01, by 18:45:14 | Old pod gone. It was seen draining at 18:45:01 and absent from the pod list at 18:45:14 |

Pairs are kept whole. A pair counts as main if it finished before 18:44:10, and as new config if it
started after 18:45:14. **Pairs 182 and 183 overlap the rollout and are left out of both:** three
answers and one direct timeout, so **no gateway failure came from the restart,** and leaving them out
flatters neither path. Gateway timeouts in pair 181, which ended at 18:43:36, and pair 184, which
started after the old pod had gone, stay in their groups.

New config, 16 pairs, 18:45:35 to 18:55:40. **This is a different config and too few pairs to judge.**
Do not pool it with the main result:

| Path | n | Failed, Wilson 95% | Timeouts | Invalid JSON | Total p50 / p95 / p99, every request | Total p50 / p95 / p99, answered only | Under 2.5 s |
|---|---|---|---|---|---|---|---|
| Gateway | 16 | 3 (18.8%), 6.6 to 43.0% | 3 | 0 | 15.83 / 30.03 / 30.03 | 15.46 / 28.25 / 28.25 | 0 |
| Direct | 16 | 1 (6.3%), 1.1 to 28.3% | 1 | 0 | 17.70 / 30.03 / 30.03 | 17.70 / 28.71 / 28.71 | 0 |

**#838 appears to remove most of what the gateway added.** All answers still came through the fallback
to Flash, 13 of 13. The time the gateway adds to one request, as a median over pairs where both answered,
after scaling the direct time to the gateway reply's token count:

| | Pairs | Median | Bootstrap 95% |
|---|---|---:|---|
| Main result | 140 | 2.52 s | 2.33 to 2.67 s |
| New config | 13 | 0.62 s | -0.03 to 1.09 s |

That fits the 2.3 s retry the homelab change names. The unscaled gaps are 2.83 s (1.43 to 3.73) and
2.15 s (-0.88 to 8.20), too noisy to show it. I chose the scaling after seeing the data, so treat it
as a good lead, not a result. Against a 17 s median it is a small part of the miss.

### Limits

- **Load was light, so these are favourable numbers.** The same prompt repeated all run, so it stayed
  warm in the prefix cache, which a rare voice call will not. Real voice would do worse on both counts.
- **The gateway injects `max_completion_tokens: 8192`, and direct has no cap.** No reply came near it.
- **A timed-out request reports no tokens,** so the completion and reasoning figures describe answered
  requests only, and lean short.
- The probe was `scripts/llm_probe.py` at #242 (sha256 `4598653d`). The 400 rows have sha256 `1440e1f2`.

### What this leaves

- **A run that passes the gate is still owed, and repeating this body would be wasted.** It no longer
  matches what voice sends. The rerun should send `think=False` on today's route, on a weekday with the
  agents busy, after `docs/plans/fast-tier.md` step 3, which `Tasks.herd` makes the only probe allowed
  next.
- **The recommendation to decide the reserved path stands, with a condition.** Voice misses with no
  queue, so holding a place is not enough on its own. The reply has to get shorter as well.

### Report

`llm_probe.py report --by-load`, main result:

```
label	prompt	max	path	load	n	err	stalled	first_text_p50	first_text_p95	first_text_p99	total_p50	total_p95	total_p99	total_max	stalls
normal	voice	None	direct	1-3 others	111	17	0	-	-	-	14.78	25.59	27.54	27.54	
normal	voice	None	direct	4+ others	13	8	0	-	-	-	24.03	28.63	28.63	28.63	
normal	voice	None	direct	alone	56	1	0	-	-	-	10.50	17.99	21.13	21.13	
normal	voice	None	direct	queued	2	2	0	-	-	-	-	-	-	-	
normal	voice	None	gateway	1-3 others	111	17	0	-	-	-	17.21	28.38	29.09	29.09	
normal	voice	None	gateway	4+ others	15	8	0	-	-	-	23.26	28.87	28.87	28.87	
normal	voice	None	gateway	alone	54	0	0	-	-	-	13.11	21.43	28.79	28.79	
normal	voice	None	gateway	queued	2	1	0	-	-	-	29.14	29.14	29.14	29.14	

pairs: {'pairs': 182, 'neither': 182, 'direct_only': 0, 'gateway_only': 0, 'both': 0}

prompt	path	load	n	failed	failed_95	timeouts	invalid_json	other	retried	caller_p50	caller_p95	caller_p99	ok_p50	ok_p95	ok_p99	over_target	prompt_p50	completion_p50	completion_p95	reasoned	fell_back	answered_by
voice	direct	all	182	28	10.9-21.3%	28	0	0	0	14.68	30.03	30.03	12.71	24.74	27.54	177	581	372	647	154/154	0	{'qwen3.8-flash-next': 154}
voice	gateway	all	182	26	9.9-20.1%	26	0	0	0	17.21	30.03	30.03	15.61	28.38	29.09	182	581	393	687	156/156	156	{'qwen3.8-flash-next': 156}
voice	direct	1-3 others	111	17	9.8-23.2%	17	0	0	0	16.73	30.03	30.03	14.78	25.59	27.54	108	581	372	641	94/94	0	{'qwen3.8-flash-next': 94}
voice	direct	4+ others	13	8	35.5-82.3%	8	0	0	0	30.03	30.03	30.03	24.03	28.63	28.63	13	580	342	491	5/5	0	{'qwen3.8-flash-next': 5}
voice	direct	alone	56	1	0.3-9.4%	1	0	0	0	10.50	18.33	30.03	10.50	17.99	21.13	54	581	385	734	55/55	0	{'qwen3.8-flash-next': 55}
voice	direct	queued	2	2	34.2-100.0%	2	0	0	0	30.03	30.03	30.03	-	-	-	2	None	None	None	0/0	0	{}
voice	gateway	1-3 others	111	17	9.8-23.2%	17	0	0	0	19.00	30.03	30.03	17.21	28.38	29.09	111	581	376	687	94/94	94	{'qwen3.8-flash-next': 94}
voice	gateway	4+ others	15	8	30.1-75.2%	8	0	0	0	30.02	30.03	30.03	23.26	28.87	28.87	15	581	312	483	7/7	7	{'qwen3.8-flash-next': 7}
voice	gateway	alone	54	0	0.0-6.6%	0	0	0	0	13.11	21.43	28.79	13.11	21.43	28.79	54	581	403	751	54/54	54	{'qwen3.8-flash-next': 54}
voice	gateway	queued	2	1	9.5-90.5%	1	0	0	0	29.14	30.03	30.03	29.14	29.14	29.14	2	582	321	321	1/1	1	{'qwen3.8-flash-next': 1}

gate: {"passed": false, "all": {"n": 364, "queued": 0.01098901098901099, "alone": 0.3021978021978022, "buckets": {"1-3 others": 222, "4+ others": 28, "queued": 4, "alone": 110}}, "direct": {"n": 182, "queued": 0.01098901098901099, "alone": 0.3076923076923077, "buckets": {"1-3 others": 111, "queued": 2, "alone": 56, "4+ others": 13}}, "gateway": {"n": 182, "queued": 0.01098901098901099, "alone": 0.2967032967032967, "buckets": {"1-3 others": 111, "4+ others": 15, "queued": 2, "alone": 54}}}
reopen: {"failures": {"gateway": [26, 182], "direct": [28, 182], "gateway_ci": [0.09939008289660273, 0.20108946527775654], "direct_ci": [0.10863610380083977, 0.21336715058353922], "exceeds": false}, "over_target": {"gateway": [182, 182], "direct": [177, 182], "gateway_ci": [0.9793286325558971, 1.0], "direct_ci": [0.9373096754768265, 0.9882096915540213], "exceeds": false}}
pairs: {"pairs": 182, "neither": 140, "direct_only": 16, "gateway_only": 14, "both": 12, "gateway_minus_direct_p50": 2.8157}
periods:
start_utc	n	queued	alone	failed	gateway_p50	direct_p50
16:55	50	0.04	0.00	5	18.41	16.73
17:10	48	0.00	0.00	5	16.95	15.96
17:25	53	0.00	0.19	4	17.87	13.68
17:40	71	0.00	0.99	1	12.81	10.96
17:55	59	0.00	0.51	2	15.23	12.22
18:10	36	0.00	0.00	18	28.13	27.54
18:25	37	0.05	0.00	16	26.84	28.63
18:40	10	0.00	0.00	3	20.63	18.89
```

New config:

```
label	prompt	max	path	load	n	err	stalled	first_text_p50	first_text_p95	first_text_p99	total_p50	total_p95	total_p99	total_max	stalls
normal	voice	None	direct	1-3 others	16	1	0	-	-	-	17.70	28.71	28.71	28.71	
normal	voice	None	gateway	1-3 others	16	3	0	-	-	-	15.46	28.25	28.25	28.25	

pairs: {'pairs': 16, 'neither': 16, 'direct_only': 0, 'gateway_only': 0, 'both': 0}

prompt	path	load	n	failed	failed_95	timeouts	invalid_json	other	retried	caller_p50	caller_p95	caller_p99	ok_p50	ok_p95	ok_p99	over_target	prompt_p50	completion_p50	completion_p95	reasoned	fell_back	answered_by
voice	direct	all	16	1	1.1-28.3%	1	0	0	0	17.70	30.03	30.03	17.70	28.71	28.71	16	581	363	663	15/15	0	{'qwen3.8-flash-next': 15}
voice	gateway	all	16	3	6.6-43.0%	3	0	0	0	15.83	30.03	30.03	15.46	28.25	28.25	16	580	305	798	13/13	13	{'qwen3.8-flash-next': 13}
voice	direct	1-3 others	16	1	1.1-28.3%	1	0	0	0	17.70	30.03	30.03	17.70	28.71	28.71	16	581	363	663	15/15	0	{'qwen3.8-flash-next': 15}
voice	gateway	1-3 others	16	3	6.6-43.0%	3	0	0	0	15.83	30.03	30.03	15.46	28.25	28.25	16	580	305	798	13/13	13	{'qwen3.8-flash-next': 13}

gate: {"passed": false, "all": {"n": 32, "queued": 0.0, "alone": 0.0, "buckets": {"1-3 others": 32}}, "direct": {"n": 16, "queued": 0.0, "alone": 0.0, "buckets": {"1-3 others": 16}}, "gateway": {"n": 16, "queued": 0.0, "alone": 0.0, "buckets": {"1-3 others": 16}}}
reopen: {"failures": {"gateway": [3, 16], "direct": [1, 16], "gateway_ci": [0.06591478773586763, 0.4300935986845823], "direct_ci": [0.011119057308331216, 0.2832926836802987], "exceeds": false}, "over_target": {"gateway": [16, 16], "direct": [16, 16], "gateway_ci": [0.8063865817272801, 1.0], "direct_ci": [0.8063865817272801, 1.0], "exceeds": false}}
pairs: {"pairs": 16, "neither": 13, "direct_only": 0, "gateway_only": 2, "both": 1, "gateway_minus_direct_p50": 2.1532}
periods:
start_utc	n	queued	alone	failed	gateway_p50	direct_p50
18:45	32	0.00	0.00	4	15.83	17.70
```

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
| 1 | Normal load. **Both arms have run.** Streaming: 97 of 200 gateway requests. Voice: 182 main pairs, gate failed, so a rerun is owed. See [Voice arm](#voice-arm) | No | None needed |
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
