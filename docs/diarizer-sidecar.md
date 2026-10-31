# Diarizer sidecar: API contract

What the jarvis-live server expects of the speaker service it calls (pyannote on the Spark, see
`docs/plans/diarization.md`). The server side is `server/src/jarvis_live/diarize/client.py`
(`HttpDiarizer`); this file is the contract both sides test against. The sidecar's deployment
manifest and image live in np-home-homelab, not here.

The server runs without the sidecar. With `JARVIS_LIVE_DIARIZE_URL` unset nothing here is called;
with it set, a sidecar that is down, slow, or wrong costs labels, never a session.

## Endpoints

### `POST /diarize`: who spoke when, in one finished recording

Called once per channel (`mic`, then `system`) after a session ends, before the note is written.

| | |
|---|---|
| Request | `Content-Type: audio/wav`, `Content-Length` set, body streamed. 16 kHz, mono, PCM16LE, a plain 44-byte WAV header. Up to an hour or more of audio per request (about 115 MB per hour). Not chunked. |
| Success | `200` with JSON (below). |
| Failure | Anything else, or a body that does not parse, counts as "no diarization" for that channel. Return `5xx` for your own failures (`503` while models are loading) rather than a partial or guessed answer. |
| Timeout | The server gives up after `max(60 s, 0.3 × audio length)` and drops the connection. Stop work when the client disconnects. A real-time factor above about 0.3 on long recordings will be cut off. |

```json
{
  "turns": [
    {"start_ms": 0,    "end_ms": 4200, "speaker": "SPEAKER_01"},
    {"start_ms": 4200, "end_ms": 9100, "speaker": "SPEAKER_00"}
  ],
  "embeddings": {"SPEAKER_00": [0.12, -0.03, "..."], "SPEAKER_01": ["..."]},
  "model_id": "wespeaker-voxceleb-resnet34-LM@<version>"
}
```

- `turns`: required, may be empty (no speech). Integers in milliseconds from the start of the
  file, `end_ms >= start_ms`. Turns of different speakers may overlap. Not required to be sorted.
- `speaker`: any string, stable **within this response**. The server renames speakers by first
  appearance (`spk_mic_0`, ...), so the names carry no meaning and need not match across calls.
- `embeddings`: optional. One voiceprint per speaker name that appears in `turns`, keys spelled
  identically. All vectors the same length. Without them the speakers are simply never
  recognised (still labelled "Speaker N").
- `model_id`: **required whenever `embeddings` is sent.** Without it nothing can ever be matched
  to a stored voice.

### `POST /embed`: the voiceprint of one short clip

Called during a live session, once per transcribed segment, one at a time per session, off to the
side of transcription (the server never waits for it).

| | |
|---|---|
| Request | `Content-Type: audio/wav`, 16 kHz mono PCM16LE, a plain WAV header, **1 to 15 seconds** of speech (shorter clips are not sent: they give unreliable voiceprints). |
| Success | `200` with `{"embedding": [floats], "model_id": "..."}`. Non-empty vector, string `model_id`. |
| Failure | Any non-2xx, timeout (10 s), or malformed body: that segment stays unlabelled. |

### `GET /health` (recommended)

Not called by the server today, but the Deployment needs a readiness probe, and it should be
false while the models load. It also lets the server grow a health check later without a contract
change.

## The voiceprint contract

A voiceprint is only useful if two prints of the same voice come out close. So:

1. **One model for both endpoints.** `/diarize`'s per-speaker embeddings and `/embed`'s clip
   embeddings must come from the same model and be directly comparable by **cosine similarity**
   (scale does not matter; the server never normalises).
2. **Make the speaker print like a clip print.** The simplest way: the mean of the `/embed`-style
   embeddings of that speaker's longest clean turns (a handful of clips of 3-15 s, avoiding
   overlapped speech), not a different statistic. The Phase 0 script
   (`scripts/diarize_bench.py`) tests exactly this assumption: a clip scored against its own
   speaker should land high, against another speaker low.
3. **`model_id` identifies the space.** Change it whenever the embedding model, its weights, its
   preprocessing or the vector length changes. The server only compares prints with equal
   `model_id`, and stored voices from an older id simply stop matching until the user names the
   voice again. A stable id for a stable model matters as much as a changed id for a changed one.
4. **Same length always.** A different length is skipped by the server rather than compared.

## Privacy and security

Audio is private and a voiceprint is biometric data.

- Do not log request bodies, audio, or embeddings. Log sizes, durations, timings, speaker counts.
- Keep audio only in a temporary file for the request and delete it (also on error and on client
  disconnect). Keep nothing between requests.
- The server sends no credentials, so the sidecar has no auth of its own. It must be reachable
  only from the jarvis-live pods: ClusterIP only, no Ingress, and a NetworkPolicy that allows the
  jarvis-live namespaces and nothing else.
- The Hugging Face token (read-only) goes in a Secret (OnePasswordItem, like the other
  credentials), never in the image or the repo.
- Model terms: pyannote's community-1 pipeline is gated on Hugging Face (accept the terms with the
  account that owns the token) and licensed CC-BY-4.0, which needs attribution.

## Where it runs

- Namespace `jarvis-ai`, Service `diarizer`, port `8000`, so that
  `JARVIS_LIVE_DIARIZE_URL=http://diarizer.jarvis-ai.svc.cluster.local:8000`. That line is shipped
  commented out in `server/deploy/k8s/base/configmap.yaml`; enabling it is a one-line change once
  the service exists.
- The Spark parks roles per profile, so the sidecar will sometimes not exist. The server treats a
  refused connection exactly like any other failure; nothing needs to be done about it.
- Resources are **unmeasured**. Run `scripts/diarize_bench.py` first (Phase 0) and size the
  requests from its peak memory and real-time factor, with headroom for two sessions ending at
  once. Models are small next to the Spark's 128 GB, so contention with the vLLM roles is the
  likelier limit than memory.
- Torch needs to work on the Spark's aarch64 / Blackwell stack: that is the main build risk and
  worth proving before anything else.
- Concurrency: finalize-time `/diarize` calls are long and few; `/embed` calls are short and
  frequent. One GPU worker with a small queue is enough to start. Do not let `/embed` wait behind
  a long `/diarize`: the server abandons it after 10 s, so either use a separate worker or answer
  `503` quickly when busy.

## Sketch (not normative, unverified)

Written without being able to run pyannote; check each call against the installed
`pyannote.audio` before relying on it. `scripts/diarize_bench.py` has the same calls and is the
place to settle the differences between pyannote 3.x and 4.x.

```python
@app.post("/diarize")
async def diarize(request: Request):
    path = await spool(request)                      # stream the body to a temp file
    try:
        out = await run_in_gpu_worker(pipeline, path)
        annotation = getattr(out, "speaker_diarization", out)
        turns = [{"start_ms": int(s.start * 1000), "end_ms": int(s.end * 1000), "speaker": str(l)}
                 for s, _, l in annotation.itertracks(yield_label=True)]
        prints = {name: mean_of_longest_clip_embeddings(path, annotation, name)
                  for name in {t["speaker"] for t in turns}}
        return {"turns": turns, "embeddings": prints, "model_id": MODEL_ID}
    finally:
        path.unlink(missing_ok=True)

@app.post("/embed")
async def embed(request: Request):
    wav = await request.body()
    return {"embedding": await run_in_gpu_worker(embed_clip, wav), "model_id": MODEL_ID}
```

## Checking a sidecar against this contract

From `server/`, with the sidecar reachable (this uses the server's own client, so it is what
the server will see):

```sh
uv run python - <<'EOF'
import asyncio, httpx
from pathlib import Path
from jarvis_live.diarize.client import HttpDiarizer

async def main():
    d = HttpDiarizer("http://localhost:8000", httpx.AsyncClient(), timeout_s=300)
    r = await d.diarize(Path("meeting.pcm"))        # raw 16 kHz mono PCM16, no header
    print(len(r.turns), "turns;", sorted({t.speaker for t in r.turns}))
    print("embeddings:", {k: len(v) for k, v in r.embeddings.items()}, "model", r.model_id)
    e = await d.embed(Path("clip.pcm").read_bytes())
    print("embed:", len(e.vector), "model", e.model_id, "same model:", e.model_id == r.model_id)

asyncio.run(main())
EOF
```

It should print the same `model_id` for both calls and equal vector lengths. Make a raw PCM file
with `ffmpeg -i in.m4a -ac 1 -ar 16000 -f s16le meeting.pcm`.
