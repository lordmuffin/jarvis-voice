# LLM gateway options

Task 13 in `Tasks.herd`. Evidence gathered 2026-10-31, reworked the same day after Grayfield's review.

| | |
|---|---|
| **Status** | Research and inventory complete. **The gateway decision is made: keep LiteLLM.** No candidate comparison was run, and none is planned. This rework ran nothing: no inference, no cluster access. |
| **Grayfield review** | Done (#235, `REVIEW.md`, against `8fd0b3e`). Verdict: keep LiteLLM upheld, reasoning rejected. This version addresses its four blockers. It has not been re-reviewed. |
| **Task 11** | Probe and findings merged (#236). Cause named and reproduced. **Normal load since the pod move is still unmeasured** `[T11]`, and that run is the next step. |
| **Your decisions** | Listed in [What you still have to decide](#what-you-still-have-to-decide). |

Citation keys:

- `[T11]` is `docs/plans/llm-path-latency.md` on `main` (`c7c539e`). **Every `[T11]` figure is taken from that document and was not re-run for this rework.** Its clock times read 5 hours behind this doc's UTC: its 13:05 pod move is this doc's 18:05.
- `[Rev]` is `REVIEW.md`, including its own read-only checks of about 19:20 to 19:30 UTC. Those figures were not re-run either.
- `[TH]` is `Tasks.herd` at `6af3191`.
- `[H]` is `homelab-gitops` (private repo, paths given), read at `0f1906f5`. The proxy manifest facts in this rework were re-read at `2f524a0f`.
- `[J]` is this repo at `6af3191`.
- `[M]` is measured in the first session, see [What was run](#what-was-run-on-the-cluster).
- `[Sn]` is the [Sources](#sources) list. Vendor figures are labelled **vendor number**.

## Bottom line

1. **Keep LiteLLM.** You decided this, and the review upholds it `[Rev]`. Reopen only on the [reopen condition](#reopen-condition).
2. **The stall that matters is an engine freeze, and it is reproduced.** One long prompt freezes every stream on the model: `--max-num-batched-tokens 8192` at 2,420 tokens/s gives a 3.4 s step, and every request advances once per step `[T11]`. Idle, 0 of 550 requests stalled. With one client sending 24k prompts, direct stalled 10 of 10 and the gateway stalled 10 of 10, with total p50 of 10.89 s and 10.84 s `[T11]`. Those samples are small and the load was synthetic. Confidence in the mechanism: high. Confidence in how often it hits under normal load: low, because that run has not happened.
3. **The network fault is a separate fault, and it is unexplained.** Task 11 did not reproduce it and calls its evidence before-and-after, with the mechanism unknown `[T11]`. Retransmits also fell on nodes the pod was not running on `[Rev]`. See [The network fault](#the-network-fault-separate-and-unexplained).
4. **A replacement cannot recover the freeze.** LiteLLM adds about 6.5 ms `[T11]`, and any gateway sits in front of the same engine `[Rev]`. That figure covers `qwen3.8-flash-next` streaming, not the `fast-chat` route `[Rev]`.
5. **The gateway's traffic is small. The engine's is not.** The proxy saw 0.016 POSTs per second `[M]`, but the engine had requests waiting in 55% of 10 s samples over 14 hours of logs `[T11]`. Saturation is real, one hop past the gateway `[Rev]`.
6. **No number yet describes the request voice makes.** Everything above is streaming, thinking off, on Flash. Voice sends `fast-chat`, non-streaming, JSON schema, with no `think` argument `[J: server/src/jarvis_live/voice/service.py:160, llm/client.py:80]`.

### Decisions already made

| Decision | Date | Source |
|---|---|---|
| Keep LiteLLM | 2026-10-31 | Your call, review upheld `[Rev]` `[TH]` |
| Drop the Bifrost scratch test | 2026-10-31 | **Your call.** It departs from the brief, which asks to run the top two or three candidates `[TH]` `[Rev]`. |
| Drop voice direct to vLLM | 2026-10-31 | Your call `[TH]` `[Rev]` |
| Move the proxy pod off the server nodes | 2026-10-31 | Done, homelab PR #832 `[H: apps/ai/litellm-proxy.yaml]` |

Because of the second row, this doc contains no measured comparison between gateways. That is a consequence of the decision, not a finding that the candidates were measured and lost.

## What you still have to decide

None of these reopens the gateway choice. Each owner is the task in `Tasks.herd`.

| # | Decision | Recommendation | Owner |
|---|---|---|---|
| 1 | Approve the normal-load run, with the voice-shaped arm | Yes. It is the deciding number `[T11]`, and it restarts nothing. | Task 11 step 1 |
| 2 | Accept the [normal-load gate](#normal-load-gate) thresholds | Accept them, or set your own before the run, not after. | You |
| 3 | Accept "beyond the confidence interval" as non-overlapping Wilson 95% intervals | Accept. See [Reopen condition](#reopen-condition). | You |
| 4 | Set a total-time target for the voice arm | Decide before the run. The SLO was written for a 1-2 sentence reply `[T11]`, and voice returns structured JSON `[J: server/src/jarvis_live/voice/service.py]`. Until you set one, the arm uses total p95 under 2.5 s. | You |
| 5 | Approve the two paging alerts and the vLLM scrape | Yes. See [The 3 AM answer](#the-3-am-answer-today). | Task 21 |
| 6 | Set `voice_timeout_s` against `fast-chat`'s 40 s path, **before Ollama returns** | Required ordering `[TH]`. Today it is 30 s `[J: server/src/jarvis_live/config.py:212]`. | Task 12 |
| 7 | Approve the CoreDNS rule and the proxy change window | Yes, with Task 11's runs either side `[Rev]`. | Task 22 |
| 8 | Approve Task 11 step 3, which lowers the chunk size | Decide on the normal-load numbers. It costs a Flash restart of 8 to 13 minutes, and the same again to roll back `[T11]`. | Task 11 step 3 |
| 9 | Name the owner of LiteLLM upgrades, and what triggers one | You are the only operator here, so set a cadence. The pin is 1.104.2 and upstream is 1.106.6 `[Rev]`. | You |
| 10 | Reserved path for interactive callers | Only if step 3 misses the SLO or costs agents too much `[T11]`. It stays behind the gateway `[Rev]`. | Task 12 |

## The network fault: separate and unexplained

These tests used no-inference requests, so they cost the Spark nothing. They are **not** Task 11's probe. They were run while testing the brief's hypothesis that a 5 s stall is a specific bug, config or load problem. Task 11 later reproduced the load problem. This section records a second fault, not the cause of the first.

| Test `[M]` | Result |
|---|---|
| `/health/liveliness`, loopback inside the proxy pod | n=1,555 over 13 min. p50 1.5 ms, p99 2.2 ms, max 20 ms. None over 250 ms. |
| `/v1/models`, loopback inside the proxy pod | n=26. p50 7 ms, max 8 ms. |
| Same period, `/health/liveliness` through the ingress from a workstation | Run 1: 10 of 20 over 1 s (3.6 to 11.4 s). Run 2: 10 of 20 over 1 s (max 5.9 s). All of the delay came after TLS completed, so it is not client DNS or the TLS handshake. |
| Control: another service on the same ingress, same window | 0 of 20 over 1 s. Max 0.23 s. Its pod runs on a different node. |
| `/health/liveliness` via ClusterIP from a `jarvis-live` pod on `agent-d`, no ingress | 6 stalls of 4.6 to 11.1 s in a 70 s run. The run hit its time limit, so there is no denominator. |
| Hostname lookup of the vLLM Service from inside the proxy pod | Repeated lookups of 4.9 to 6.2 s. The run hit its time limit, so there is no denominator. |
| UDP DNS to each of 3 CoreDNS replicas, 75 queries, 1.5 s timeout, from the proxy pod on `server-d` | 11 and 9 lost in two runs (15%, 12%). |
| Same, from another pod on `server-d` | 4 lost (5%). |
| Same, from a pod on `agent-d` | 0 lost. |
| Prometheus, TCP retransmits per second over 1 h | `server-b` to `server-e`: 3.9 to 5.0. `server-a`: 1.8. Agent nodes: 0.3 to 1.1. Conntrack at or under 4.5%. No interface drops on server nodes. |

All tests above ran with the pod on `server-d`, before the move. After the move to `agent-d`:

| Test `[M]`, 19:17 UTC, about 70 minutes after the move | Result |
|---|---|
| `/health/liveliness` through the ingress from a workstation, same test as above | 0 of 20 over 1 s. Max 0.44 s. |

10 of 20 against 0 of 20 gives a Fisher p of 0.0004 if the runs are independent `[Rev]`. That is a real difference at one time of day. It does not show the cause `[Rev]`. Neither the control nor the other tests were repeated after the move.

Task 11's runs agree with the timing. Before the move, gaps ended in bursts of held tokens, with an idle engine and DNS failing at the same moment. After it, 0 of 23 gaps ended in a burst, even from the same node `[T11]`. DNS failed in 63 of 151 requests in run A, and one lookup was slow across the idle run after the move `[T11]`.

The move has no established mechanism `[Rev]`:

- **Retransmits fell on nodes the pod was not running on.** Two 15-minute samples, ~17:26 and ~19:26 UTC, `[Rev]`:

| Node | Before | After |
|---|---|---|
| `server-b` | 6.2 | 0.4 |
| `server-c` | 6.0 | **3.8** |
| `server-d` | 5.9 | 0.1 |
| `server-e` | 5.5 | 0.1 |
| `server-a` | 2.4 | 0.1 |
| `agent-d` | 1.4 | 0.0 |

- **A bad node does not explain that pattern, and `server-c` has not recovered** `[Rev]`.
- **The move also restarted the pod,** so placement and restart are confounded `[Rev]`. The evidence supports "something changed at about 18:05 UTC", no more `[Rev]`.
- **It could return.** Task 11 cannot say why that pod on that node caused it `[T11]`.

What the first session's tests still show:

- **LiteLLM's event loop was not blocked in that window.** Every sample was served in 20 ms or less, including across a 17:15 UTC tick of the `/health` traffic described below `[M]`.
- **The delay sat between other pods and the proxy pod.** It affected a route that does no work `[M]`.

What changed once Task 11 reported:

- **The short-prompt lead is explained.** A short request is in flight for less time, so it overlaps fewer steps `[T11]`. The earlier "fewer packets at risk" reading is withdrawn.
- **The direct stall in the brief is explained** by the same engine freeze `[T11]`, which needs no network fault.
- **The steady 5.5 s has two candidates:** a prefill step, or the 5 s DNS timeout, depending on the moment `[T11]`.

The brief's 12-pair run stays thin on its own: 5 of 12 against 1 of 12 gives a Fisher exact p of 0.155 if the arms are treated as independent, with overlapping Wilson 95% intervals.

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

The gateway's request rate is low: 341 POSTs over 6 h, about 57 an hour or 0.016 per second `[M]`. The pod was 12 h old, so a full day was not available, and this is one window, not a baseline. **That rate describes the gateway, not the engine.** Over 14 hours of logs the engine had requests waiting in 55% of 10 s samples, 4 or more running in 63%, and KV cache above 90% in 34% `[T11]`. `pi-durable` averages about 34,000 tokens per prompt and is the main caller `[T11]`.

The shim's buffering was reproduced locally on Python 3.12: an upstream sent 10 chunks over 1.8 s, and the client received them as one read at 2.01 s `[M]`. Direct, the client received 10 reads. The cause is `read(65536)` on a chunked response. It affects only the two shim aliases, and no working traffic uses it today.

### Who depends on what

| Client | API | Alias or route | Source |
|---|---|---|---|
| `jarvis-live` copilot, spoken requests | OpenAI, JSON schema, **not streaming, no `think` argument** | `fast-chat`; spoken requests use `VOICE_MODEL` first, which is empty. Voice is on in lab only, so on live this route is the copilot's | `[J: server/deploy/k8s/base/configmap.yaml, server/src/jarvis_live/llm/client.py, copilot/loop.py:144, voice/service.py:160]` |
| `jarvis-live` final note | OpenAI, the only caller that sets `think` (off by default) | `heavy` | `[J: server/src/jarvis_live/finalize/finalizer.py:316, config.py:95]` |
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

The brief asks to drop candidates that fail a hard requirement, without listing them. These are derived from the inventory.

| # | Requirement | Why |
|---|---|---|
| R1 | OpenAI chat with streaming, structured output, and unmodified `chat_template_kwargs` | Nearly every client. In `jarvis-live` only the finalizer turns thinking off this way. Voice and the copilot pass no `think` argument, so they send no `chat_template_kwargs` and thinking stays on by default `[J: server/src/jarvis_live/llm/client.py:80,94]`. See the `/no_think` item in [Open items](#open-items). |
| R2 | Anthropic `/v1/messages` reaching local vLLM | 8 clients, and `hermes-runtime` hardcodes it. |
| R3 | Server-side aliases, including slash-free names | Clients must not learn checkpoints. opencode cannot use a slash. |
| R4 | Server-side, one-way fallback chains | Your rule: never silently downgrade. Clients do not send fallback lists. |
| R5 | **Soft, not a gate.** `/v1/embeddings` and `/v1/audio/transcriptions` | Open WebUI STT, transcript search. Downgraded because both are dead today: `embed` returned 500 `[M]` `[T11]` and the Whisper role has no endpoint `[M]` `[Rev]`. It changes no outcome, since Kong fails R4 and R7 anyway. |
| R6 | Runs keyless on the LAN | Otherwise this is also an auth migration across every client. |
| R7 | Self-hosted, and R1 to R6 are free | Homelab, no licence budget. |
| R8 | Maintained: an upstream push within 90 days | You would run this at the centre of everything. |
| R9 | **Conditional.** A `priority` field sent by a client reaches vLLM through the gateway | Binds only if priority scheduling is ever used. `drop_params: true` is set on the proxy `[H: apps/ai/litellm-proxy.yaml]` and may strip the field `[T11]`. |

**Downgraded from the brief's list.** None of these should decide a swap:

- `auto` router. It is opt-in, and today has two effective rungs, one of which has no backend.
- Decision log. It is off.
- `finish_reason` fix. Not reproduced on Flash. Re-test under `coder`.
- `qwen-shim`. Its routes 404.
- Context-window fallbacks. No-ops today.

## Candidates

| Candidate | R1 | R2 | R3 | R4 | R5 (soft) | R6 | R7 | R8 | R9 | Result |
|---|---|---|---|---|---|---|---|---|---|---|
| LiteLLM | ✅ † | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Unproven** | **Kept. The only finalist.** |
| Voice direct to vLLM | ✅ | n/a | n/a | ❌ | n/a | ✅ | ✅ | ✅ | n/a | **Dropped by you**, 2026-10-31 |
| Bifrost | Paper | Paper | Paper | Paper | Paper | Paper | ✅ | ✅ | Not assessed | **Dropped by you**, 2026-10-31 |
| Envoy AI Gateway | Paper | ❌ | Paper | Paper | Paper | Unknown | ✅ | ✅ | Not assessed | **Dropped** |
| Self-hosted Portkey | Paper | Unknown | Unknown | Paper | Paper | Unknown | ✅ | ❌ | Not assessed | **Dropped** |
| Kong AI Gateway | Paper | Unknown | Paper | ❌ | ❌ | Unknown | ❌ | ✅ | Not assessed | **Dropped** |

"Paper" means the docs or source say yes and nothing here tested it.

† Whether `chat_template_kwargs` survives `drop_params` has not been confirmed. Task 12 owns that check `[TH]`.

**R9 for LiteLLM is the one unproven cell that could matter.** LiteLLM's docs say `drop_params` drops unsupported OpenAI parameters, and do not say what happens to a provider-specific field. They also describe `allowed_openai_params` as passing a parameter through as is `[S31]`. So the docs do not settle it. It takes a test, and only if priority is ever reached.

### Dropped

**Bifrost: dropped by you, not by a failed requirement.** On paper it survived the screen, on evidence that is mostly vendor documentation. The brief asks for the top two or three to be measured, and you dropped this test on 2026-10-31. The case for doing so `[Rev]`: an arm that needs direct to meet the SLO while tuned LiteLLM misses it cannot win, because replacing a 6.5 ms hop cannot recover a miss the hop did not cause. The earlier cost estimate is deleted. It priced porting only, not cutover across clients that have not been enumerated `[Rev]`. Licence notes, if the question ever reopens: Apache-2.0 `[S20]`, with clustering and other features listed as Enterprise `[S21]`. The first test would be `/anthropic/v1/messages` reaching vLLM with tool use intact, which vendor guides do not show for vLLM `[S18]`.

**Voice direct to vLLM: dropped by you.** It fails R4. Direct stalled 10 of 10 under load `[T11]`, so it buys nothing against the known cause. It costs the all-calls-via-LiteLLM rule, the fallback under `coder`, central logs and alias indirection `[TH]` `[Rev]`. It is not the reserved path, which is Task 12's question and can stay behind the gateway `[Rev]`. Direct requests remain only as a measurement, in [arm A](#measurement-plan).

**Kong AI Gateway: fails R4 and R7.**

- Fallback lives in AI Proxy Advanced, which Kong's docs say is "only available as part of our AI Gateway Enterprise offering" `[S7]`.
- Secondary sources state that fully self-managed AI Gateway needs a Gateway Enterprise licence `[S10]`.
- Embeddings and transcription routes arrive in Gateway 3.11 `[S8]`, and the newest release in the open repo is 3.9.4 (2026-10-12) `[S9]`. R5 is soft, so this does not count against it.

Evidence: strong for R4 and R7, primary sources.

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

### Reopen condition

**Reopen only if, under normal load, gateway error or stall rate exceeds direct beyond the confidence interval, and that survives tuning the timeout. Otherwise this is a closed question** `[Rev]`.

The condition exists because of one signal that is not about speed. The proxy returned 12 HTTP 500s in 75 minutes after the move, from `Timeout on reading data from socket` and `MidStreamFallbackError`, 10 of them from `pi-durable` `[Rev]`. So LiteLLM may turn a frozen engine into a failed stream where direct would have kept waiting. That is a timeout setting first, and every gateway has one `[Rev]`.

Proposed test, for your approval: the Wilson 95% intervals for gateway and direct do not overlap. At 200 requests per arm, if direct has 0 errors its upper bound is 1.9%, so the gateway would need 8 errors of 200 (4%) to cross it. This is arithmetic from the Wilson formula, not a measurement.

## LiteLLM: what keeping it costs

| | |
|---|---|
| **Cost of keeping it** | Real, and the first version priced it wrongly as "Nothing". See below. |
| **What breaks** | Every config PR interrupts every client. See below. |
| **Licence** | MIT outside `enterprise/` `[S2]`. The Prometheus docs do not mark `/metrics` as enterprise-only `[S3]`, but LiteLLM's enterprise page returned 404 here, so that boundary is **unverified**. |
| **3 AM** | Nothing pages. See [The 3 AM answer](#the-3-am-answer-today). |

Costs, from finding 8 of the review:

- **Each config change kills long streams.** Reloader restarts the pod on any ConfigMap change `[H: apps/ai/litellm-proxy.yaml]`. Grace is 30 s with no `preStop`, and aliases allow 300 s `[Rev]`. Six tuning items would mean up to six interruptions for every client, and six breaks in Task 11's before and after runs `[Rev]`.
- **Restarts are routine on a single replica.** 6 pods in 14 days `[Rev]`.
- **Memory is close to the limit.** It peaked at 865 Mi of 1 Gi in 7 days, and no alert covers it `[Rev]`. An OOM would take out every client. No OOM kill was found `[Rev]`.
- **Nobody owns upgrades.** The pin is 1.104.2 and upstream is 1.106.6 `[Rev]`, on a package with a supply-chain incident on record: `litellm` 1.82.7 and 1.82.8 on PyPI were compromised on 2026-03-24 `[S6]`. No official postmortem was found, so that account is from secondary sources. The manifest comments record 1.82.3 then 1.104.2 here `[H]`, and the image is digest-pinned. Whether 1.106.6 has a Docker image was not checked, and that has caught this repo before `[H]`.

Costs of the pod move itself:

- If the proxy's node fails, the pod waits out the default 300 s node eviction with nothing paging. That is the Kubernetes default, not an observed time `[Rev]`.
- `agent-d` also takes Forgejo CI bursts `[H: docs/forgejo.md]`, and the proxy still requests 100m CPU `[H: apps/ai/litellm-proxy.yaml]` `[Rev]`.
- Failover lands on `agent-a`, `-b` or `-c`, and none of them was measured `[Rev]`.

Tuning, in order. Batch every config change into one window, with Task 11's runs either side, and raise the memory limit first `[Rev]`. None of these is proven to help, and none addresses the engine freeze.

1. **Placement: done.** PR #832 added a scheduling constraint: required amd64, not control-plane, not `andrew-gamingpc`, with `agent-d` preferred but not pinned `[H: apps/ai/litellm-proxy.yaml]`. Its own comment calls it a test of a hypothesis.
2. Raise the memory limit, then the CPU request. The pod requests 100m CPU and has a 1 Gi limit `[H]`. LiteLLM's guide asks for 1 vCPU and 4 Gi per worker, set as both request and limit `[S4]`.
3. Tune the read timeout behind the 500s `[Rev]`. The reopen condition depends on this.
4. Point the facts tick at something other than `/health`, or turn on `background_health_checks` `[S1]`.
5. Add `LITELLM_DETAILED_TIMING=true` on the test run, for per-phase timing headers `[S5]`. Sample `/health/backlog` beside each request `[S5]`.
6. Move readiness to `/health/readiness` `[S4]`.
7. Remove the two 404ing aliases and the shim, after repointing the three LXC agents.

Task 22 owns items 2 and 3, and the CoreDNS rule `[TH]`.

## The 3 AM answer today

### The proxy

- **Who gets paged: nobody.** Only `notify: ntfy` alerts reach a human `[H: apps/observability/kube-prometheus-stack/release.yaml]`. No rule under `apps/observability` covers the proxy `[H]`.
- **Kubernetes will not save you from this failure.** Liveness uses `/health/liveliness` `[H]`, which LiteLLM documents as a process check with no dependencies `[S1]`. It stayed healthy throughout the stalls measured here `[M]`.
- **There is one replica** and no PodDisruptionBudget `[H]`. A second replica needs Redis, per LiteLLM's guide, to share router state `[S4]`.
- **The triage path shares the failure.** Alerts labelled for Jev are triaged by LiteLLM `jev` `[H]`. If the proxy is down, Jev fails closed `[H]`, and only alerts also labelled `notify: ntfy` reach you.
- **You find out by something feeling broken.** Blast radius is every client in the table above.

### The Spark

The Spark is the larger single point of failure `[Rev]`.

- **One active chat model serves every client** `[H: bare-metal/dgx-spark/models.yaml]` `[M]`.
- **Recovery is slow.** A cold load takes 8 to 13 minutes `[T11]`.
- **There is nowhere to fall to.** `heavy`'s fallback has no endpoint under this profile `[M]`, and hosted fallback is excluded on purpose, so a DGX outage fails loudly `[H: apps/ai/litellm-proxy.yaml]`.
- **A freeze looks healthy.** During a prefill freeze `/health/liveliness` answers in milliseconds, and no `vllm:*` series exist in Prometheus `[Rev]` `[T11]`. Today it pages nobody and graphs nothing.

### What to add

A reachability alert would stay green through the actual fault, so the first version's single alert is withdrawn. Replace it with these three `[Rev]`. They reuse the Blackbox exporter and `Probe` CRDs under `apps/observability/home-status/` `[H]`, so they need no new component. Task 21 owns them `[TH]`.

| # | Signal | Action | Catches |
|---|---|---|---|
| 1 | Latency-bound probe of the proxy, from another node | **Page** | Total outage. Reachability alone misses a 10 s answer. |
| 2 | One-token completion through `heavy`, at a low rate | **Page** | Inference failing end to end. It is the only signal that proves inference works. Check it does not become load. |
| 3 | vLLM scrape, Task 11 step 2 | No page. Feeds Task 9 | What the engine was doing. |

## Measurement plan

**Task 11 step 1 owns this run. Nothing here has run, and this rework sent no inference.** It is one normal-load run, not a bake-off. The probe is merged, but it only sends streaming requests with thinking off `[J: scripts/llm_probe.py:88-90]`, so arm V needs adding first.

| Arm | Path | Body |
|---|---|---|
| A | Direct to vLLM. **The measurement floor, not a route for any client.** | The probe's body, and arm V's body so V has a floor |
| B | LiteLLM as deployed | The probe's body, on `qwen3.8-flash-next` |
| C | LiteLLM after the Task 22 window | Same as B |
| V | **Voice-shaped**, through the gateway | `fast-chat`, non-streaming, JSON schema, thinking as sent today, under normal load |

The target is Task 11's SLO, through the gateway, per 200 pairs under normal load: first text p95 under 0.8 s, total p95 under 2.5 s, worst gap p99 under 1 s, errors under 0.5% `[T11]`. Arm V can only be judged on total time and errors, against the target from [decision 4](#what-you-still-have-to-decide).

Rules:

1. **A is a floor.** If A stalls, no gateway can fix it. A already stalled under synthetic load `[T11]`.
2. **Control placement.** Run the client off `server-d` `[T11]`, and record the client's and the proxy's node in every run. Interleaving only protects the comparison if both paths start from the same place.
3. **Interleave** A with B, and A with V, using identical bodies within each pair.
4. **Normal load only, 200 pairs per comparison.** Idle runs already exist `[T11]`. With 200 samples, p99 rests on 2 or 3 requests, so report stall and error rates with confidence intervals. 0 errors in 200 has a Wilson upper bound of 1.9%, so this sample cannot confirm the SLO's 0.5% error target `[T11]` on its own.
5. **Arm V sends what voice sends:** the `VOICE_SYSTEM` prompt, `VoiceRequest` schema, invented request text and no `think` argument, so no `chat_template_kwargs` `[J: server/src/jarvis_live/voice/service.py:37,160, llm/client.py:80,94]`. Non-streaming hides first text, so only total time counts `[Rev]`. Use the 30 s `voice_timeout_s` `[J: server/src/jarvis_live/config.py:212]`, and count a timeout as a failure. Count invalid JSON too, because the client retries once `[J: server/src/jarvis_live/llm/client.py:97]`, so the caller waits for both attempts. Record completion tokens and prompt tokens: a 1,800-token prompt already misses first-text p95 idle `[T11]`.
6. **Arm V uses `fast-chat`, not Flash,** because that is what voice sends: `VOICE_MODEL` is empty, then the copilot model `[J: server/deploy/k8s/base/configmap.yaml:31,141, server/src/jarvis_live/voice/service.py:122]`. A and B stay on `qwen3.8-flash-next`, not a shim alias. Task 11's clearance of the callbacks and pre-call path covers that model only `[Rev]`, so it does not carry over to V.
7. **Keep it apart from other changes.** C runs after the Task 22 window, which must not overlap Task 11 step 3 `[TH]`.

Size: 400 requests for A and B, 400 for A and V, so 800 before arm C. At the 10.8 to 55 s measured under synthetic load `[T11]`, that is 2.4 to 12.2 hours serial. This is arithmetic, not a forecast. The original plan came to 6,400 requests and 10 to 49 hours `[Rev]`.

### Normal-load gate

An idle run closes nothing `[Rev]`. A run counts only if all of these hold. The thresholds are this author's proposal, set below the 14-hour baseline `[T11]` so an ordinary busy period passes.

- `pi-durable` and the other agents were running, not paused, for the whole run.
- `llm_probe.py report --by-load` is attached to the result `[J: scripts/llm_probe.py]`.
- At least 40% of requests fall in the `queued` bucket. Baseline: requests waiting in 55% of samples `[T11]`.
- At most 40% of requests fall in the `alone` bucket. Baseline: 0 or 1 running in 24% of samples `[T11]`.

The baselines are 10 s log samples and the buckets are per request, so they compare only roughly. Whoever reports the run checks the gate, and a failed gate is reported as a failed gate.

## Decision rule

Check the gate first. An idle run at 4 AM fails it, and no row fires. Row 1 can fire with any other row. Rows 2 to 5 are first match wins. All rows are on a run that passed the gate.

| # | Result | Decision |
|---|---|---|
| — | Run fails the [gate](#normal-load-gate) | **No row fires.** Run it again under normal load. |
| 1 | Gateway error or stall rate exceeds direct beyond the confidence interval | Tune the timeout in the Task 22 window and re-run. If it survives, the [reopen condition](#reopen-condition) is met. If it does not, LiteLLM stays. |
| 2 | **A misses the SLO.** The evidence predicts this row `[Rev]` | Not a gateway problem. Keep LiteLLM, then in this order: **(a)** Task 11 step 3, lower `--max-num-batched-tokens` to 1024 and measure agents and voice. **(b)** If that misses the SLO or costs agents too much, the reserved path for interactive callers, Task 12, behind the gateway. **(c)** Only then priority scheduling, and only after R9 is proven. |
| 3 | A meets the SLO, B misses, and row 1 did not fire | The two are within the confidence interval, and a 6.5 ms hop `[T11]` does not explain a miss. Keep LiteLLM. Check again with arm C. |
| 4 | A and B meet the SLO, V misses | The voice route is the problem, not the gateway. Compare V with A's voice body to split route cost from engine cost, then Task 12. Keep LiteLLM. |
| 5 | A, B and V all meet the SLO | Keep LiteLLM. Close this task. |

Priority sits last for three reasons `[T11]` `[Rev]`. It cannot help a request that is already running, since those all share the same steps. `drop_params: true` may strip the field. It is untested with chunked prefill and MTP here. It may still shorten the capacity queue, which averages 157 s `[T11]`. vLLM has `--scheduling-policy priority` `[S30]`, but that source is a forum answer, so verify it. Each change of serving flags costs a cold load `[T11]`.

Step 3's gains are arithmetic from a measured rate, not measurements: about 5 s for a 30-token reply during prefill at 1024, and about 2.5 s at 512 `[T11]`. A smaller chunk costs agents an unknown amount of prefill speed `[T11]`.

## Fix regardless of gateway

In order, from the review `[Rev]`:

1. Task 11 step 1, normal load since the move.
2. The vLLM scrape and the two page-level alerts. See [What to add](#what-to-add).
3. Decide `voice_timeout_s` against `fast-chat`'s 40 s path before Task 12 restores Ollama. With Ollama back and the gaming PC asleep, `fast-chat` costs about 40 s across two 20 s attempts `[H: apps/ai/litellm-proxy.yaml]`, and `voice_timeout_s` is 30 `[J]`, so voice can give up before the fallback fires `[Rev]`.
4. Keep CoreDNS off `andrew-gamingpc`. One of three replicas runs there, and PR #832 banned the proxy from that node because it sleeps `[Rev]` `[H]`. The proxy still resolves through it, with a 5 s client timeout `[Rev]`.
5. Memory limit, CPU request and the 500s' read timeout, in one change window.

Also found during the inventory. None depends on this decision:

6. Kai, Forge and Atlas LXC configs default to a model id that 404s under this profile `[H]` `[M]`. Deployed state not checked.
7. `heavy` has a dead fallback, and `qwen3.8-flash-next` has none. The configs read as safer than they are `[H]` `[M]`.
8. The decision log is off, so Phase 2 of the `auto` plan has no data `[H]`. Also, `fast-chat` has no backend, which logged 22 `ModelNotMappedError` tracebacks in 6 h `[M]`.
9. `docs/open-webui.md` still says no embeddings endpoint exists `[H]`. `embed` now does, though it returned 500 `[M]` `[T11]`.
10. The shim buffers streams, if it is kept at all `[M]`.

## Open items

These are not findings. From the review's "Not verified here" `[Rev]`:

- **Normal load since the move.** Nobody has this number.
- **The cause of the 12 HTTP 500s,** and whether direct would have survived the same requests. One 75-minute window, overlapping Task 11's work.
- **Whether `fast-chat`'s fallback adds measurable time with zero endpoints.** Task 12's 6 to 16 s may be mostly thinking or engine wait.
- **Whether voice with no `chat_template_kwargs` actually reasons on Flash.** The code path is confirmed. Runtime behaviour was not tested.
- **Why retransmits fell across nodes,** and why `server-c` did not. The node-eviction figure is the Kubernetes default, not observed.
- **All `[T11]` figures** come from that document and were not re-run.

Added in this rework:

- **`/no_think`.** `VOICE_SYSTEM` and `COPILOT_SYSTEM` both open with `/no_think`, a prompt convention separate from `chat_template_kwargs` `[J: server/src/jarvis_live/voice/service.py:37, copilot/prompts.py:1,6]`. Neither the review nor Task 11 addresses it. No source here shows whether it suppresses reasoning on Flash. Arm V sends it, so the run measures it as sent. Task 12 reports reasoning of 380 to 490 characters per reply with thinking on `[TH]`.
- **R9.** Whether a `priority` field survives LiteLLM. The docs do not settle it `[S31]`.
- **Whether `chat_template_kwargs` survives `drop_params`.** Task 12 owns this `[TH]`.

Carried from the first version:

- Why loss differs between pods on one node, and whether the cause is the node, its host, or the overlay.
- Whether `agent-d` is a different failure domain from `server-d`. A new node name is not one. Check the host map in the private homelab repo `[Rev]`.
- Whether any `[M]` result holds at other times. The pre-move results come from one window of about 20 minutes, and the post-move result is one ingress run at 19:17 UTC.
- Deployed config of the LXC agents, Open Notebook and Honcho.
- The `auto` default in Open WebUI, which is an admin setting.
- LiteLLM's enterprise boundary for `/metrics`.

## Vendor numbers and evidence quality

Kept because the brief asks for vendor claims to be weighed. None of them reopens the decision.

| Claim | Who says | What it is worth here |
|---|---|---|
| Bifrost adds 11 µs at 5,000 RPS on t3.xlarge, 59 µs on t3.medium | **Vendor number** `[S20]`. The same README also says under 200 µs. | Throughput 300,000 times this traffic. The README's two figures do not agree. |
| Bifrost p99 1.68 s, LiteLLM p99 90.72 s, at 500 RPS | **Vendor number**, Maxim-run `[S27]` | 30,000 times this traffic. It measures saturation, which this proxy never reaches. |
| "54x faster" | **Vendor number**, per the brief | Same. |
| LiteLLM overhead p50 2 ms, p95 8 ms, p99 13 ms at 1,170 RPS | **Vendor number**, mock backend, 4 instances `[S28]` | Supports the brief's point. It is the maker's own figure, and does not measure streaming stalls. |
| Portkey "<1 ms" | **Vendor number**, no method given `[S11]` | Not usable. |
| LiteLLM 28 s against Bifrost 50 ms at 1,000 concurrent users | One developer's test, cited in a roundup `[S29]` | Single setup, no controls, nowhere near this load. |

**No independent like-for-like benchmark was found**, in agreement with the brief. An extended search found only vendor posts and single-author tests `[S29]`. Overhead in microseconds cannot be the deciding factor when the problem measured here is seconds. At 0.016 requests per second, all gateways on this list are far from saturation. The saturation is in the engine `[T11]`, which no gateway changes. The Bifrost rows stay so that nobody re-derives a case from them.

## What was run on the cluster

This section covers the first session. The rework ran nothing on the cluster and sent no inference. It read this repo, `homelab-gitops` through a read-only connector, and two public documentation pages.

Read-only, 2026-10-31, about 17:03 to 17:20 UTC, plus one ingress run at 19:17 UTC. No configuration was changed from this session and nothing was deployed. The only change in the period is homelab PR #832, which moved the proxy pod through Flux at 18:04 UTC.

- Log reads, `kubectl get`, `top`, and Prometheus queries through a port-forward.
- About 1,600 `/health/liveliness` and `/v1/models` loopback requests inside the proxy pod.
- About 70 requests from a workstation through the ingress.
- 7 chat calls to a 404ing alias. These do no inference, but they put that broken route in cooldown once, for 60 s.
- 4 short tool-call completions direct to vLLM (under 80 tokens each), to test `finish_reason`.
- DNS timing from three pods, plus 40 or fewer ClusterIP requests from `jarvis-live`.

**Overlap with Task 11.** Its `llm-probe` pod started at 17:05:48, during this work, and it was found mid-run. One DNS test ran inside that pod before its purpose was noticed: roughly 150 UDP packets. The 4 completions ran on the shared model in that window too. The load is small, but Task 11 should know before trusting samples from 17:05 to 17:20 UTC. The 19:17 run also overlapped a new `llm-probe` pod that had started about 90 seconds earlier: 20 `/health/liveliness` requests over about 45 seconds, no inference.

## Sources

Accessed 2026-10-31. "Vendor" means written by the product's maker. Keys are kept from the first version, so the gaps are sources removed with the Bifrost finalist.

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
| S20 | [Bifrost repo](https://github.com/maximhq/bifrost) | Primary. Benchmarks are vendor numbers. |
| S21 | [Bifrost pricing](https://www.getmaxim.ai/bifrost/pricing.md) | **Vendor** |
| S27 | [Maxim: top LLM gateways](https://www.getmaxim.ai/articles/top-5-llm-gateways-for-2026-a-comprehensive-comparison/) | **Vendor** |
| S28 | [LiteLLM benchmarks](https://docs.litellm.ai/docs/benchmarks) | **Vendor** |
| S29 | [DEV roundup](https://dev.to/varshithvhegde/top-5-llm-gateways-in-2026-a-deep-dive-comparison-for-production-teams-34d2), [DEV buyer's guide](https://dev.to/pranay_batta/buyers-guide-to-pick-the-best-llm-gateway-in-2026-1epa) | Secondary, single authors. Some affiliated with vendors. |
| S30 | [vLLM forum, priority](https://discuss.vllm.ai/t/priority-in-batch-api/2376), [vLLM scheduler config](https://docs.vllm.ai/en/v0.14.0/api/vllm/config/scheduler/) | Forum answer is AI-generated. Scheduler docs are primary. |
| S31 | [LiteLLM drop params](https://docs.litellm.ai/docs/completion/drop_params) | Vendor docs |
