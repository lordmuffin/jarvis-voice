# LLM gateway options

Task 13 in `Tasks.txt`. Evidence gathered 2026-10-31.

| | |
|---|---|
| **Status** | Paper research and inventory complete. **The measured comparison is outstanding.** Task 11's probe is on local branch `fix/llm-path-stalls` (`scripts/llm_probe.py`), not merged, no PR. Per the task's gate, no candidate was deployed or measured. |
| **Grayfield review** | Not done. It runs separately, so this doc is not yet at DONE. |
| **Decision you can make today** | Do not start a gateway migration. See [Bottom line](#bottom-line). |
| **Decision you cannot make yet** | Which gateway is best for voice. That needs the runs in [Measurement plan](#measurement-plan). |

Citation keys: `[H]` is `homelab-gitops` at `0f1906f5` (private repo, paths given). `[J]` is this repo at `79968d3`. `[M]` is measured in this session, see [What was run](#what-was-run-on-the-cluster). `[Sn]` is the [Sources](#sources) list. Vendor figures are labelled **vendor number**.

## Bottom line

1. **No evidence available today shows that replacing LiteLLM would fix the stall.** Evidence confidence: mixed, because no inference stream was measured here.
2. **New evidence points at the network path to the proxy pod, not at LiteLLM's software.** Over 13 minutes, a no-op endpoint answered in a maximum of 20 ms from inside the pod (n=1,555). The same endpoint reached over the network stalled for 3.6 to 11.4 s in half of attempts during the same window. A gateway swap in the same place inherits that path.
3. **Provisional recommendation: keep LiteLLM.** The proxy pod moved to an agent node at about 18:05 UTC, and the ingress stalls did not appear in one 20-request run after the move. Task 11 still has to re-run its probe, because that run used no inference. This stays provisional because the numbers the task asks for do not exist yet.
4. **If LiteLLM still misses the SLO after that, Bifrost is the only replacement that survives the paper screen.** Kong, self-hosted Portkey and Envoy AI Gateway are dropped below. Voice direct to vLLM stays a measured arm, and needs your sign-off to ship.

Decisions needed from you, none of them blocking Task 11's next step:

| Decision | Recommendation |
|---|---|
| Approve moving the proxy pod to an agent node as Task 11's next test | **Approved and done 2026-10-31.** Homelab PR #832 merged at 18:04 UTC and the pod now runs on `agent-d`. First result is in [Where the stall appears to be](#where-the-stall-appears-to-be). Task 11's probe result is still outstanding. |
| Sign off a direct-to-vLLM path for voice | Not yet. Moving the pod is cheaper and keeps the rule. Costs are in [The direct-to-vLLM bypass](#the-direct-to-vllm-bypass). |
| Add an alert on proxy reachability | Yes, whichever gateway wins. Nothing pages today. |

## Where the stall appears to be

Testing the brief's hypothesis that a 5 s stall is a specific bug, config or load problem, not inherent gateway overhead. These tests used no-inference requests, so they cost the Spark nothing. They are **not** Task 11's probe.

| Test `[M]` | Result |
|---|---|
| `/health/liveliness`, loopback inside the proxy pod | n=1,555 over 13 min. p50 1.5 ms, p99 2.2 ms, max 20 ms. None over 250 ms. |
| `/v1/models`, loopback inside the proxy pod | n=26. p50 7 ms, max 8 ms. |
| Same period, `/health/liveliness` through the ingress from a workstation | Run 1: 10 of 20 over 1 s (3.6 to 11.4 s). Run 2: 10 of 20 over 1 s (max 5.9 s). All of the delay came after TLS completed, so it is not client DNS or the TLS handshake. |
| Control: another service on the same ingress, same window | 0 of 20 over 1 s. Max 0.23 s. Its pod runs on a different node. |
| `/health/liveliness` via ClusterIP from a `jarvis-live` pod on `agent-d`, no ingress | 6 stalls of 4.6 to 11.1 s in a 70 s run. The run hit its time limit, so there is no denominator. |
| Hostname lookup of the vLLM Service from inside the proxy pod | Repeated lookups of 4.9 to 6.2 s. The run hit its time limit, so there is no denominator. The brief's "suspiciously steady 5.5 s" falls in this range. |
| UDP DNS to each of 3 CoreDNS replicas, 75 queries, 1.5 s timeout, from the proxy pod on `server-d` | 11 and 9 lost in two runs (15%, 12%). |
| Same, from another pod on `server-d` | 4 lost (5%). |
| Same, from a pod on `agent-d` | 0 lost. |
| Prometheus, TCP retransmits per second over 1 h | `server-b` to `server-e`: 3.9 to 5.0. `server-a`: 1.8. Agent nodes: 0.3 to 1.1. Conntrack at or under 4.5%. No interface drops on server nodes. |

All tests above ran with the pod on `server-d`, before the move. After the move to `agent-d`:

| Test `[M]`, 19:17 UTC, about 70 minutes after the move | Result |
|---|---|
| `/health/liveliness` through the ingress from a workstation, same test as above | 0 of 20 over 1 s. Max 0.44 s. |

That is 0 of 20 against 10 of 20 in each earlier run. It is a single run at a different time of day, and neither the control nor the other tests were repeated, so it supports the network hypothesis without proving it.

What this shows:

- **LiteLLM's event loop was not blocked in this window.** Every sample was served in 20 ms or less, including across a 17:15 UTC tick of the `/health` traffic described below. This argues against the callbacks and the single process as the cause of *these* stalls.
- **The delay is between other pods and the proxy pod.** It affects a route that does no work, so it cannot be model load, pre-call checks or a callback.
- **The proxy's own outbound traffic suffers too.** Lookups of the vLLM Service stall, and the backend connection crosses the same network.

What it does not show. Confidence: **mixed**.

- **No inference stream was measured.** This is a stall on a control path that looks like the LLM stall. That they share a cause is a hypothesis, and Task 11 owns it.
- **It does not isolate one fault.** Loss differs between two pods on `server-d`, and retransmit rates are similar on four server nodes, so this is not cleanly "one bad node". Those rates are absolute, not normalised for traffic. Server nodes also carry etcd.
- **It does not explain the direct-to-vLLM stall** in the brief (1 of 12). That path may have a second cause, such as load on the shared model.
- **It fits two unexplained results from the brief, without proving either.** Stalls after the first text would fit TCP retransmits mid-stream. Short requests not stalling would fit fewer packets at risk.

The brief's own numbers are also thinner than they look. 5 of 12 against 1 of 12 gives a Fisher exact p of 0.155 if the arms are treated as independent, with Wilson 95% intervals of 19 to 68% and 1 to 35%. They overlap, so that run alone does not establish that the gateway adds stalls.

**Note for Task 11.** The `llm-probe` pod ran on `server-d`, the node that shows loss, so runs from that pod started on an affected node, which can hide the difference between arms. A new `llm-probe` pod was running on `agent-d` at 19:17 UTC. Keep the two sets of runs apart. This session's traffic also overlapped its runs, see [What was run](#what-was-run-on-the-cluster).

## Inventory

### What the proxy does today

Deployed: LiteLLM 1.104.2, one pod, one process, namespace `open-webui` `[H: apps/ai/litellm-proxy.yaml]` `[M]`. Active Spark profile: `flash-embed`, serving one chat model `[H: bare-metal/dgx-spark/models.yaml]` `[M]`.

| Function | Today | State on 2026-10-31 |
|---|---|---|
| Aliases | 17 model names `[M]` | Working where a backend exists. |
| Role routing | Aliases target `vllm-role-*` Services, so a checkpoint swap needs no proxy edit `[H]` | `heavy` has an endpoint. `fast`, `vision`, `whisper` have none `[M]`. |
| Availability fallbacks | `fast-coder→qwen-coder-32b`, `fast-chat→heavy`, `llama3-gaming-pc→llama3-jarvis`, `heavy→qwen-coder-32b`, `jev→jev-cpu` `[H]` | **`heavy`'s fallback is dead:** `qwen-coder-32b` has no endpoint under this profile `[M]`. **`qwen3.8-flash-next` has no fallback** `[H]`, and the proxy logs that `[M]`. `fast-chat→heavy` carries real traffic. |
| Context-window fallbacks | Six one-way rules plus `enable_pre_call_checks` `[H]` | The config calls them no-ops today `[H]`. Most target `qwen-coder-32b`, which has no endpoint. |
| `auto` router | LiteLLM `complexity_router`, heuristic scorer, for human chat `[H: docs/litellm-auto-router.md]` | Runs. Its SIMPLE tier is `fast-chat`, which has no backend, so it falls back to `heavy`. |
| CNPG decision log | `router_log.py` writes decisions to Postgres `[H]` | **Off.** The DSN secret does not exist and its ExternalSecret is deliberately not applied `[H: apps/secrets/kustomization.yaml]` `[M]`. By design, the logger then disables itself. |
| `finish_reason` fix | Callback rewrites `stop` to `tool_calls`, for the 27B model `[H]` | **Not reproduced on Flash:** vLLM returned `tool_calls` correctly in 4 of 4 tries, 2 streaming and 2 not `[M]`. Small sample. Untested on 27B. |
| `qwen-shim` sidecar | Forces a literal model id on two aliases `[H]` | **Those aliases return 404**, because the active model does not claim that id `[M]`. Its own header doubts it is still needed `[H]`. It also **buffers streams**, see below. |
| `hosted_vllm` | Used by most Spark aliases `[H]` | Working for `heavy`. |
| `ollama` | `fast-chat`, `fast-coder`, `llama3-*` `[H]` | Gaming PC Ollama at 0 replicas `[M]`. `llama3-jarvis` reachability not checked. |
| `openrouter` | `opus-4.8` only, never a fallback `[H]` | Requires `OPENROUTER_API_KEY`. Traffic not measured. |
| Audio transcription | `whisper-large-v3` `[H]` | Role has no endpoint under this profile `[M]`, and Open WebUI STT depends on it `[H: docs/open-webui.md]`. |
| Embeddings | `embed` `[H]` | One `POST /v1/embeddings` in 6 h, and it returned 500 `[M]`. No caller on this repo's `main` `[J]`. The proxy comment names transcript search as the user `[H]`. |
| Keyless LAN access | Explicit opt-out of a master key `[H]` | Clients send a placeholder. Pinned by a test `[H: scripts/litellm_proxy/tests/test_router_config.py]`. |
| Anthropic `/v1/messages` | Translates onto chat-completion backends `[H: apps/ai/hermes-runtime.yaml]` | **Not in the brief's list, and a hard requirement.** Zero calls in the 6 h log window `[M]`, so the dependency is by config. |
| Vision | `qwen2.5-vl` `[H]` | Role has no endpoint under this profile `[M]`. |

Traffic is small. 341 POSTs over 6 h, about 57 an hour or 0.016 per second `[M]`. The pod was 12 h old, so a full day was not available. This is one window, not a baseline.

The shim's buffering was reproduced locally on Python 3.12: an upstream sent 10 chunks over 1.8 s, and the client received them as one read at 2.01 s `[M]`. Direct, the client received 10 reads. The cause is `read(65536)` on a chunked response. It affects only the two shim aliases, and no working traffic uses it today.

### Who depends on what

| Client | API | Alias or route | Source |
|---|---|---|---|
| `jarvis-live` copilot, spoken requests | OpenAI, JSON schema, **not streaming** | `fast-chat`, unless `VOICE_MODEL` is set | `[J: server/deploy/k8s/base/configmap.yaml, server/src/jarvis_live/llm/client.py]` |
| `jarvis-live` final note | OpenAI | `heavy` | `[J]` |
| `jarvis-live` STT | **Bypasses proxy** | Direct to the whisper role | `[J]` |
| Open WebUI | OpenAI, STT | `auto` (admin setting, not verified), `whisper-large-v3` | `[H: apps/ai/open-webui/deployment.yaml]` |
| Paperclip agents (7) | **Anthropic, via Claude Code** | `qwen3.8-flash-next` | `[H: scripts/paperclip_agents/agents.d/]` |
| `hermes-runtime` | **Anthropic, hardcoded** | `heavy` | `[H: apps/ai/hermes-runtime.yaml]` |
| Hermes tenant | OpenAI | `heavy` | `[H: apps/ai/hermes/tenants/tenant-alpha/]` |
| Kai, Forge, Atlas LXC agents | OpenAI | Literal 27B id, **which 404s under this profile**. Deployed state not checked. | `[H: lxc-services/pve3/*/config.yaml]` |
| `opencode-runtime` | OpenAI, slash-free names | `qwen-coder-32b`, `heavy`, `opus-4.8`, `llama3-jarvis` | `[H: apps/ai/opencode-runtime.yaml]` |
| aider | OpenAI | `qwen-coder-32b` | `[H: bare-metal/jarvis-v1/setup-aider.sh]` |
| Pi Durable, digest, runner-01 | OpenAI | `qwen3.8-flash-next`, `local-default` | `[H: apps/pi-harness/, lxc-services/pve2/]` |
| Pi sandbox pattern | OpenAI | `qwen-coder-32b` | `[H: apps/pi-harness/pattern-b-sandbox/]` |
| Jev, Pi guard | OpenAI, fails closed | `jev`, `jev-cpu` | `[H: apps/ai/jev/deployment.yaml]` |
| Facts tick | `GET /health` every 15 min | All models | `[H: apps/ai/jev/facts-tick-configmap.yaml]` |
| capture-pipeline | OpenAI; **STT and VLM bypass proxy** | `heavy` | `[H: apps/ai/capture-pipeline/configmap-pipeline.yaml]` |
| JobNavigator | OpenAI | `heavy`, others | `[H: apps/services/jobnavigator/deployment-backend.yaml]` |
| Open Notebook | OpenAI-compatible credential set by hand | Not verified | `[H: apps/ai/open-notebook/README.md]` |
| Honcho | Example config only, names an alias the proxy lacks | Deployed state not checked | `[H: bare-metal/dgx-spark/honcho/.env.example]` |

Out of scope: the separate Agent Control Plane LiteLLM (`apps/ai/litellm.yaml`) and the legacy `src/jarvis_voice/`, which defaults to the decommissioned proxy host.

`GET /health` matters because LiteLLM documents that each call sends a real test request to every deployment `[S1]`. That is why two 404s land on the shim every 15 minutes `[M]`, one per shim alias. This `/health` load did not stall the event loop in the window sampled `[M]`, but it is pointless load on a shared model. LiteLLM offers `background_health_checks` to avoid it `[S1]`.

## Hard requirements

The brief asks to drop candidates that fail a hard requirement, without listing them. These are derived from the inventory. Correct them if you disagree.

| # | Requirement | Why |
|---|---|---|
| R1 | OpenAI chat with streaming, structured output, and unmodified `chat_template_kwargs` | Nearly every client. `jarvis-live` turns thinking off this way. |
| R2 | Anthropic `/v1/messages` reaching local vLLM | 8 clients, and `hermes-runtime` hardcodes it. |
| R3 | Server-side aliases, including slash-free names | Clients must not learn checkpoints. opencode cannot use a slash. |
| R4 | Server-side, one-way fallback chains | Your rule: never silently downgrade. Clients do not send fallback lists. |
| R5 | `/v1/embeddings` and `/v1/audio/transcriptions` | Open WebUI STT, transcript search. |
| R6 | Runs keyless on the LAN | Otherwise this is also an auth migration across every client. |
| R7 | Self-hosted, and R1 to R6 are free | Homelab, no licence budget. |
| R8 | Maintained: an upstream push within 90 days | You would run this at the centre of everything. |

**Downgraded from the brief's list.** None of these should decide a swap:

- `auto` router. It is opt-in, and today has two effective rungs, one of which has no backend.
- Decision log. It is off.
- `finish_reason` fix. Not reproduced on Flash. Re-test under `coder`.
- `qwen-shim`. Its routes 404.
- Context-window fallbacks. No-ops today.

## Candidates

| Candidate | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | Result |
|---|---|---|---|---|---|---|---|---|---|
| LiteLLM tuned | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Finalist** (incumbent) |
| Voice direct to vLLM | ✅ | n/a | n/a | ❌ | n/a | ✅ | ✅ | ✅ | **Finalist**, voice only, needs your sign-off |
| Bifrost | Paper | Paper | Paper | Paper | Paper | Paper | ✅ | ✅ | **Finalist** for a scratch test |
| Envoy AI Gateway | Paper | ❌ | Paper | Paper | Paper | Unknown | ✅ | ✅ | **Dropped** |
| Self-hosted Portkey | Paper | Unknown | Unknown | Paper | Paper | Unknown | ✅ | ❌ | **Dropped** |
| Kong AI Gateway | Paper | Unknown | Paper | ❌ | ❌ | Unknown | ❌ | ✅ | **Dropped** |

"Paper" means the docs or source say yes and nothing here tested it. Evidence for Bifrost's row is mixed: much of it is vendor documentation.

### Dropped

**Kong AI Gateway: fails R4, R5 and R7.**

- Fallback lives in AI Proxy Advanced, which Kong's docs say is "only available as part of our AI Gateway Enterprise offering" `[S7]`.
- Embeddings and transcription routes arrive in Gateway 3.11 `[S8]`, and the newest release in the open repo is 3.9.4 (2026-10-12) `[S9]`.
- Secondary sources state that fully self-managed AI Gateway needs a Gateway Enterprise licence `[S10]`.

Evidence: strong for R4 and R7, primary sources. For R5, it is an inference from combining two primary sources, not a statement by Kong.

**Self-hosted Portkey: fails R8.**

- The last stable release is v1.15.2 (2026-01-12), and the repo's last push is 2026-05-25 `[S11]`.
- Gateway 2.0 remains pre-release `[S12]`.
- Palo Alto Networks announced its acquisition on 2026-04-30 and will use Portkey as the gateway in Prisma AIRS `[S13]`. Sources disagree on the closing date.

The code itself is not the problem. `src/index.ts` has `/v1/messages`, embeddings and audio routes `[S11]`. No source says the project is abandoned. Dropping it is a judgement on five months of silence under a new owner, and a push to the repo would reverse it.

**Envoy AI Gateway: fails R2 as documented.** It has been renamed Agent Router, and the repo now lives at `theagentrouter/agent-router` `[S14]`.

- The Anthropic endpoint lists Anthropic-family providers. The compatibility table marks most OpenAI-compatible providers as unsupported, and the page's two lists conflict for OpenAI itself `[S15]`.
- It needs Envoy Gateway 1.9.2 or later and Gateway API 1.6.1 `[S16]`. This cluster has Gateway API CRDs but no GatewayClass `[M]`, so it adds a second data and control plane beside Traefik.
- A possible reversal: vLLM itself serves `/v1/messages` `[S17]`, so an Anthropic-schema backend might avoid translation. No document says this works, and it was not tested.

Licence is Apache-2.0, latest v1.2.0 (2026-10-07) `[S14]`.

## Finalists

Migration costs are **this author's estimates**, with no external source. All three share a sole operator.

### LiteLLM tuned

| | |
|---|---|
| **Migration cost** | Hours per change, one PR each, all reversible. |
| **What breaks** | Nothing by design. Each change below needs Task 11's before and after numbers. |
| **Licence** | MIT outside `enterprise/` `[S2]`. The Prometheus docs do not mark `/metrics` as enterprise-only `[S3]`, but LiteLLM's enterprise page returned 404 here, so that boundary is **unverified**. |
| **3 AM** | Nothing pages. See below. |

Tuning candidates, in order of expected value. None is proven to help.

1. Move the pod off the server nodes. No scheduling constraint exists today `[H]`.
2. Raise resources. The pod requests 100m CPU and has a 1 Gi limit `[H]`. LiteLLM's guide asks for 1 vCPU and 4 Gi per worker, set as both request and limit `[S4]`. Live use was 736 Mi `[M]`.
3. Point the facts tick at something other than `/health`, or turn on `background_health_checks` `[S1]`.
4. Add `LITELLM_DETAILED_TIMING=true` on the test run, for per-phase timing headers `[S5]`. Sample `/health/backlog` beside each request `[S5]`.
5. Move readiness to `/health/readiness` `[S4]`.
6. Remove the two 404ing aliases and the shim, after repointing the three LXC agents.

Worth knowing: `litellm` 1.82.7 and 1.82.8 on PyPI were compromised on 2026-03-24 `[S6]`. No official postmortem was found, so that account is from secondary sources. The manifest comments record 1.82.3 then 1.104.2 here `[H]`, and the image is digest-pinned. 1.106.6 is the latest upstream `[S2]`. Whether it has a Docker image was not checked, and that has caught this repo before `[H]`.

### Voice direct to vLLM

| | |
|---|---|
| **Migration cost** | About a day. `voice_model` already exists, but one `LiteLLMClient` takes one base URL, so voice needs its own `[J: server/src/jarvis_live/app.py, config.py]`. Plus your sign-off. |
| **What breaks** | The all-calls rule, and everything in [The direct-to-vLLM bypass](#the-direct-to-vllm-bypass). |
| **Licence** | None. It uses what is already deployed. |
| **3 AM** | During a profile flip, voice gets connection refused for the 8 to 13 minute cold load, with no retry. Nothing pages. The gateway stays up, so this is a smaller blast radius: voice only. |

### Bifrost

| | |
|---|---|
| **Migration cost** | Estimate 2 to 4 days. Port the model list, fallbacks and `scripts/litellm_proxy/tests/`, and re-point Anthropic clients to an `/anthropic` prefix `[S18]`. Update `validate-model-profiles.py`'s LiteLLM cross-check `[H]`. |
| **What breaks** | `auto`, for which no equivalent was found: routing rules are CEL expressions on request fields `[S19]`, not a complexity scorer. `router_log.py` and any `x-litellm-*` header users, such as the Jev docs `[H: docs/jev.md]`. Any LiteLLM-specific callbacks. |
| **Licence** | Apache-2.0 `[S20]`. Vendor pages list adaptive load balancing, clustering, guardrails, log exports and zero-downtime deployment as Enterprise, with no public price `[S21]`. So **more than one replica is likely gated**, which leaves the same single point of failure. |
| **3 AM** | Nothing pages by default. You would debug an unfamiliar Go service, with no local operating history, during an outage of everything. Upstream moves fast: v2.0.0 carried breaking changes, including closing a path that let an unauthenticated caller register a native plugin `[S22]`. That matters on a keyless proxy. |

On paper it fits:

- Native `vllm`, `ollama` and `openrouter` providers. Embeddings on all three, transcription on vLLM `[S23]`.
- Server-side aliases `[S24]`, and routing rules that carry fallbacks `[S19]`.
- A `HTTPTransportStreamChunkHook` plugin hook, if the `finish_reason` fix proves necessary `[S25]`.
- The Helm chart turns inference auth on by default, and the raw binary leaves it open `[S26]`, so R6 needs a setting, not a change.

**Unverified, and could fail it:** `/anthropic/v1/messages` reaching vLLM with tool use intact. Vendor guides show the pattern for other providers, none for vLLM `[S18]`. Test this first, because 8 clients depend on it.

### The 3 AM answer today

This applies to all three finalists until someone adds an alert.

- **Who gets paged: nobody.** Only `notify: ntfy` alerts reach a human `[H: apps/observability/kube-prometheus-stack/release.yaml]`. No rule under `apps/observability` covers the proxy `[H]`.
- **Kubernetes will not save you from this failure.** Liveness uses `/health/liveliness` `[H]`, which LiteLLM documents as a process check with no dependencies `[S1]`. It stayed healthy throughout the stalls measured here `[M]`.
- **There is one replica** and no PodDisruptionBudget `[H]`. A second replica needs Redis, per LiteLLM's guide, to share router state `[S4]`.
- **The triage path shares the failure.** Alerts labelled for Jev are triaged by LiteLLM `jev` `[H]`. If the proxy is down, Jev fails closed `[H]`, and only alerts also labelled `notify: ntfy` reach you.
- **You find out by something feeling broken.** Blast radius is every client in the table above.

## The direct-to-vLLM bypass

The rule appears as "every call goes through LiteLLM" `[H: apps/pi-harness/pattern-a-statefulset/statefulset.yaml]` and "everything through litellm-proxy" `[H: docs/open-webui.md]`. It already has exceptions: `jarvis-live` and capture-pipeline both reach Whisper directly `[J]` `[H]`. That is precedent, not permission. **This needs your explicit sign-off, and has not been built.**

What it costs, stated as it stands today:

| Lost | Cost now | Cost later |
|---|---|---|
| Availability fallback | **Effectively nothing.** `qwen3.8-flash-next` has none, and `heavy→qwen-coder-32b` has no endpoint. Going direct to `heavy`'s model replaces `fast-chat→heavy`. | Under `coder`, `heavy→qwen-coder-32b` works again and voice would not get it. **This is the real cost, and it returns the moment the profile changes.** |
| Retry and cooldown | One retry, 60 s cooldown `[H]` | Same. Voice must handle failure itself. |
| Central visibility | Voice leaves the proxy's logs and any future metrics | Task 9's numbers must come from `jarvis-live` itself |
| Alias indirection | Must name a served model. Use `qwen3-heavy`, which both Flash and 27B claim `[H]`. | A profile that drops that name breaks voice with no fallback |
| The rule itself | One more exception to a rule that keeps model access auditable | Exceptions compound. Task 6's local-only guarantees would need to cover this path. |

The stronger objection is that the bypass may solve the wrong problem. If the fault is the proxy pod's network path, moving the pod fixes every client and keeps the rule. The bypass fixes only voice.

## Vendor numbers and evidence quality

| Claim | Who says | What it is worth here |
|---|---|---|
| Bifrost adds 11 µs at 5,000 RPS on t3.xlarge, 59 µs on t3.medium | **Vendor number** `[S20]`. The same README also says under 200 µs. | Throughput 300,000 times this traffic. The README's two figures do not agree. |
| Bifrost p99 1.68 s, LiteLLM p99 90.72 s, at 500 RPS | **Vendor number**, Maxim-run `[S27]` | 30,000 times this traffic. It measures saturation, which this proxy never reaches. |
| "54x faster" | **Vendor number**, per the brief | Same. |
| LiteLLM overhead p50 2 ms, p95 8 ms, p99 13 ms at 1,170 RPS | **Vendor number**, mock backend, 4 instances `[S28]` | Supports the brief's point. It is the maker's own figure, and does not measure streaming stalls. |
| Portkey "<1 ms" | **Vendor number**, no method given `[S11]` | Not usable. |
| LiteLLM 28 s against Bifrost 50 ms at 1,000 concurrent users | One developer's test, cited in a roundup `[S29]` | Single setup, no controls, nowhere near this load. |

**No independent like-for-like benchmark was found**, in agreement with the brief. An extended search found only vendor posts and single-author tests `[S29]`. Overhead in microseconds cannot be the deciding factor when the problem measured here is seconds. At 0.016 requests per second, all gateways on this list are far from saturation.

## Measurement plan

Runs once Task 11's probe merges. Nothing here has run.

| Arm | Path |
|---|---|
| A | Direct to vLLM, the lower bound for any gateway |
| B | LiteLLM as deployed |
| C | LiteLLM tuned, using whatever Task 11 finds |
| D | Bifrost, scratch |

Rules:

1. **A is a floor.** If A stalls, no gateway can fix it, and the question becomes priority or a reserved path, as Task 11's scope (f) says.
2. **Control placement.** Run the client and every gateway on the same node, and repeat on an agent node. Otherwise the bake-off measures node placement, and flatters whichever pod landed somewhere healthy. This is the largest risk to the result.
3. **Interleave all arms**, with identical bodies. Task 11's probe has two arms, so a third and fourth need adding.
4. **At least 200 per arm**, idle and busy, short and long prompts, per Task 11. With 200 samples, p99 rests on 2 or 3 requests, so report stall rate with its confidence interval as well.
5. **Add a real voice body:** non-streaming, JSON schema, thinking off, because that is what `jarvis-live` sends `[J]`. Streaming probes alone do not describe it.
6. **Use `qwen3.8-flash-next`,** not a shim alias.
7. **Bifrost stays scratch:** ClusterIP only, no hosted providers, removed afterwards. Task 13 forbids more than that unless Task 11 shows LiteLLM cannot meet the SLO. Before timing it, test R2 with tools.

## Decision rule

| Probe result | Decision |
|---|---|
| Stalls stop after a network or placement fix | Keep LiteLLM. Close this task. |
| A meets the SLO, C does not, D does | Bifrost is justified. Plan a migration, starting with the R2 test. |
| A meets the SLO, C and D do not | Gateways are not the fix for voice. Decide the bypass, with its costs above. |
| A misses the SLO | No gateway decision. Move to priority scheduling: vLLM has `--scheduling-policy priority` `[S30]`. That source is a forum answer, so verify it. A change of serving flags costs a cold load. |
| B and D are within the confidence interval | Keep LiteLLM. Migration cost has no return. |

## Fix regardless of gateway

Found during the inventory. None depends on this decision.

1. No alert on proxy reachability. Probe it **through the network**, since a loopback check stays green.
2. Kai, Forge and Atlas LXC configs default to a model id that 404s under this profile. Deployed state not checked.
3. `heavy` has a dead fallback, and `qwen3.8-flash-next` has none. The configs read as safer than they are.
4. The decision log is off, so Phase 2 of the `auto` plan has no data. Also, `fast-chat` has no backend, which logged 22 `ModelNotMappedError` tracebacks in 6 h `[M]`.
5. `docs/open-webui.md` still says no embeddings endpoint exists `[H]`. `embed` now does.
6. The shim buffers streams, if it is kept at all.

## Not verified

- Whether the network stalls are the LLM stalls. This is the central open question.
- Why loss differs between pods on one node, and whether the cause is the node, its host, or the overlay.
- Whether any of the `[M]` results hold at other times. All were taken in one 20 minute window.
- Deployed config of the LXC agents, Open Notebook and Honcho.
- The `auto` default in Open WebUI, which is an admin setting.
- Bifrost's `/anthropic` path to vLLM, its config storage, and how well it fits GitOps.
- LiteLLM's enterprise boundary for `/metrics`.

## What was run on the cluster

Read-only, 2026-10-31, about 17:03 to 17:20 UTC, plus one ingress run at 19:17 UTC. No configuration was changed from this session and nothing was deployed. The only change in the period is homelab PR #832, which moved the proxy pod through Flux at 18:04 UTC.

- Log reads, `kubectl get`, `top`, and Prometheus queries through a port-forward.
- About 1,600 `/health/liveliness` and `/v1/models` loopback requests inside the proxy pod.
- About 70 requests from a workstation through the ingress.
- 7 chat calls to a 404ing alias. These do no inference, but they put that broken route in cooldown once, for 60 s.
- 4 short tool-call completions direct to vLLM (under 80 tokens each), to test `finish_reason`.
- DNS timing from three pods, plus 40 or fewer ClusterIP requests from `jarvis-live`.

**Overlap with Task 11.** Its `llm-probe` pod started at 17:05:48, during this work, and it was found mid-run. One DNS test ran inside that pod before its purpose was noticed: roughly 150 UDP packets. The 4 completions ran on the shared model in that window too. The load is small, but Task 11 should know before trusting samples from 17:05 to 17:20 UTC. The 19:17 run also overlapped a new `llm-probe` pod that had started about 90 seconds earlier: 20 `/health/liveliness` requests over about 45 seconds, no inference.

## Sources

Accessed 2026-10-31. "Vendor" means written by the product's maker.

| Key | Source | Class |
|---|---|---|
| S1 | [LiteLLM health checks](https://docs.litellm.ai/docs/proxy/health) | Vendor docs |
| S2 | [LiteLLM LICENSE](https://github.com/BerriAI/litellm/blob/main/LICENSE), [releases](https://github.com/BerriAI/litellm/releases) | Primary |
| S3 | [LiteLLM Prometheus](https://docs.litellm.ai/docs/proxy/prometheus) | Vendor docs |
| S4 | [LiteLLM production guide](https://docs.litellm.ai/docs/proxy/prod) | Vendor docs |
| S5 | [LiteLLM latency troubleshooting](https://docs.litellm.ai/docs/troubleshoot/latency_overhead) | Vendor docs |
| S6 | [Comet](https://www.comet.com/site/blog/litellm-supply-chain-attack/), [PyPI Stats](https://pypistats.com/blog/litellm-pypi-supply-chain-compromise-how-a-popular-llm-proxy-became-a-credential-stealing-backdoor-march-24-2026) | Secondary. No official postmortem found. |
| S7 | [Kong AI Proxy Advanced](https://developer.konghq.com/plugins/ai-proxy-advanced/) | Vendor docs |
| S8 | [Kong AI Proxy](https://developer.konghq.com/plugins/ai-proxy/) | Vendor docs |
| S9 | [Kong releases](https://github.com/Kong/kong/releases) | Primary |
| S10 | [Kosmoy comparison](https://kosmoy.com/resources/blog/litellm-vs-kong-ai-gateway/), [API7 comparison](https://api7.ai/kong-ai-gateway-vs-litellm) | Secondary. API7 is a competitor. |
| S11 | [Portkey gateway repo](https://github.com/Portkey-AI/gateway), `src/index.ts`, GitHub API release and push dates | Primary. Latency claim is a vendor number. |
| S12 | [Portkey gateway README](https://github.com/Portkey-AI/gateway) | Primary |
| S13 | [Pulse 2](https://pulse2.com/palo-alto-networks-to-acquire-portkey-to-establish-ai-gateway-as-control-plane-for-autonomous-enterprise-agents/), [NAND Research](https://nand-research.com/palo-alto-networks-portkey-acquisition-anchors-its-agentic-security-stack/) | Secondary |
| S14 | [Agent Router docs banner](https://theagentrouter.ai/docs/capabilities/), [repo](https://github.com/theagentrouter/agent-router) | Primary |
| S15 | [Agent Router supported endpoints](https://theagentrouter.ai/docs/capabilities/llm-integrations/supported-endpoints/) | Vendor docs |
| S16 | [Agent Router compatibility](https://theagentrouter.ai/docs/compatibility/) | Vendor docs |
| S17 | [vLLM online serving](https://docs.vllm.ai/en/latest/serving/online_serving/) | Primary |
| S18 | [Maxim: Claude Code with other models](https://www.getmaxim.ai/articles/running-claude-code-with-openai-models-a-step-by-step-setup-guide/) | **Vendor** |
| S19 | Bifrost `docs/providers/routing-rules.mdx`, [repo](https://github.com/maximhq/bifrost) | Vendor docs |
| S20 | [Bifrost repo](https://github.com/maximhq/bifrost) | Primary. Benchmarks are vendor numbers. |
| S21 | [Bifrost pricing](https://www.getmaxim.ai/bifrost/pricing.md) | **Vendor** |
| S22 | Bifrost `docs/migration-guides/v2.0.0.mdx` | Vendor docs |
| S23 | [Bifrost supported providers](https://docs.getbifrost.ai/providers/supported-providers/overview) | Vendor docs |
| S24 | Bifrost `docs/providers/aliasing-models.mdx` | Vendor docs |
| S25 | Bifrost `core/schemas/plugin.go` | Primary |
| S26 | Bifrost `helm-charts/bifrost/values.yaml` | Primary |
| S27 | [Maxim: top LLM gateways](https://www.getmaxim.ai/articles/top-5-llm-gateways-for-2026-a-comprehensive-comparison/) | **Vendor** |
| S28 | [LiteLLM benchmarks](https://docs.litellm.ai/docs/benchmarks) | **Vendor** |
| S29 | [DEV roundup](https://dev.to/varshithvhegde/top-5-llm-gateways-in-2026-a-deep-dive-comparison-for-production-teams-34d2), [DEV buyer's guide](https://dev.to/pranay_batta/buyers-guide-to-pick-the-best-llm-gateway-in-2026-1epa) | Secondary, single authors. Some affiliated with vendors. |
| S30 | [vLLM forum, priority](https://discuss.vllm.ai/t/priority-in-batch-api/2376), [vLLM scheduler config](https://docs.vllm.ai/en/v0.14.0/api/vllm/config/scheduler/) | Forum answer is AI-generated. Scheduler docs are primary. |
