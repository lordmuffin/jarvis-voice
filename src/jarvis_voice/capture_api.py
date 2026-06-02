"""FastAPI capture endpoints.

Two ways to land a voice note in the vault:

- ``POST /api/v1/capture/voice`` — JSON payload with a pre-transcribed
  ``transcript`` (client-side STT).
- ``POST /api/v1/capture/audio`` — multipart upload of a raw audio file;
  the server runs faster-whisper and writes the transcript.

Both paths share auth, frontmatter, and vault output via ``write_voice_note``.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jarvis_voice import transcribe as transcribe_mod
from jarvis_voice.vault import VAULT_INBOX, voice_note_basename, write_voice_note

log = logging.getLogger(__name__)

app = FastAPI(title="Jarvis Capture API", version="1.0")


def _transcode_to_mp3(src_path: str, dst_path: str) -> bool:
    """Transcode *src_path* to MP3 at *dst_path*. Returns True on success."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_path,
                "-codec:a", "libmp3lame", "-qscale:a", "4", dst_path,
            ],
            check=True,
            capture_output=True,
        )
        return True
    except FileNotFoundError as err:
        log.warning("mp3 transcode failed for %s: ffmpeg not installed (%s)", src_path, err)
        return False
    except subprocess.CalledProcessError as err:
        stderr = (err.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        tail = stderr[-1] if stderr else ""
        log.warning("mp3 transcode failed for %s: %s", src_path, tail)
        return False

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/manifest.json", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(_STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/capture", include_in_schema=False)
def capture_page() -> HTMLResponse:
    page = (_STATIC_DIR / "capture.html").read_text(encoding="utf-8")
    key = os.environ.get("JARVIS_CAPTURE_KEY", "")
    page = page.replace("__JARVIS_KEY__", _html.escape(key, quote=True))
    return HTMLResponse(page)


class VoiceCapture(BaseModel):
    transcript: str
    timestamp: str
    device: str
    duration_seconds: int | None = None
    model: str | None = None
    confidence: float | None = None
    intent_hint: str | None = None


def verify_key(x_jarvis_key: str = Header(...)) -> str:
    expected = os.environ.get("JARVIS_CAPTURE_KEY", "")
    if not expected or x_jarvis_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_jarvis_key


def _parse_timestamp(value: str | None) -> _dt.datetime:
    if not value:
        return _dt.datetime.now()
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _dt.datetime.now()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/capture/voice")
def capture_voice(payload: VoiceCapture, _: str = Depends(verify_key)) -> dict[str, str]:
    ts = _parse_timestamp(payload.timestamp)

    extra = {
        "intent_hint": payload.intent_hint,
        "confidence": payload.confidence,
    }
    extra = {k: v for k, v in extra.items() if v is not None}

    path = write_voice_note(
        transcript=payload.transcript,
        duration_seconds=payload.duration_seconds or 0,
        source=payload.device,
        timestamp=ts,
        model=payload.model,
        extra_frontmatter=extra or None,
    )
    return {
        "id": str(uuid.uuid4()),
        "routed_to": f"00 Inbox/Voice Notes/{os.path.basename(path)}",
        "status": "received",
    }


@app.post("/api/v1/capture/audio")
def capture_audio(
    file: UploadFile = File(...),
    device: str = Form(...),
    timestamp: str | None = Form(None),
    intent_hint: str | None = Form(None),
    confidence: float | None = Form(None),
    _: str = Depends(verify_key),
) -> dict[str, str]:
    ts = _parse_timestamp(timestamp)

    suffix = pathlib.Path(file.filename or "").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        shutil.copyfileobj(file.file, tmp)

    try:
        try:
            transcript, duration, transcribe_seconds = transcribe_mod.transcribe(tmp_path)
        except transcribe_mod.TranscribeError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

        if not transcript:
            raise HTTPException(status_code=422, detail="No speech detected in audio")

        extra = {
            "intent_hint": intent_hint,
            "confidence": confidence,
        }
        extra = {k: v for k, v in extra.items() if v is not None}

        stem = voice_note_basename(ts, device)
        os.makedirs(VAULT_INBOX, exist_ok=True)
        mp3_target = os.path.join(VAULT_INBOX, f"{stem}.mp3")
        audio_file = f"{stem}.mp3" if _transcode_to_mp3(tmp_path, mp3_target) else None

        path = write_voice_note(
            transcript=transcript,
            duration_seconds=duration,
            source=device,
            timestamp=ts,
            model=f"whisper-{transcribe_mod.WHISPER_MODEL}",
            extra_frontmatter=extra or None,
            audio_file=audio_file,
            transcribe_seconds=transcribe_seconds,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

    return {
        "id": str(uuid.uuid4()),
        "routed_to": f"00 Inbox/Voice Notes/{os.path.basename(path)}",
        "status": "received",
        "transcript": transcript,
        "transcribe_seconds": round(float(transcribe_seconds), 1),
    }
