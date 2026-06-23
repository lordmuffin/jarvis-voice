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

# Default API key — matches DEFAULT_VAULT_KEY in VoiceChatViewModel.kt.
# Override with JARVIS_CAPTURE_KEY env var to rotate the key on both sides.
_DEFAULT_API_KEY = "0WBpWVdLsieaJPpTI7JEjKBZZMd2G-9WWZM2Iiq_wMo"

# Vault root — two levels up from the Voice Notes inbox subfolder.
# Override with VAULT_ROOT env var if the directory layout differs.
VAULT_ROOT = pathlib.Path(
    os.environ.get("VAULT_ROOT", str(pathlib.Path(VAULT_INBOX).parents[1]))
)
_VAULT_MAX_CHARS = 6000

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
    expected = os.environ.get("JARVIS_CAPTURE_KEY", _DEFAULT_API_KEY)
    if x_jarvis_key != expected:
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


@app.post("/api/v1/capture/voice", response_model=None)
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


@app.post("/api/v1/capture/audio", response_model=None)
def capture_audio(
    file: UploadFile = File(...),
    device: str = Form(...),
    timestamp: str | None = Form(None),
    intent_hint: str | None = Form(None),
    confidence: float | None = Form(None),
    _: str = Depends(verify_key),
) -> dict[str, object]:
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


# ── Vault tool endpoints (used by Android Kai voice tool calls) ───────────────

class AppendPayload(BaseModel):
    path: str
    text: str


@app.get("/api/v1/vault/note")
def vault_read_note(path: str, _: str = Depends(verify_key)) -> dict:
    """Read a note from the vault by relative path."""
    target = VAULT_ROOT / path
    if not target.exists():
        name = pathlib.Path(path).name
        matches = list(VAULT_ROOT.rglob(f"*{name}"))
        if matches:
            target = matches[0]
        else:
            raise HTTPException(status_code=404, detail=f"Not found: {path}")
    content = target.read_text(encoding="utf-8", errors="replace")
    if len(content) > _VAULT_MAX_CHARS:
        content = content[:_VAULT_MAX_CHARS] + "\n\n[truncated]"
    return {"path": str(target.relative_to(VAULT_ROOT)), "content": content}


@app.get("/api/v1/vault/search")
def vault_search(
    query: str,
    directory: str = "",
    _: str = Depends(verify_key),
) -> dict:
    """Search vault notes for a keyword. Returns list of relative paths."""
    search_root = VAULT_ROOT / directory if directory else VAULT_ROOT
    try:
        result = subprocess.run(
            ["grep", "-rl", "--include=*.md", query, str(search_root)],
            capture_output=True, text=True, timeout=5,
        )
        matches = result.stdout.strip().splitlines()
        rel = [str(pathlib.Path(m).relative_to(VAULT_ROOT)) for m in matches[:20]]
        return {"query": query, "count": len(matches), "matches": rel}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Search timed out")


@app.get("/api/v1/vault/sprint-state")
def vault_sprint_state(_: str = Depends(verify_key)) -> dict:
    """Return today's daily note, sprint board, and TASKS.md concatenated."""
    import datetime as dt
    today = dt.date.today().strftime("%Y-%m-%d")
    paths = [
        f"20 Areas/Personal/Daily Notes/{today}.md",
        "20 Areas/Personal/Sprints.md",
        "20 Areas/Personal/TASKS.md",
    ]
    parts: list[dict] = []
    for p in paths:
        try:
            content = (VAULT_ROOT / p).read_text(encoding="utf-8", errors="replace")
            parts.append({"path": p, "content": content[:2000]})
        except FileNotFoundError:
            parts.append({"path": p, "content": "[not found]"})
    return {"date": today, "sections": parts}


@app.post("/api/v1/vault/note/append")
def vault_append_note(payload: AppendPayload, _: str = Depends(verify_key)) -> dict:
    """Append text to an existing vault note."""
    target = VAULT_ROOT / payload.path
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {payload.path}")
    with open(target, "a", encoding="utf-8") as f:
        f.write(f"\n{payload.text.rstrip()}\n")
    return {"status": "appended", "path": payload.path}
