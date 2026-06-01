"""Gate 3: FastAPI capture endpoint. Receives a transcript from the Android
app (or any client) and writes a markdown note via the shared vault module.
"""
from __future__ import annotations

import datetime as _dt
import os
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from jarvis_voice.vault import write_voice_note

app = FastAPI(title="Jarvis Capture API", version="1.0")


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/capture/voice")
def capture_voice(payload: VoiceCapture, _: str = Depends(verify_key)) -> dict[str, str]:
    try:
        ts = _dt.datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
    except ValueError:
        ts = _dt.datetime.now()

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
