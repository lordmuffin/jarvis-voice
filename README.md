# jarvis-voice

Voice-capture API for the Jarvis second brain. One HTTP surface, two endpoints, one vault output. Deploys to `andrew-jarvis-v1`; markdown notes land in `~/Notes/00 Inbox/Voice Notes/` and propagate to every device via Syncthing.

## What this is

A FastAPI service that turns voice into markdown notes in the vault. Two ingest paths:

- **`POST /api/v1/capture/voice`** — JSON payload with a pre-transcribed `transcript` field. Use this when STT runs on the client (Kotlin Android app, future native clients).
- **`POST /api/v1/capture/audio`** — multipart upload of a raw audio file. Use this when STT should run on the server; the API hands the file to faster-whisper and writes the resulting transcript.

Both endpoints share auth (`X-Jarvis-Key`), frontmatter, and vault output — only the `source:` field differs by caller.

## Architecture

```
                       ┌──────────────────────────────────────┐
  Any client ─────────►│ capture_api.py                       │
  (text or audio)      │   • X-Jarvis-Key auth                │
                       │   • /capture/voice  (JSON, text)     │
                       │   • /capture/audio  (multipart)      │
                       │       └─► transcribe.py (Whisper)    │
                       │   • vault.write_voice_note           │
                       └──────────────┬───────────────────────┘
                                      │
                                      ▼
                       ~/Notes/00 Inbox/Voice Notes/*.md
```

Syncthing replicates the vault folder from `andrew-jarvis-v1` to every device, so a note written by the API appears in Obsidian within seconds.

## Repo layout

```
jarvis-voice/
├── README.md                       # this file
├── requirements.txt                # faster-whisper, fastapi, uvicorn, python-multipart
├── src/jarvis_voice/
│   ├── transcribe.py               # faster-whisper wrapper (audio endpoint)
│   ├── vault.py                    # write_voice_note() — markdown emitter
│   └── capture_api.py              # API entry point (uvicorn target)
├── systemd/
│   └── jarvis-capture-api.service
├── scripts/
│   └── deploy.sh                   # rsync + systemd reload on host
└── .env.example                    # documents the env vars; never committed with real values
```

## Host prerequisites (`andrew-jarvis-v1`)

- Python 3.11+
- `ffmpeg` on PATH (Whisper needs it to convert OGG/M4A → 16 kHz mono WAV)
- `~/.whisper-venv/` — the existing faster-whisper venv (rebuilt by `deploy.sh` against `requirements.txt`)
- systemd
- Traefik (optional — only if you want `capture.apj.dev` to reach the API externally)

Audit commands Forge runs before deploying:

```bash
ls -la ~/.whisper-venv/bin/python*       # venv intact?
which ffmpeg                              # ffmpeg present?
systemctl status jarvis-capture-api      # current state
ss -tlnp | grep 8881                      # port 8881 free?
```

## Environment file

One host-side env file. Not committed. `.env.example` documents the shape.

**`/home/lordmuffin/.jarvis-capture.env`**

| var | meaning |
|---|---|
| `JARVIS_CAPTURE_KEY` | 32-byte random key the API checks against the `X-Jarvis-Key` header |
| `VAULT_INBOX` *(optional)* | override output dir (defaults to `~/Notes/00 Inbox/Voice Notes`) |
| `WHISPER_MODEL` *(optional)* | override model name (defaults to `small.en`) |

Generate the capture key on first deploy:

```bash
python3 -c "import secrets; print('JARVIS_CAPTURE_KEY='+secrets.token_urlsafe(32))" \
    > ~/.jarvis-capture.env
chmod 600 ~/.jarvis-capture.env
```

## Deployment

From this repo on the workstation:

```bash
scripts/deploy.sh                          # → andrew-jarvis-v1
# or
scripts/deploy.sh lordmuffin@192.168.1.42  # override host
```

The script:

1. Creates `/opt/jarvis-voice/` on the host (sudo).
2. `rsync`s `src/`, `systemd/`, and `requirements.txt`.
3. `pip install`s the requirements into `~/.whisper-venv/`.
4. Installs `jarvis-capture-api.service` into `/etc/systemd/system/` and runs `systemctl daemon-reload`.
5. Enables and (re)starts the service. First-time:

```bash
ssh andrew-jarvis-v1 'sudo systemctl enable --now jarvis-capture-api'
```

Key fields in the unit file (sanity check):

```
EnvironmentFile=/home/lordmuffin/.jarvis-capture.env
WorkingDirectory=/opt/jarvis-voice/src
ExecStart=/home/lordmuffin/.whisper-venv/bin/python -m uvicorn jarvis_voice.capture_api:app --host 0.0.0.0 --port 8881
```

## Testing the API

All examples assume you've loaded the capture key into the shell:

```bash
source ~/.jarvis-capture.env
```

### Health check

```bash
curl -s http://localhost:8881/health
# → {"status":"ok"}
```

No auth required. Use this to confirm the service is up before exercising the capture endpoints.

### Text capture — `POST /api/v1/capture/voice`

Use when the client has already transcribed the audio. Send JSON.

```bash
curl -s -X POST http://localhost:8881/api/v1/capture/voice \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-Key: $JARVIS_CAPTURE_KEY" \
  -d "{
    \"transcript\": \"Voice endpoint smoke test from $(hostname).\",
    \"timestamp\": \"$(date -Iseconds)\",
    \"device\": \"curl-test\",
    \"duration_seconds\": 5,
    \"model\": \"client-stt\"
  }"
# → {"id":"…","routed_to":"00 Inbox/Voice Notes/YYYY-MM-DD - HH-MM - curl-test.md","status":"received"}
```

Required fields: `transcript`, `timestamp` (ISO 8601), `device`. Optional: `duration_seconds`, `model`, `confidence`, `intent_hint` — the last two are folded into the note's frontmatter when present.

### Audio capture — `POST /api/v1/capture/audio`

Use when you want the server to run Whisper. Multipart upload — the file's extension (`.m4a`, `.ogg`, `.wav`, `.mp3`, …) is preserved through to ffmpeg, so anything ffmpeg understands works.

```bash
curl -s -X POST http://localhost:8881/api/v1/capture/audio \
  -H "X-Jarvis-Key: $JARVIS_CAPTURE_KEY" \
  -F "file=@/path/to/sample.m4a" \
  -F "device=curl-audio-test" \
  -F "timestamp=$(date -Iseconds)"
# → {"id":"…","routed_to":"00 Inbox/Voice Notes/YYYY-MM-DD - HH-MM - curl-audio-test.md","status":"received"}
```

This call is slower than the text path — Whisper runs synchronously on CPU. Expect ~1× realtime for a 30s clip on `small.en`. The note's frontmatter will record `model: whisper-small.en` (or whatever `WHISPER_MODEL` is set to).

If Whisper returns no speech, the endpoint responds `422 No speech detected in audio` and no note is written.

Optional form fields: `intent_hint`, `confidence` — same semantics as the voice endpoint.

### Auth failure

A missing or wrong `X-Jarvis-Key` returns `401`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8881/api/v1/capture/voice \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-Key: wrong-key" \
  -d '{"transcript":"x","timestamp":"2026-01-01T00:00:00Z","device":"x"}'
# → 401
```

### Verify the note landed

```bash
ls -t ~/Notes/00\ Inbox/Voice\ Notes/ | head -3
journalctl -u jarvis-capture-api -n 50
```

## Optional Traefik route

If Traefik is running on the host (`docker ps | grep traefik`), drop a dynamic config so external clients can reach the API at `https://capture.apj.dev`:

```yaml
# /data/data/configurations/capture.yml
http:
  routers:
    jarvis-capture:
      rule: "Host(`capture.apj.dev`)"
      service: jarvis-capture
      entryPoints: [websecure]
      tls: {certResolver: letsencrypt}
  services:
    jarvis-capture:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:8881"
```

## Vault output format

Every note — regardless of endpoint — looks like this:

```markdown
---
created: 2026-05-31 21:14
source: pixel-10-pro             # whatever the caller passed as `device`
tags: [voice-capture, inbox]
duration_seconds: 27
model: whisper-small.en
---

# Voice Note — 2026-05-31 21:14

**Source:** pixel-10-pro  **Duration:** 27s  **Model:** whisper-small.en

---

<transcript>

---
#voice-capture #inbox
```

Filename: `YYYY-MM-DD - HH-MM - <source>.md`. `intent_hint:` and `confidence:` are added to frontmatter when the caller provides them.

## Troubleshooting

| Symptom | First look |
|---|---|
| `faster_whisper` import fails | `~/.whisper-venv/bin/python -c "import faster_whisper"` — if it errors, rebuild: `python3 -m venv ~/.whisper-venv && ~/.whisper-venv/bin/pip install -r /opt/jarvis-voice/requirements.txt` |
| `ffmpeg: not found` in API logs (audio endpoint only) | `sudo apt-get install -y ffmpeg` |
| Capture API returns 401 | `cat ~/.jarvis-capture.env` and compare with the `X-Jarvis-Key` the client sent |
| Audio endpoint returns 422 | Whisper found no speech — check the file is audible and not silent/corrupted |
| File doesn't appear in vault | Check `VAULT_INBOX` override (if set) and dir perms on `~/Notes/00 Inbox/Voice Notes/`; the service runs as `lordmuffin` |
| Port 8881 already bound | `ss -tlnp \| grep 8881` — kill the squatter or change the port in `jarvis-capture-api.service` |

## Roadmap

Phase B2 (Kotlin Android app) and intent routing land in separate repos; this one stays scoped to ingest + STT.
