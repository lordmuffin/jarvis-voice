# Plan: alerts for the LLM path (proxy, engine, vLLM metrics)

Status: **approved 2026-11-01, with pages going to Gotify. The build has started, in the order of
[Delivery](#delivery). Nothing is deployed or merged yet.** Step 5's inference Probe is held by a gate
listed there. No inference was sent while writing this, and Task 11's probe was not run.
Task 21 in `Tasks.herd`. Contains Task 11 step 2. Measured 2026-11-01.

## Answer

At 3 AM with the engine stopped, one Gotify page reaches a phone about 20 to 25 minutes later. That
needs two things this task did not ask for:

1. **Nothing is a page today.** `HomeTileDown` fired for 305 of the last 336 hours on ntfy
   `infra_alerts`, at default priority, so pages get their own route to Gotify at priority 10.
2. **Everything that would fire, or deliver the page, runs on `andrew-gamingpc`.** That is Prometheus,
   the Blackbox exporter, Gotify and the Gotify adapter, on the node the review says sleeps
   unpredictably. If it sleeps, nothing fires, and `absent()` cannot save you, because Prometheus
   evaluates it. All of them move.

The design also changed from the brief in one place. **A short latency bound on the one-token probe
would page on a healthy engine.** Requests routinely wait minutes for a seat, so the probe has to
outlast the queue, and the page needs a second signal to tell "queued" from "stopped".

| Alert | Fires on | Pages after | Pages? |
|---|---|---|---|
| `LlmProxyDown` | `/health/liveliness` fails or takes over 2 s, for 3 min | 3-4 min | Yes |
| `LlmInferenceDown` | No one-token success in 20 min and the engine produced no tokens | 20-25 min | Yes |
| `LlmInferenceDown` | No one-token success in 60 min, whatever the engine says | 65-70 min | Yes |
| `LlmProbesMissing` | A probe or the vLLM scrape has gone quiet | 15 min | No |
| `LlmPagePathDown` | Gotify or the adapter is not answering, so a page could not be delivered | 5 min | No, ntfy |

**What this does not page on:** the 3.4 s prefill freeze and a 10 s answer. Under today's load that
is normal: first text p50 was 18 s in the normal-load run. Paging on it would fire most days. It
goes on the dashboard, and Task 12 and Task 11 step 3 are the fix. Decided 2026-11-01.

## What I measured

Read-only: Forgejo `main`, `kubectl get`, engine `/metrics`, engine and proxy logs, Prometheus,
Mimir, and the deployed LiteLLM source. Raw normal-load data is from Task 11's run, not re-run.

### The queue decides the probe

The engine's own histogram since the pod started on 2026-10-28, 3.9 days, 6,826 requests:

| Waited for a seat | Requests | Share |
|---|---|---|
| Over 60 s | 3,400 | 49.8% |
| Over 120 s | 2,998 | 43.9% |
| Over 240 s | 1,488 | 21.8% |
| Over 480 s | 0 | 0% |

A caveat on that last row: the histogram only counts requests that got a seat. Abandoned requests
are missing (`finished_reason="abort"` is 0, though Task 11 timed out five). So 480 s is not a
proven ceiling.

I replayed the last 19.4 hours of engine logs with a simulated probe every 5 minutes: wait for the
requests already waiting, then take a seat. "Worst run" also tries every start offset.

| Probe timeout | Failed probes | Worst run in a row |
|---|---|---|
| 60 s | 35 of 233 | 29, about 2.4 h |
| 120 s | 30 of 233 | 35, about 2.9 h |
| 180 s | 22 of 233 | 6 |
| 240 s | 13 of 233 | 6 |
| 285 s | 3 of 233 | 4, 20 min |

This is an estimate. It assumes first come, first served and uses `POST` log lines as seat release,
and uvicorn logs those at response start, not at the end of a stream. That window was also calm:
requests were waiting in 31% of samples, against 90% in Task 11's run. Read it as direction, and the
direction is clear: **a 120 s probe fails for hours on a healthy engine, and no timeout on its own
is safe.**

### The signal that separates stopped from queued

In those 19.4 hours, with requests running, the longest stretch with zero generated tokens was
**30 s**. While requests were waiting, the 5-minute token rate never reached zero (lowest 0.17
tokens/s, p1 7.5). An engine with a queue keeps producing tokens. A stopped one does not. A gap of
5 minutes is ten times anything observed, so `rate(vllm:generation_tokens_total[5m]) > 0` means
"queued, not down". That is why the scrape comes first, though it does not page by itself.

### Proxy latency

40 `GET /health/liveliness` through the running Blackbox exporter: p50 12 ms, worst 27 ms. Task 11's
gateway heartbeat peaked at 1.00 s in the normal-load run. A 2 s bound clears both, and catches a
10 s answer. It still answers in milliseconds during an engine fault, which is why it is only half
the design.

### Where the alert path runs

| Piece | Node |
|---|---|
| Prometheus, Blackbox exporter, Gotify, `ntfy-gotify-adapter`, kube-state-metrics, Jev | `andrew-gamingpc` |
| Alertmanager | `lxc-k3s2-agent-b`, but only `amd64` is required, so it can land anywhere |
| ntfy | `lxc-k3s2-agent-c` |
| LiteLLM proxy | `lxc-k3s2-agent-d` |
| Postgres primary, which Gotify writes messages to | `lxc-k3s2-server-a` |
| vLLM | The Spark |

Mimir has no Prometheus samples for **29.1 of the last 408 hours**: 3.4 h, 1.8 h and 23.8 h, all
between 2026-10-28 14:19 and 2026-10-29 21:04 UTC. Prometheus's container last exited at 21:00 UTC on
the 29th, which fits. I did not establish the cause, and the Spark pod restarted that day too, so it
may not be the gaming PC sleeping. If Prometheus was down, no rule ran for a day and nobody was told.
Nothing watches this: `Watchdog` goes to the `null` receiver.

### Other facts the design rests on

- **`stream: true` is required.** In LiteLLM 1.104.2 a streaming request is cancelled upstream if the
  client leaves before the first chunk (`_buffer_first_chunk_honoring_disconnect`). The non-streaming
  equivalent needs `cancel_on_disconnect`, which is off. Without streaming, an abandoned probe could
  sit in the queue until LiteLLM's 300 s timeout, and probes would stack.
- **Fallback must be off for this request.** `heavy` falls back to `qwen-coder-32b`. That is dead
  under Flash, but under the `coder` profile it would answer the probe and hide a dead `heavy`.
- **`max_tokens` survives the gateway.** The alias sets `max_completion_tokens: 8192`, yet gateway
  replies in Task 11 stopped at 43 tokens or fewer against a limit of 60 and 200. That was the
  `qwen3.8-flash-next` alias. `heavy` has the same shape and is checked in the build.
- **Probe failures cannot put `heavy` in cooldown for real clients, on reading the code.** A single
  deployment needs 1,000 requests a minute all failing, and a client disconnect is a 499. Confirmed
  by test in step 3 below, before anything reaches the cluster.
- **No periodic one-token traffic exists.** 41 requests with `max_tokens` 1 in 3.9 days. That gives a
  clean check that the probe is the only thing adding to that bucket.
- No NetworkPolicy in `open-webui` or `jarvis-ai`, so nothing blocks the probes or the scrape.

## a) Proxy probe

New Blackbox module, added like `http_2xx_3xx_noredirect`, leaving `http_2xx` as it is:

```yaml
http_liveness_2s:
  prober: http
  timeout: 2s
  http:
    valid_status_codes: [200]
    preferred_ip_protocol: ip4
```

Probe every 30 s against `litellm-proxy-service.open-webui.svc:4000/health/liveliness`, job
`llm-path`, label `llm_check: proxy`. This also covers finding 6: a dead node waits out a 300 s
eviction, and this pages before that ends.

"From another node": the Blackbox exporter follows the placement rule under
[Making it a page](#making-it-a-page). Anti-affinity is only checked at scheduling, so if the proxy
later fails over onto the exporter's node they share it until one moves. Accepted, since the probe
still works there.

## b) One-token probe through `heavy`

```yaml
llm_one_token:
  prober: http
  timeout: 285s
  http:
    method: POST
    headers:
      Content-Type: application/json
      x-litellm-num-retries: "0"
    body: '{"model":"heavy","stream":true,"max_tokens":1,"disable_fallbacks":true,
           "messages":[{"role":"user","content":"Reply with one word."}]}'
    valid_status_codes: [200]
    fail_if_body_not_matches_regexp:
      - '"finish_reason":\s*"(length|stop)"'
    preferred_ip_protocol: ip4
```

Run against Blackbox v0.28.0 and a local stub: a good stream passes, a 200 with an error in the
stream fails on the regexp, and a hang fails at the timeout.

### The rate: one every 5 minutes, timeout 285 s

| Interval | Timeout | Why not |
|---|---|---|
| 1 min | 55 s | The probe would fail for hours on a healthy engine (table above) |
| **5 min** | **285 s** | **Chosen.** Under LiteLLM's 300 s, so the probe hangs up first and records no timeout there |
| 10 min | 585 s | Fails least, but four samples take 40 min, too slow for the point of the alert |

Prometheus allows no overlap on one target, so at most one probe exists at a time.

### Why it does not become load

Engine totals for those 3.9 days: 1,750 requests, 51.7 M prompt tokens and 1.38 M generated tokens a
day. The probe sends 288 requests a day of at most 50 prompt tokens and 1 generated token.

| Measure | Probe per day | Share of the engine |
|---|---|---|
| Prompt tokens | 14,400 | 0.03% |
| Generated tokens | 288 | 0.02% |
| Seat time, idle (0.18 s each) | 52 s | 0.008% of 691,200 seat-seconds |
| Seat time, worst case (two 3.4 s steps each) | 33 min | 0.28% |
| KV cache | 50 tokens at a time, against about 590,000 | Under 0.01% |

Request count rises 16%, and that is the wrong measure: the average real prompt is 29,000 tokens. A
waiting request holds no seat. In a queue, a probe delays the request behind it by two steps at
most, under 7 s, once every 5 minutes, against a measured 157 s mean wait. An identical body also
hits the prefix cache.

A build check keeps this honest: `vllm:request_params_max_tokens_bucket{le="1"}` must rise by 12 an
hour, no more. If it rises faster, probes are stacking, and the Probe gets deleted first.

## c) vLLM metrics, no page

A `PodMonitor` in the new `apps/observability/llm-path/`, on namespace `jarvis-ai` and label
`model-role: heavy`, port `http`, every 30 s, with `release: kube-prometheus-stack`. Selecting by
role means it follows a profile flip. The endpoint serves 491 series today. Dropping `*_created`,
`python_*` and `process_*` at scrape leaves 365, trivial against Prometheus's 756,000 head series. I
will check Mimir's headroom before merging. `/metrics` answered in 0.22 s at worst under Task 11's
load, so a 30 s scrape is safe.

**Task 9 gets a row, not a dashboard.** One row, "LLM engine", at the bottom of
`server/deploy/grafana/jarvis-live.json`, which this repo owns, then copied to
`apps/observability/jarvis-live/dashboard-jarvis-live.json` as usual:

| Panel | Query |
|---|---|
| Seats in use and waiting, against 8 | `vllm:num_requests_running`, `vllm:num_requests_waiting` |
| Tokens per second | `rate` of `generation_tokens_total` and `prompt_tokens_total` |
| KV cache | `vllm:kv_cache_usage_perc` |
| Wait for a seat, p50 and p95 | `histogram_quantile` on `request_queue_time_seconds_bucket` |
| Share of token gaps over 1 s | `inter_token_latency_seconds_bucket` |
| One-token probe result and time | `max_over_time(probe_duration_seconds{llm_check="inference"}[6m])` |

The last panel doubles as a queue measure from the caller's side, and it is where the 10 s answer
shows. Range functions are needed there, because a 5-minute sample sits on the edge of Prometheus's
default 5-minute lookback.

## Rules

In `apps/observability/llm-path/rules.yaml`, with a new Flux Kustomization modelled on
`observability-promoted-services`.

```yaml
- record: llm_path:inference_down:20m
  expr: >-
    (count_over_time(probe_success{job="llm-path", llm_check="inference"}[20m]) >= 3)
    and (max_over_time(probe_success{job="llm-path", llm_check="inference"}[20m]) == 0)
- record: llm_path:inference_down:60m
  expr: >-
    (count_over_time(probe_success{job="llm-path", llm_check="inference"}[60m]) >= 10)
    and (max_over_time(probe_success{job="llm-path", llm_check="inference"}[60m]) == 0)

- alert: LlmProxyDown
  expr: probe_success{job="llm-path", llm_check="proxy"} == 0
  for: 3m
  labels: {severity: critical, notify: ntfy, page: "true"}

- alert: LlmInferenceDown
  expr: >-
    (
      (llm_path:inference_down:20m unless on () (sum(rate(vllm:generation_tokens_total[5m])) > 0))
      or llm_path:inference_down:60m
    )
    unless on () (min_over_time(probe_success{job="llm-path", llm_check="proxy"}[10m]) == 0)
  for: 2m
  labels: {severity: critical, notify: ntfy, page: "true"}
```

Each part earns its place:

- **20 minutes** is set by the cold load, not the queue. 8-13 min plus about 80 s of init is 14.5 min
  at worst, so a flip or a self-healing restart pages nobody. Past that, a person is needed.
- **The token guard** stops a queue paging. If the vLLM series vanish, the guard goes empty and the
  page goes through, which is the safe direction.
- **The 60 minute branch** means the probe can always page alone, so a wedged scheduler with one
  stream still producing tokens is not hidden forever. An hour with no seat for anyone new is an
  outage for interactive callers anyway.
- **The proxy clause** gives one page for a proxy outage. It looks back 10 minutes, because the
  inference window stays failed for a few minutes after the proxy recovers.
- **The sample counts** stop one failed probe paging when Prometheus comes back from a gap.

**No `jev: "true"` label.** Jev's incident agent calls LiteLLM, so during this fault it cannot work,
and it would add load to a struggling engine.

`promtool test rules` passes on all seven scenarios, against v3.5.0 (the cluster runs v3.14.0, so the
committed test pins that):

| Scenario | Result |
|---|---|
| Engine stops at 30 min | Silent at 40, `LlmInferenceDown` by 50 |
| Saturated: probes fail, tokens flow | Silent to 66 min, hard cap by 75 |
| Proxy down, both probes failing | `LlmProxyDown` by 24 min, inference silent for the whole 65 min |
| Cold load, worst phase | Silent |
| Engine gone, no vLLM series | `LlmInferenceDown` |
| One failed probe just after a Prometheus gap | Silent |
| Proxy restart under 3 min | Silent |

## Making it a page

A page is one Gotify message at priority 10. Everything else in Gotify today is priority 5.

Alertmanager has no Gotify receiver, and Gotify cannot read its webhook, so the existing
`ntfy-gotify-adapter` learns one new route. No new component. As it stands the adapter would not
work: its catch-all `POST /{topic}` hardcodes priority 5 and would push the raw JSON, and its
NetworkPolicy admits only `pi-durable`.

| Change | Detail |
|---|---|
| New `POST /alertmanager?priority=10` | Declared before the catch-all. Title from `annotations.summary`, message from `annotations.description`, which carries the runbook line. Ignores resolved alerts. A Gotify rejection stays a 502, so Alertmanager retries |
| `networkpolicy.yaml` | Also admit `observability`. `test_only_pi_durable_may_reach_it` changes with it |
| `VERSION` | 0.1.1 to 0.2.0, with the image tag, as that workflow requires |

The existing `POST /{topic}` keeps its behaviour, so `pi-durable` sees no change. Pages use the
adapter's existing token, so they appear under its Gotify application. That needs no manual step.

A new Alertmanager receiver and route, in front of the `ntfy` route:

```yaml
- receiver: gotify-page
  group_by: ["..."]
  continue: true
  matchers:
    - page = "true"
# receiver: webhook to the adapter's /alertmanager?priority=10, send_resolved: false
```

`send_resolved: false`, or the all-clear would wake you too. `continue: true` lets the alert carry on
to the existing `ntfy` route, because the rules keep `notify: ntfy`. That gives a normal-priority
record with the all-clear, and a copy if Gotify is the thing that failed. Still firing after 12 h,
the default `repeat_interval` pages again. That is the one exception to "one page per outage".

**Placement rule.** Prometheus, Alertmanager, the Blackbox exporter, Gotify and the adapter all get
the same required affinity: not `andrew-gamingpc`, and not on the proxy's node, or losing that node
would take the page with it. Postgres is already clear. By CPU requests Prometheus's 500m only fits
on `agent-a` once `agent-d` is excluded, at 97% requested and 20% real use. It was starved on that
node on 2026-10-15, so it gets watched there. The others need 60m or less. A test pins the rule for
all five.

**Watching delivery.** Alertmanager's failure metric has no per-receiver label, and it already reads
1,697 server errors from Jev's 503s, so it cannot show a broken page path. Instead, two more Probes,
`llm_check: page_path`, hit Gotify's `/health`, which pings its database, and the adapter's
`/healthz`. `LlmPagePathDown` goes to ntfy only, so the two paths watch each other.

"Exactly one page" means one priority-10 Gotify message. These also appear, and none of them wakes
anyone:

| Fault | Other message | When |
|---|---|---|
| Any page | The same alert on ntfy `infra_alerts`, default priority, then its all-clear | With the page |
| Whole Spark lost | `HomeTileDown` for `dgx-spark`, since the dashboard pod is on it | 5 min |
| Proxy down | `JevUnavailable`, if an alert reaches Jev while it is failing | 15 min |

The Spark message arrives before the page, so it cannot be inhibited, and it is useful: it is the
early hint that the host, not just the engine, is gone. Task 9's Gotify pushes are priority 5 and
need a live session, so they stay quiet at 3 AM.

## d) Runbook

Lands in `docs/observability.md`, with each first line also in the alert's `description` so it
reaches the phone.

| Alert | What it means | Check first | Who can act |
|---|---|---|---|
| `LlmProxyDown` | Every LLM client is failing. The proxy is gone, unreachable, or taking over 2 s | `kubectl -n open-webui get pods -l app=litellm-proxy -o wide`, then that node's `Ready` | Andrew. `kubectl rollout restart` is safe, and streams die anyway. A dead node means waiting for eviction, or deleting the pod |
| `LlmInferenceDown` | No new request has been answered, or tokens have stopped. Every client is affected, and there is no working fallback | Dashboard, LLM engine row. Tokens at 0 with seats held: stuck. No series: `kubectl -n jarvis-ai get pods -o wide`, then the Spark node | Andrew only. Jev cannot help, since it needs this path. Restart `vllm-qwen3` only if stuck: it costs 8-13 min and kills every stream |
| `LlmProbesMissing` | Coverage is lost: a Probe or the scrape went quiet. Nothing is known to be broken | `kubectl -n observability get probes,podmonitors`, then Flux status for `observability-llm-path` | Andrew, next working hour. Not a 3 AM task. While the scrape is missing, a busy engine can page falsely |
| `LlmPagePathDown` | A page could not reach the phone right now. The LLM path may be fine | `kubectl -n gotify get pods -o wide`, `kubectl -n ntfy-gotify-adapter get pods`, then Postgres, since Gotify's `/health` needs it | Andrew, next working hour. Until fixed, pages reach ntfy only |

Before a planned Flash restart, silence `LlmInferenceDown` if the load may run past 14.5 minutes. A
cold load that overruns will page, and arguably should.

## Delivery

Homelab PRs go to Forgejo, one change each. TDD per `TDD.md`: tests go in
`tests/tdd/test_llm_path_alerts.py`, in the style of `test_home_status.py`, plus the promtool cases.

**Gates, per the `Tasks.herd` sequence, as of 2026-11-01 18:50 UTC:**

- **Voice arm: not reported yet.** Its run was at pair 187 of 200 at 18:47 UTC, and no results PR
  exists. Steps 1a to 4b send no inference, so they do not wait for it.
- **Reserved path (decision 10): decided.** A separate small model on pve, alias `interactive`,
  falling back to `heavy`, in [docs/plans/fast-tier.md](fast-tier.md). Which model stays provisional.
  This design does not change: the one-token probe stays on `heavy`.
- **Step 5's inference Probe waits for `chore/fast-tier-comparison` to finish,** so it is not in that
  run's numbers. The comparison follows the voice arm, so this covers that run too.
- **Task 12's best-effort alert is not part of this build.** See
  [Limits and follow-ups](#limits-and-follow-ups).

Step 3 is the last check before anything sends inference.

| # | Where | Change | Rollback |
|---|---|---|---|
| 1a | homelab | Prometheus to the placement rule. One WAL replay, on its NFS volume. Watch it after the move: it was starved on `agent-a` on 2026-10-15 | Revert |
| 1b | homelab | Alertmanager and the Blackbox exporter to the placement rule | Revert |
| 1c | homelab | Gotify and the adapter to the placement rule. Gotify is `Recreate`, so it blinks once | Revert |
| 2 | homelab | PodMonitor, then the Flux wiring | Revert |
| 2b | jarvis-voice, homelab | Dashboard row (`chore(server):`, no release), then the copy | Revert |
| 3 | Local | Run the pinned proxy image against a stub engine. Prove: `heavy` honours `max_tokens: 1`, hanging up cancels upstream, retries and fallback are off, and no cooldown follows repeated failures | None needed |
| 4a | homelab | Adapter `POST /alertmanager`, NetworkPolicy, 0.2.0 | Revert, pin 0.1.1 |
| 4b | homelab | Blackbox modules, then the `gotify-page` route | Revert |
| 5 | homelab | Probes and rules, `LlmProbesMissing`, `LlmPagePathDown`, tests. Held until `chore/fast-tier-comparison` has finished. Then check `le="1"` rises by 12 an hour and no more | Revert. Deleting the inference Probe ends all traffic it sends, and comes first if that check fails |
| 6 | homelab | Runbook in `docs/observability.md`, and `docs/ntfy-gotify-adapter.md`, which says only `pi-durable` calls it | Revert |
| 7 | homelab | Dead-man's check below | Revert |
| 8 | Both | The simulations below, and a line in `docs/plans/llm-path-latency.md` for step 2 | Revert the break PRs |

**Dead-man's check.** One step in the hourly `flux-staleness.yml`: `Watchdog` must be active in
Alertmanager, which proves Prometheus evaluates rules and Alertmanager receives them. On failure it
posts to Gotify's public `/message`, using the same token, loaded from 1Password as that workflow
already does. It sends priority 5, because it repeats hourly until fixed and is lost coverage, not an
outage. It would have caught the 23.8 h gap. It would not catch a 10 minute one.

## Done-when check

**"vLLM metrics are graphed":** the row populates on the `Jarvis Live` dashboard, and `max_tokens`
`le="1"` rises by 12 an hour.

**"A simulated outage on each path raises exactly one page":** neither run may touch the real proxy
or Flash, since both serve every client. A break PR repoints the Probe targets at an unused port,
which fails the probes the way a real outage does while no client notices. Revert afterwards.

| Run | Break | Expect | Fail if |
|---|---|---|---|
| Proxy path | Both Probes pointed away, held 70 min | One Gotify priority-10 `LlmProxyDown`, in 3-4 min | A second page, or anything from `LlmInferenceDown` |
| Engine path | Inference Probe only, held 70 min | One Gotify priority-10 `LlmInferenceDown`, at 62-70 min | A page before 60 min, or a `LlmProxyDown` |

The engine run lands on the hard cap, because real traffic keeps the token guard on. So the 20 minute
branch is proven by promtool, not end to end. Faking it live would need the engine stopped. The next
real Flash restart gives one more check: nothing should page. I will count both runs from the
Alertmanager and adapter logs, Gotify's message list and the phone, and report only what actually
arrived.

## Decisions, 2026-11-01

1. **"Frozen" means a stopped engine.** Page in 20-25 minutes. The 3.4 s freeze and a 10 s answer go
   on the dashboard.
2. **A page is a Gotify message at priority 10,** not ntfy.
3. **Prometheus moves off the gaming PC.** Choosing Gotify extends this to Gotify and the adapter,
   which run there too, and Alertmanager and the Blackbox exporter follow the same rule.
4. **The dead-man's check is in.** It reports to Gotify.
5. **Simulations do not break the real proxy or Flash.**

Two follow from those without needing a separate call: ntfy keeps a default-priority copy of each
page, and pages use the adapter's existing Gotify token.

## Limits and follow-ups

- **Nothing here has been sent through the real proxy.** The body, the regexp and `heavy`'s streamed
  `finish_reason` are proven on a stub only, and step 3 narrows that before step 5.
- **The cancel and cooldown behaviour is from reading code,** until step 3.
- **The replay is an estimate** from one calm 19.4 hours. The token guard is what makes the design
  safe, so it does not rest on those numbers.
- **Whether a page wakes you depends on the phone.** Priority 10 lands in the Gotify Android app's
  high-priority channel as I recall its code, not from a test here. That channel has to be allowed
  through Do Not Disturb, and the phone has to hold its Gotify connection at night. One `amtool alert
  add` with `page=true`, at an hour of your choosing, tests the whole path after step 4b. I could not
  confirm a phone is connected: Gotify's logs do not show it.
- **Nothing shows the adapter has carried a message recently.** Its logs show no POSTs, so step 4a
  starts with one direct test message.
- **The reserved path does not shorten this probe's queue.** Task 12 chose a separate model, so
  `heavy` keeps the agents' queue, and the timeout and 60 minute cap stay until that changes.
  Re-measure the probe's wait once interactive callers have moved, and tighten them only if it fell.
  The probe covers `heavy`, not `interactive`.
- **Task 12's `BestEffortTierDown` alert** is Task 12 step 6, not this task. It follows step 1 here
  and uses `apps/observability/llm-path/` without `page: "true"`. That directory and its Flux wiring
  arrive in step 2.
