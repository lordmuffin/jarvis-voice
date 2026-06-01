# jarvis-voice

Voice-first capture pipeline for the Jarvis second brain. Two ingestion surfaces, one vault output. Deploys to `andrew-jarvis-v1`; markdown notes land in `~/Notes/00 Inbox/Voice Notes/` and propagate to every device via Syncthing.

## What this is

- **Gate 2 — Telegram path:** voice messages to the Jarvis Telegram bot → faster-whisper transcription → markdown note in the vault. Reuses the Telegram app already on the phone; proves the pipeline end-to-end without any native-app work.
- **Gate 3 — Native-app path:** `POST /api/v1/capture/voice` accepts a transcript payload from the future Kotlin Android app (or any other client) and writes the same kind of markdown note. STT happens on the client; this endpoint is a thin ingest + auth layer.

Both gates share `transcribe.py` (Whisper wrapper) and `vault.py` (markdown emitter), so frontmatter and filenames are identical — only the `source:` field differs.

## Architecture

```
                         ┌────────────────────────────────┐
  Telegram client ──────►│ telegram_bot.py (Gate 2)       │
                         │   • download voice file        │
                         │   • transcribe.py (Whisper)    │
                         │   • vault.write_voice_note     │
                         └─────────────┬──────────────────┘
                                       │
                                       ▼
                         ~/Notes/00 Inbox/Voice Notes/*.md
                                       ▲
                                       │
                         ┌─────────────┴──────────────────┐
  Android app ─────────►│ capture_api.py (Gate 3)         │
                         │   • X-Jarvis-Key auth          │
                         │   • vault.write_voice_note     │
                         └────────────────────────────────┘
```

Syncthing replicates the vault folder from `andrew-jarvis-v1` to every device, so a note written by either gate appears in Obsidian within seconds.

## Repo layout

```
jarvis-voice/
├── README.md                       # this file
├── requirements.txt                # faster-whisper, fastapi, uvicorn, python-telegram-bot
├── src/jarvis_voice/
│   ├── transcribe.py               # faster-whisper wrapper (shared)
│   ├── vault.py                    # write_voice_note() — shared markdown emitter
│   ├── telegram_bot.py             # Gate 2 entry point
│   └── capture_api.py              # Gate 3 entry point (uvicorn target)
├── systemd/
│   ├── jarvis-voice-pipeline.service
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
systemctl list-units | grep jarvis        # anything already running?
ss -tlnp | grep -E '8880|8881'            # port 8881 free?
```

## Environment files

Two host-side env files. Neither is committed. `.env.example` documents the shape.

**`/home/lordmuffin/.jarvis.env`** — Gate 2

| var | meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | bot token from @BotFather |
| `NOTIFY_CHAT_ID` | chat id for status notifications (e.g. `7024287135`) |

**`/home/lordmuffin/.jarvis-capture.env`** — Gate 3

| var | meaning |
|---|---|
| `JARVIS_CAPTURE_KEY` | 32-byte random key the API checks against the `X-Jarvis-Key` header |

Generate the capture key on first deploy if absent:

```bash
python3 -c "import secrets; print('JARVIS_CAPTURE_KEY='+secrets.token_urlsafe(32))" \
    > ~/.jarvis-capture.env
chmod 600 ~/.jarvis-capture.env
```

Optional overrides (either file): `VAULT_INBOX` to change the output dir, `WHISPER_MODEL` (default `small.en`) to swap models.

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
4. Installs both `.service` files into `/etc/systemd/system/` and runs `systemctl daemon-reload`.
5. Restarts any service that's already enabled. First-time enablement is manual:

```bash
ssh andrew-jarvis-v1 'sudo systemctl enable --now jarvis-voice-pipeline'
ssh andrew-jarvis-v1 'sudo systemctl enable --now jarvis-capture-api'
```

Key fields in the unit files (sanity check):

```
EnvironmentFile=/home/lordmuffin/.jarvis.env           # or .jarvis-capture.env
WorkingDirectory=/opt/jarvis-voice/src
ExecStart=/home/lordmuffin/.whisper-venv/bin/python -m jarvis_voice.telegram_bot
ExecStart=/home/lordmuffin/.whisper-venv/bin/python -m uvicorn jarvis_voice.capture_api:app --host 0.0.0.0 --port 8881
```

## Verification

### Gate 2 — Telegram path

1. Send a 10–30s voice note to the bot from the phone.
2. Within ~30s a new file appears:
   ```bash
   ls -t ~/Notes/00\ Inbox/Voice\ Notes/ | head -3
   ```
3. The bot replies with `✅ Saved → YYYY-MM-DD - HH-MM - telegram-voice.md` and a preview.
4. Logs are clean:
   ```bash
   journalctl -u jarvis-voice-pipeline -n 50
   ```

### Gate 3 — Capture API

```bash
source ~/.jarvis-capture.env

curl -s http://localhost:8881/health
# → {"status":"ok"}

curl -s -X POST http://localhost:8881/api/v1/capture/voice \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-Key: $JARVIS_CAPTURE_KEY" \
  -d "{
    \"transcript\": \"Gate 3 verification test.\",
    \"timestamp\": \"$(date -Iseconds)\",
    \"device\": \"gate3-test\",
    \"duration_seconds\": 5,
    \"model\": \"test\"
  }"
# → {"id":"...","routed_to":"00 Inbox/Voice Notes/...","status":"received"}

ls -t ~/Notes/00\ Inbox/Voice\ Notes/ | head -1
journalctl -u jarvis-capture-api -n 50
```

A missing or wrong `X-Jarvis-Key` must return `401`.

### Optional Traefik route

If Traefik is running on the host (`docker ps | grep traefik`), drop a dynamic config so the Android app can reach the API at `https://capture.apj.dev`:

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

Every note — regardless of gate — looks like this:

```markdown
---
created: 2026-05-31 21:14
source: telegram-voice          # or e.g. "pixel-10-pro" from the Android app
tags: [voice-capture, inbox]
duration_seconds: 27
model: whisper-small.en
---

# Voice Note — 2026-05-31 21:14

**Source:** telegram-voice  **Duration:** 27s  **Model:** whisper-small.en

---

<transcript>

---
#voice-capture #inbox
```

Filename: `YYYY-MM-DD - HH-MM - <source>.md`. The capture API also includes `intent_hint:` and `confidence:` in frontmatter when the caller provides them.

## Troubleshooting

| Symptom | First look |
|---|---|
| `faster_whisper` import fails | `~/.whisper-venv/bin/python -c "import faster_whisper"` — if it errors, rebuild: `python3 -m venv ~/.whisper-venv && ~/.whisper-venv/bin/pip install -r /opt/jarvis-voice/requirements.txt` |
| `ffmpeg: not found` in bot logs | `sudo apt-get install -y ffmpeg` |
| Bot doesn't reply | `journalctl -u jarvis-voice-pipeline -n 100`. Confirm `TELEGRAM_BOT_TOKEN` is set (`systemctl show jarvis-voice-pipeline -p Environment`) |
| Capture API returns 401 | `cat ~/.jarvis-capture.env` and compare with the `X-Jarvis-Key` the client sent |
| File doesn't appear in vault | Check `VAULT_INBOX` override (if set) and dir perms on `~/Notes/00 Inbox/Voice Notes/`; the service runs as `lordmuffin` |
| Port 8881 already bound | `ss -tlnp \| grep 8881` — kill the squatter or change the port in `jarvis-capture-api.service` |

## Roadmap

Phase B2 (Kotlin Android app) and intent routing land in separate repos; this one stays scoped to STT + ingest.
