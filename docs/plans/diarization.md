# Plan: speaker diarization and enrolled voices

Status: **approved**. PR 1 (protocol) and PR 2 (finalize-time diarization, fake diarizer) are
written and uncommitted; Phase 0 (Spark benchmark) waits on a Hugging Face token and Spark access.
PRs 3-5 are written locally (not pushed). Scope: `protocol/v1/`, `server/`, `web/`, `macos/` (plus a
minimal parse change in `android/`, see §6). Legacy `src/jarvis_voice/` is not touched.

Goal: a transcript that says who said what, so "what did Andrew commit to" is answerable and the
copilot stops attributing other people's words to the note's owner.

Assumption to confirm: the misattribution you describe is *other people's speech landing on "me"*.
That is what the code does today (§1): everything on the mic channel is `me`, so an in-room meeting
recorded mic-only is 100% "me".

## 1. What exists today

| Piece | Where | Today |
|---|---|---|
| Speaker | `ingest/store.py: SPEAKER`, `ingest/pipeline.py: _emit` | `speaker` is derived from the **channel**: mic → `me`, system → `them`. No audio analysis. |
| Wire | `protocol/v1/schemas/segment.json`, `protocol.py: Segment` | `speaker: "me" \| "them"`, enum, `additionalProperties: false`. |
| DB | `db/models.py: SegmentRow` | `speaker` String(8) with `CHECK speaker IN ('me','them')`. |
| Segments | `ingest/segmenter.py` | webrtcvad cuts ≤15 s segments per channel (700 ms close silence, 500 ms overlap on forced cuts). Whisper gets text only, **no word timestamps** (`stt/tiers.py` reads `["text"]`). |
| Consumers | `copilot/transcript.py` (`Line.speaker`, `"[mm:ss] me\|them: text"`), `copilot/prompts.py` ("me is the note's owner"), `finalize/render.py` (`Me`/`Them`), `workitems/state.py`, `api/sessions.py: SegmentOut`, `cli.py` | All assume the two-value speaker. |
| Clients | web `types.ts`/`reducer.ts`/`jarvis/TranscriptLine.tsx`, macOS `Speaker` enum in `ServerEvent.swift` + `SessionStore.swift` + `CopilotPanelView.swift`, Android `ServerEvent.kt` + `SpeakerLabel.kt` | `me`/`them` hard-coded; macOS `Speaker` is a closed enum so any other value fails decoding. |
| Lifecycle hooks | `IngestHub._end` → `pipeline.finish()` → `on_end` → orchestrator → `Finalizer` | The natural place for an authoritative pass is between drain and finalizer. |
| Re-run | `ingest/retranscribe.py` | Replaces `final` segments with new ones; any speaker data keyed to segment ids would be lost, so speaker *turns* must be stored by time, not by segment. |
| GPU | `deploy/k8s/base/configmap.yaml` | Whisper tiers: `spark` (vllm-whisper, `jarvis-ai` ns; down when the Spark's active profile parks the role), `pve` (RTX 2070), `gamingpc`. Same pattern fits a diarizer. |

Consequences that shape the design:

- With a system channel, mic ≈ "me" is a decent heuristic; the real gaps are (a) several remote
  people all collapsed into `them`, and (b) mic-only/in-room sessions where everyone is `me`.
- Segments are VAD-cut, not speaker-cut, and STT has no word timestamps. A segment can contain a
  speaker change. We assign a segment its majority speaker and flag mixed ones (§4); true splitting
  needs word timestamps (open question 3).

## 2. Diarization options on the Spark

**Honesty note:** I have not run any of these on your Spark. Figures below are published numbers
from other hardware or from the model cards; where I could not find a number I say so. Phase 0
(§8) measures all of it on the real box before anything else is built.

| Option | Streaming? | Speakers | Published cost | Notes |
|---|---|---|---|---|
| **pyannote community-1** (pyannote.audio pipeline) | No: offline, whole-file | Estimates count; no hard cap | Older 3.1 pipeline: ~2.5 % real-time factor on a V100 (≈1.5 min per hour of audio). No community-1 RTF or VRAM figure found. Models are small (segmentation + a ResNet-class embedding), so I expect well under a few GB, **unmeasured**. Self-reported CPU RTF ~0.86 (third party). | CC-BY-4.0, gated on Hugging Face (needs an accepted-terms token). Runs on CPU by default, `.to("cuda")` for GPU. Needs a torch build that works on the Spark's aarch64/Blackwell stack: the main integration risk. |
| **NVIDIA Sortformer (streaming)** (NeMo) | Yes, ~80 ms frames; ~1.04 s algorithmic latency at the low-latency preset | **Hard cap of 4** | Not found. | English-centric training. Accuracy claims conflict between sources (vendor claims vs. pyannote-favouring comparisons), none official. Pulls in NeMo, a heavy dependency. Cap of 4 is a real limit for meetings. |
| **sherpa-onnx** (pyannote-3.0 segmentation + 3D-Speaker/TitaNet/WeSpeaker embeddings + clustering) | Offline | Estimates count | CPU only: RTF 0.11–0.30 on a 57 s clip (hardware unstated), int8 slightly faster. | No torch, tiny deployment, runs inside the existing server pod. Good fallback tier. Older segmentation model than community-1. |
| Streaming pyannote variants (e.g. diart) | Yes | Online clustering | Not verified here | From memory only; not evaluated. Streaming accuracy is below offline, and they would be a second dependency just for the live view. |

**The real constraints are not VRAM.** The Spark has 128 GB unified memory; these models are tiny
next to the vLLM roles. The costs that matter:

1. **Contention and parking.** The Spark parks roles per profile (the whisper role is already
   sometimes down). The diarizer will sometimes be unavailable → it must be optional everywhere.
2. **Latency is only affordable at finalize.** Offline diarization of an N-minute meeting runs after
   `end`; if published RTFs hold, an hour is on the order of minutes at worst, which sits alongside
   the existing STT drain and LLM finalizer.
3. **Streaming full diarization is poor value.** It means a second long-running GPU consumer per live
   session, a speaker cap (Sortformer) or lower accuracy (online clustering), for labels we are going
   to overwrite at finalize anyway.

### Recommendation

**pyannote community-1 as the authoritative pass at finalize, run as a small sidecar service on the
Spark, plus cheap *provisional* labels live from per-segment voice embeddings.**

Why: no speaker cap and it estimates the number of speakers (meetings exceed 4); offline is where
accuracy is best and nothing here needs it sooner; the embedding model gives us the voiceprints that
enrollment needs, in the same space as the clustering, so enrolled voices match consistently; and
the live path costs one short embedding call per VAD segment (we already hold each segment's PCM in
`_process`) rather than a streaming diarizer. Sortformer is rejected mainly for the 4-speaker cap and
the NeMo dependency; revisit only if Phase 0 shows live labels from embeddings are too weak.
sherpa-onnx on CPU is the fallback tier if the Spark is parked (optional, Phase 2).

## 3. Architecture

```
live:     VAD seg ─► STT ─► emit segment (speaker as today, speaker_id=null)
                     └──► embed(seg.pcm) ─► match enrolled voices / session clusters
                              └─► speaker_update rev=1 (provisional)      [best effort]
finalize: end ─► drain ─► DIARIZE (whole recording, per channel) ─► store turns
                     ─► align turns to segments ─► match clusters to enrolled voices
                     ─► speaker_update rev=2 final ─► Finalizer (note uses names)
```

- **Diarizer sidecar** (`jarvis-ai` ns, like `vllm-role-whisper`): `POST /diarize` (WAV in →
  `{turns:[{start_ms,end_ms,cluster}], embeddings:{cluster: [..], model_id}}`) and `POST /embed`
  (WAV → vector). Server config `JARVIS_LIVE_DIARIZE_URL` (empty = feature off, exactly today's
  behaviour). Deployment manifest for the sidecar lives in np-home-homelab, not this repo; this repo
  only gains the URL setting in `base/configmap.yaml`, in the same PR as the code.
- **New package** `server/src/jarvis_live/diarize/`: `client.py` (httpx, timeout, health probe, like
  `TierPool`), `align.py` (turn ↔ segment overlap), `identify.py` (cosine match vs. enrolled
  profiles), `service.py` (the finalize step).
- **Channels are diarized separately.** Mic and system are different acoustic paths; running them
  apart keeps the mic's room speakers from merging with remote ones and keeps echo handling intact.
  Speaker ids are per session and shared across channels once identified (an enrolled voice heard on
  both channels is one speaker).
- **Alignment:** segment → speaker with maximal turn overlap. If a second speaker covers >25 % of the
  segment, mark it `mixed` (UI shows a subtle marker; copilot text prefixes nothing extra). Turns are
  stored by time (`speaker_turns`), so `retranscribe` can re-align without re-running the model.
- **Ordering:** diarization runs after the pipeline drains and *before* the Finalizer, bounded by a
  timeout (proposal: `max(60 s, 0.3 × duration)`). On timeout/error the Finalizer proceeds on the
  undiarized transcript.

## 4. Protocol (`protocol/v1`)

Principle: do not break `speaker: "me"|"them"`. It keeps its meaning (which side of the call: mic vs.
system) so every existing client keeps working; identity is additive.

- **`segment` is unchanged.** Found while implementing: the shipped macOS decoder rejects unknown
  keys *and* unknown message types (`StrictCoding.swift`, `ServerEvent.swift`), so new fields on
  `segment` would make installed Mac apps silently drop every segment (`LiveConnection` skips what
  it cannot decode), and an unsolicited new message type is dropped too. All identity therefore
  travels in a new message, sent only to clients that opt in.
- New server → client message `speaker_update`:
  `{type, speakers:[{id, label: str|null, enrolled: bool, is_owner: bool}], assignments:[{segment_id, speaker_id: str|null, rev: int, final: bool, mixed?: bool}]}`.
  - **Revision rule:** a client applies an assignment only if `rev` is greater than the one it holds
    for that segment. Idempotent and order-independent, so reconnects and replays are safe. This is
    how provisional (live) labels get revised by the authoritative pass.
  - `speakers` entries are upserts keyed by `id`; `label: null` = unknown (rendered "Speaker N").
    Renaming a speaker (enrollment) is a `speakers` upsert with no assignments.
- **Opt-in:** viewers never send `hello`, so the capability is a query parameter on the stream URL,
  `/v1/sessions/{id}/stream?ticket=…&features=speaker_update`. The bus delivers `speaker_update`
  only to subscriptions that asked. (Replaces the earlier `hello.features` idea.)
- REST: `SegmentOut` gains `speaker_id`, `speaker_rev`, `mixed`; session detail gains `speakers[]`.
- Fixtures (all in the protocol PR): `valid/speaker_update.json`,
  `valid/speaker_update__rename.json`, `invalid/speaker_update__negative_rev.json`,
  `invalid/speaker_update__extra_field.json`.

## 5. Data model (migration `0010`)

- `speaker_profiles(id, user_id, label, model_id, embedding float4[], n_samples, created_at, updated_at)`:
  enrolled voices, per user. A small number per user, so cosine match runs in Python; no pgvector.
- `session_speakers(session_id, speaker_id, label, profile_id nullable, is_owner)`.
- `speaker_turns(session_id, channel, start_ms, end_ms, speaker_id)`.
- `segments` + `speaker_id nullable`, `speaker_rev int default 0`, `mixed bool default false`. The
  `speaker IN ('me','them')` CHECK stays.
- **Privacy:** voiceprints are biometric. They stay per user, are deletable from the UI and API, are
  never logged, and never leave the server DB (the repo is public; nothing real is committed). The
  `model_id` is stored; if the embedding model changes, old profiles are marked stale and need
  re-enrollment rather than being matched across incompatible spaces.

## 6. Enrollment

Principle: never silently create a voiceprint.

1. **Label once, in any session:** the transcript shows "Speaker 2". You name it (or mark "this is
   me") → server builds a profile from that speaker's cleanest segments in the session (centroid of
   the longest, non-mixed segments' embeddings) and re-labels the speaker. Stored under your user.
2. **Bootstrap for you specifically:** since you are in nearly every recording, on a session with a
   system channel the mic channel is almost entirely you. Offer "enroll my voice from this session's
   mic channel" as an explicit one-click action.
3. **Reuse:** at finalize (and live, provisionally) each cluster/segment embedding is compared with
   the user's profiles. Match = cosine ≥ threshold *and* margin over the runner-up; otherwise
   unknown. Matches refine the profile slowly (confirmed labels only) to follow mic/room changes.
4. **Corrections:** relabelling a speaker in a finished session updates that session's assignments
   (new rev) and can be fed back to the profile; "not them" demotes the match.
5. `is_owner` is the profile marked "me". The copilot/finalizer render the owner as `me` (prompt
   semantics unchanged) and others by label or "Speaker N". An unenrolled mic-only session no longer
   claims everything for you: unknown voices stay "Speaker N" and only the owner profile (or the
   mic-channel-with-system-present heuristic) yields `me`.

Thresholds are config, tuned in Phase 0 on your recordings.

## 7. Rendering

- **Web** (`web/`): `Speaker` type widens; `TranscriptLine` shows the label, a colour from a small
  stable palette *taken from instrument tokens* (no hard-coded colours; lint-enforced). Unknown
  speakers: muted/dashed treatment and "Speaker N", clearly different from labeled ones. Click a
  name → rename / "this is me". PastSession and live view share the component. If the palette needs
  a token that instrument lacks, propose it upstream rather than building locally.
- **macOS**: `Speaker` closed enum is the hazard. Keep `speaker: Speaker` and `Segment` as is; add `SpeakerUpdate` event and apply the rev rule in
  `SessionStore` (macOS opts in with `features=speaker_update`); `CopilotPanelView` shows the label with the same unknown/labeled distinction using
  `Color.inst.*`/`InstType`.
- **Android**: not in your scope, but `ProtocolFixtureTest` consumes the same fixtures and the
  parser reads fields by name. I will check it ignores unknown keys/types and make only the minimal
  change needed to stay green; no UI work.

## 8. Phases and PRs

Each PR follows `TDD.md`, uses Conventional Commits, runs `make test` first.

0. **Spike (no product code merged).** Stand up pyannote community-1 on the Spark; run it on 2–3 of
   your own recordings (kept local, not committed). Record: peak GPU memory, wall time vs. audio
   length, DER-ish sanity check against your own labelling, embedding separation of your voice vs.
   others, behaviour when the Spark profile parks the role. **Gate:** confirm the recommendation or
   switch to sherpa-onnx/Sortformer before building further. Needs a Hugging Face token with
   community-1 terms accepted (yours to provide).
1. **`feat(protocol)`:** schemas, fixtures, `protocol.py`, server and Swift decoders, Android parse
   check, `test_protocol_fixtures.py` + Swift tests. Also verifies how old clients treat an unknown
   message type (open question 2).
2. **`feat(server)` diarize at finalize:** migration, `diarize/` client + service + align, fake
   diarizer for tests, orchestrator step, `retranscribe` re-align, `DIARIZE_URL` config, degrade-to-
   undiarized paths, copilot/render using labels.
3. **`feat(server)` enrollment:** profiles API, identify, owner handling, rename/relabel endpoints,
   `speaker_update` publish.
4. **`feat(server)` live provisional:** embedding call in `_process`, session-local online clusters,
   rev=1 updates; strictly best effort and bounded concurrency.
5. **`feat(web)` + `feat(macos)`:** rendering, unknown vs. labeled, rename/enroll UI.

### Decisions made in PR 2

- Speaker ids are per channel and numbered by first appearance: `spk_mic_0`, `spk_system_1`.
  `session_speakers.ordinal` numbers them across the session ("Speaker 3"). Merging one enrolled
  voice across channels is PR 3.
- **Transcript labels** (copilot, note, ideas): a named voice always shows its name; an unnamed one
  shows "Speaker N" only where its channel holds more than one voice (with one, `me`/`them` says
  enough); the owner is `me`. So until enrollment exists, a mic-only room with several voices reads
  "Speaker 1, Speaker 2", never "me": no one is credited with words they did not say.
- The pass runs after the last ideas pass and before the finalizer, bounded by
  `max(diarize_timeout_min_s, diarize_timeout_ratio x audio)`; reprocess re-aligns from stored turns.
- `JARVIS_LIVE_DIARIZE_URL` is shipped commented-out in `base/configmap.yaml`: on only when the
  sidecar exists.

### Decisions made in PR 3

- **A voiceprint is the diarizer's per-speaker embedding** (the sidecar returns `embeddings` and a
  `model_id` with the turns), not a centroid of hand-picked clean segments as first sketched.
  Simpler, and the sidecar already averages over the whole speaker. Revisit if Phase 0 shows
  contaminated clusters.
- Naming a speaker (`PUT /v1/sessions/{id}/speakers/{speaker_id}`) enrols it. The same label again
  refines the profile (weighted mean). `is_owner` marks "me" and never un-marks; one owner per user.
  A profile is only compared with prints from the same `model_id`.
- Recognition: cosine >= `speaker_match_threshold` (0.6, a placeholder to tune in Phase 0) and
  ahead of the runner-up by `speaker_match_margin`; one cluster per profile per channel.
- Profiles are never returned with their print; `DELETE /v1/speaker-profiles/{id}` forgets a voice.
  Past sessions keep the name they were given and just stop being linked.
- The DONE-WHEN "my enrolled voice is attributed correctly in a fresh session" is tested here with
  scripted embeddings; with the real model it is still the Phase 0 check on the Spark.

### Decisions made in PR 4

- Live labels are **off to the side**: a segment is saved and published first, then queued (one
  worker, at most `speaker_live_queue` clips, extra ones dropped) for `POST <sidecar>/embed`. A
  slow or dead sidecar means fewer labels, never a later transcript or acks.
- Clips with less than `speaker_live_min_ms` of speech (1 s) are not embedded.
- Matching: an enrolled voice first (same rule as the final pass), else the nearest voice heard
  so far in the channel (`speaker_live_cluster_threshold`, 0.5, a placeholder for Phase 0), else a
  new voice `live_<channel>_<n>`. Voices are written to `session_speakers` too, so a running
  session's transcript already reads "Speaker 2" and the owner "me" for the copilot.
- Writes only where `speaker_rev < 1`, so a provisional label can never overwrite the final pass.
  The final pass replaces the channel's speaker rows, which removes the `live_*` voices; if it
  fails, the provisional labels stay as the best available.
- `finish()` waits `speaker_live_drain_s` (5 s) for queued labels before the stream is handed on.

### Decisions made in PR 5

- `speaker_update`'s speakers gained an optional `ordinal`, so every client says "Speaker 3" the way
  the server's notes do instead of numbering voices itself.
- One label rule everywhere (server notes, web, macOS): the owner is Me; a named voice shows its
  name; an unnamed one is "Speaker N" only where its side holds several voices.
- Unnamed vs named: an unnamed web tag is outlined (the vendored `i-spk--draft` look) and a named
  one filled; on macOS an unnamed tag is italic and muted. Both also read differently in words.
  A dedicated `i-spk--unknown` style belongs in lordmuffin/instrument; reusing the outlined draft
  look avoids a local component until then.
- Naming a voice is web-only for now (click its tag, session owner only). macOS shows labels and
  opts in to `speaker_update`, but has no naming UI: it needs an API call and a prompt that I
  could not compile or try here.

## 9. Failure behaviour (hard requirement: never a failed session)

| Failure | Result |
|---|---|
| `DIARIZE_URL` unset | Today's behaviour, byte for byte. |
| Sidecar down/parked/timeout at finalize | Log + skip; transcript stays `me`/`them`; Finalizer runs; no `failed` status. Optionally one retry later (not a blocker). |
| Sidecar error live | Segment is emitted without `speaker_id`; no `speaker_update`. STT path unaffected (embed call is a separate task, never awaited by the emit). |
| Embedding model changed | Profiles stale → unknown speakers until re-enrolled; never wrong-space matching. |
| Garbage/too-short audio | Segment stays `speaker_id=null`. |

The diarize step is wrapped so no exception propagates into `IngestHub._end`, the orchestrator, or
the Finalizer.

## 10. Tests and "done when"

**In CI (deterministic, no GPU, no voices committed):**
- Protocol fixtures validate in server and Swift suites.
- A **fake diarizer** returning scripted turns for a **synthetic multi-speaker fixture** (TTS-generated
  distinct voices; license-clean; replayed through `ReplayProducer`) → asserts labeled turns persist,
  `speaker_update` revs (provisional rev=1, final rev=2), mixed-segment flag, retranscribe re-align,
  and every failure row in §9 leaves the transcript intact and the session `done`.
- Enrollment plumbing with fake embeddings: label once → fresh session → matched; below-threshold →
  unknown; stale model → unknown.
- Web/Playwright: unknown vs. labeled speakers visually distinct; Swift: rev rule in `SessionStore`.

**On the Spark, opt-in (new pytest marker, like the existing `live`):** real pyannote on the TTS
fixture produces the expected number of speakers/turns.

**Your acceptance check, which CI cannot do:** I cannot commit your voice to a public repo, so the
"my enrolled voice is attributed correctly in a fresh session" criterion is verified by you (or me
with your local recordings) on the Spark: enroll from session A, record/replay session B, confirm
turns are labeled with your name and others are unknown. I will provide a script for it
(`jarvis-live diarize-check <session>`).

## 11. Open questions

1. Hugging Face token and approval to pull community-1 (gated, CC-BY-4.0, attribution required).
2. ~~Do existing clients ignore unknown message types?~~ Answered: macOS does not; gated by `features`.
3. Can the Spark vllm-whisper return word timestamps (`verbose_json`)? If yes, we can split mixed
   segments at speaker changes; if no, majority-speaker + `mixed` flag stays.
4. Sidecar manifest in np-home-homelab: do you want me to draft it there, or hand you the spec?
5. Which Spark profile hosts the diarizer, and is losing it when a profile parks the whisper role
   acceptable (plan assumes yes, via graceful degrade)?

Sources for published numbers: pyannote
[community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1/blob/main/README.md),
[pyannote vs. NeMo comparison](https://www.pyannote.ai/blog/pyannote-vs.-nvidia-nemo-diarization-accuracy-setup-and-production-readiness),
[FluidAudio Sortformer docs](https://docs.fluidinference.com/diarization/sortformer),
[sherpa-onnx diarization models](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html).
