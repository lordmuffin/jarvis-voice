"""Jarvis outbound call handling via Twilio Programmable Voice.

Flow:
  1. Android → POST /api/v1/calls/initiate  — Jarvis dials out
  2. Twilio  → POST /api/v1/calls/webhook   — returns TwiML connecting Media Stream
  3. Twilio  ↔ WS  /api/v1/calls/stream    — bidirectional μ-law 8kHz audio
     - inbound (caller) → VAD → Whisper STT → LiteLLM → gTTS → mulaw → outbound
  4. Android → GET  /api/v1/calls/transcript/{sid}  — SSE live transcript
  5. Android → POST /api/v1/calls/end/{sid}          — hang up
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import AsyncGenerator

import numpy as np
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from jarvis_voice import transcribe as _transcribe

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_TWILIO_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_FROM  = os.environ.get("TWILIO_FROM_NUMBER", "")
_PUBLIC_URL   = os.environ.get("JARVIS_PUBLIC_URL", "https://kai.apj.dev").rstrip("/")
_LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://192.168.1.93:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "litellm")
_CALL_MODEL   = os.environ.get("CALL_LLM_MODEL", "gpt-4o-mini")

# ── In-memory call state ──────────────────────────────────────────────────────
_calls: dict[str, dict] = {}
_sse_queues: dict[str, asyncio.Queue] = {}

router = APIRouter(prefix="/api/v1/calls", tags=["calls"])

# ── Audio helpers (μ-law ↔ PCM16, no audioop dependency) ─────────────────────

def _ulaw_to_pcm16(data: bytes) -> np.ndarray:
    u = (~np.frombuffer(data, dtype=np.uint8)).astype(np.int32)
    sign = u & 0x80
    exp  = (u >> 4) & 0x07
    mant = u & 0x0F
    linear = ((mant << 3) + 0x84) << exp
    linear -= 0x84
    linear = np.where(sign == 0, linear, -linear)
    return linear.astype(np.int16)


def _pcm16_to_ulaw(pcm: np.ndarray) -> bytes:
    x = pcm.astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    x = np.clip(np.abs(x) + 0x84, 0x84, 0x7FFF)
    exp = np.floor(np.log2(x)).astype(np.int32) - 7
    exp = np.clip(exp, 0, 7)
    mant = (x >> (exp + 3)) & 0x0F
    return ((~(sign | (exp << 4) | mant)) & 0xFF).astype(np.uint8).tobytes()


def _resample(pcm: np.ndarray, from_hz: int, to_hz: int) -> np.ndarray:
    if from_hz == to_hz:
        return pcm
    new_len = int(len(pcm) * to_hz / from_hz)
    return np.interp(
        np.linspace(0, len(pcm) - 1, new_len),
        np.arange(len(pcm)),
        pcm,
    ).astype(np.int16)


def _rms(pcm: np.ndarray) -> float:
    if len(pcm) == 0:
        return 0.0
    return float(math.sqrt(np.mean(pcm.astype(np.float32) ** 2)))

# ── TTS: gTTS → mulaw bytes ───────────────────────────────────────────────────

def _tts_to_ulaw(text: str) -> bytes:
    """Convert text to μ-law 8 kHz audio. Returns raw bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "tts.mp3"
        raw = Path(tmp) / "tts.raw"
        # Try gTTS first (internet), fall back to espeak-ng (offline)
        try:
            from gtts import gTTS  # type: ignore
            gTTS(text=text, lang="en", tld="com").save(str(mp3))
            src = str(mp3)
        except Exception:
            wav = Path(tmp) / "tts.wav"
            subprocess.run(
                ["espeak-ng", "-w", str(wav), text],
                check=True, capture_output=True,
            )
            src = str(wav)
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-ar", "8000", "-ac", "1", "-f", "mulaw", str(raw)],
            check=True, capture_output=True,
        )
        return raw.read_bytes()

# ── LLM call ─────────────────────────────────────────────────────────────────

def _llm_respond(messages: list[dict]) -> str:
    body = json.dumps({
        "model":      _CALL_MODEL,
        "messages":   messages,
        "max_tokens": 150,
    }).encode()
    req = urllib.request.Request(
        f"{_LITELLM_BASE}/chat/completions",
        data=body,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {_LITELLM_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
    return result["choices"][0]["message"]["content"].strip()

# ── Twilio REST helper ────────────────────────────────────────────────────────

def _twilio_call(to: str, webhook_url: str) -> str:
    """Place outbound call via Twilio REST API. Returns call SID."""
    if not (_TWILIO_SID and _TWILIO_TOKEN and _TWILIO_FROM):
        raise RuntimeError("Twilio credentials not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER)")
    payload = f"To={urllib.request.quote(to)}&From={urllib.request.quote(_TWILIO_FROM)}&Url={urllib.request.quote(webhook_url)}".encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{_TWILIO_SID}/Calls.json",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    import base64 as _b64
    creds = _b64.b64encode(f"{_TWILIO_SID}:{_TWILIO_TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())["sid"]


def _twilio_hangup(call_sid: str) -> None:
    if not (_TWILIO_SID and _TWILIO_TOKEN):
        return
    payload = b"Status=completed"
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{_TWILIO_SID}/Calls/{call_sid}.json",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    import base64 as _b64
    creds = _b64.b64encode(f"{_TWILIO_SID}:{_TWILIO_TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as exc:
        logger.warning("hangup failed: %s", exc)

# ── SSE helpers ───────────────────────────────────────────────────────────────

def _push_event(call_sid: str, event: dict) -> None:
    q = _sse_queues.get(call_sid)
    if q:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def _sse_gen(call_sid: str) -> AsyncGenerator[str, None]:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_queues[call_sid] = q
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "ended":
                    break
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        _sse_queues.pop(call_sid, None)

# ── System prompt ─────────────────────────────────────────────────────────────

def _system_prompt(contact: str, context: str) -> str:
    return (
        "You are Jarvis, calling on behalf of Andrew Jackson "
        "(Manager of Enterprise Platform Engineering). "
        f"You are speaking with: {contact}. "
        f"Call objective: {context}. "
        "Keep each response to 1-2 sentences. Be professional and concise. "
        "When the objective is achieved or the caller wants to end the call, "
        "say a polite goodbye and respond with exactly: [CALL_COMPLETE]"
    )

# ── Routes ────────────────────────────────────────────────────────────────────

class InitiatePayload(BaseModel):
    to:      str          # E.164 phone number e.g. "+16125551234"
    contact: str = ""     # Display name of who we're calling
    context: str = ""     # What Jarvis should accomplish on the call


@router.post("/initiate")
def calls_initiate(payload: InitiatePayload) -> dict:
    call_sid = "pending-" + uuid.uuid4().hex[:8]
    webhook = f"{_PUBLIC_URL}/api/v1/calls/webhook"
    try:
        call_sid = _twilio_call(payload.to, webhook)
    except RuntimeError as exc:
        # Twilio not configured — return a stub so the Android UI still works for dev
        logger.warning("Twilio not configured, using stub call: %s", exc)

    _calls[call_sid] = {
        "call_sid": call_sid,
        "to":        payload.to,
        "contact":   payload.contact or payload.to,
        "context":   payload.context or "General check-in",
        "status":    "queued",
        "stream_sid": None,
        "messages":  [],
        "transcript": [],
        "started_at": time.time(),
    }
    return {"call_sid": call_sid, "status": "queued"}


@router.post("/webhook")
async def calls_webhook(request: Request) -> PlainTextResponse:
    """Twilio HTTP callback — return TwiML connecting the call to our stream."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    if call_sid in _calls:
        _calls[call_sid]["status"] = "in-progress"
        _push_event(call_sid, {"type": "status", "status": "in-progress"})

    stream_url = f"wss://{_PUBLIC_URL.removeprefix('https://').removeprefix('http://')}/api/v1/calls/stream"
    twiml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<Response>\n"
        "  <Connect>\n"
        f"    <Stream url=\"{stream_url}\">\n"
        f"      <Parameter name=\"call_sid\" value=\"{call_sid}\" />\n"
        "    </Stream>\n"
        "  </Connect>\n"
        "</Response>"
    )
    return PlainTextResponse(twiml, media_type="application/xml")


@router.websocket("/stream")
async def calls_stream(ws: WebSocket) -> None:
    """Twilio Media Streams — bidirectional μ-law 8 kHz audio."""
    await ws.accept()
    call_sid: str | None = None
    stream_sid: str | None = None

    # VAD state
    ENERGY_THRESH = 300       # RMS threshold for speech
    SILENCE_FRAMES = 40       # 40 × 20ms = 800ms silence → end of utterance
    audio_buf: list[np.ndarray] = []
    silence_count = 0
    has_speech = False

    async def _process_utterance(buf: list[np.ndarray]) -> None:
        """STT → LLM → TTS → send mulaw back to Twilio."""
        if not call_sid:
            return
        call = _calls.get(call_sid)
        if not call:
            return

        # 1. STT ──────────────────────────────────────────────────────────────
        pcm_8k = np.concatenate(buf)
        pcm_16k = _resample(pcm_8k, 8000, 16000)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        # Write 16kHz PCM WAV
        import struct, wave
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm_16k.tobytes())
        try:
            text, _, _ = await asyncio.get_event_loop().run_in_executor(
                None, _transcribe.transcribe, wav_path
            )
        finally:
            Path(wav_path).unlink(missing_ok=True)

        if not text.strip():
            return

        logger.info("[call %s] caller said: %s", call_sid, text)
        call["transcript"].append({"role": "caller", "text": text})
        _push_event(call_sid, {"type": "transcript", "role": "caller", "text": text})

        # 2. LLM ──────────────────────────────────────────────────────────────
        if not call["messages"]:
            call["messages"].append({"role": "system", "content": _system_prompt(call["contact"], call["context"])})

        call["messages"].append({"role": "user", "content": text})

        try:
            reply = await asyncio.get_event_loop().run_in_executor(None, _llm_respond, list(call["messages"]))
        except Exception as exc:
            logger.error("LLM error: %s", exc)
            reply = "I apologize, I'm having trouble connecting. Let me try again."

        call["messages"].append({"role": "assistant", "content": reply})
        call["transcript"].append({"role": "jarvis", "text": reply})
        _push_event(call_sid, {"type": "transcript", "role": "jarvis", "text": reply})

        # Check for call completion signal
        done = "[CALL_COMPLETE]" in reply
        clean_reply = reply.replace("[CALL_COMPLETE]", "").strip()

        # 3. TTS → mulaw → WebSocket ──────────────────────────────────────────
        if stream_sid and clean_reply:
            try:
                ulaw_bytes = await asyncio.get_event_loop().run_in_executor(None, _tts_to_ulaw, clean_reply)
                # Send in 160-byte chunks (20ms at 8kHz) with small delay to avoid buffer overrun
                _CHUNK = 160
                for i in range(0, len(ulaw_bytes), _CHUNK):
                    chunk = ulaw_bytes[i:i + _CHUNK]
                    if len(chunk) < _CHUNK:
                        chunk += b"\x7F" * (_CHUNK - len(chunk))  # pad with silence
                    msg = json.dumps({
                        "event":     "media",
                        "streamSid": stream_sid,
                        "media":     {"payload": base64.b64encode(chunk).decode()},
                    })
                    await ws.send_text(msg)
                    await asyncio.sleep(0.018)  # ~18ms between 20ms frames
            except Exception as exc:
                logger.error("TTS/send error: %s", exc)

        if done:
            _calls[call_sid]["status"] = "completed"
            _push_event(call_sid, {"type": "ended", "reason": "objective_complete"})
            if call_sid:
                await asyncio.get_event_loop().run_in_executor(None, _twilio_hangup, call_sid)

    try:
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            event = msg.get("event")

            if event == "start":
                stream_sid = msg["start"]["streamSid"]
                call_sid   = msg["start"].get("customParameters", {}).get("call_sid") \
                             or msg["start"].get("callSid", "")
                if call_sid and call_sid in _calls:
                    _calls[call_sid]["stream_sid"] = stream_sid
                    _push_event(call_sid, {"type": "status", "status": "connected"})
                    # Greet the caller
                    call = _calls[call_sid]
                    greeting = f"Hello, this is Jarvis calling on behalf of Andrew Jackson. {call['context']}"
                    call["messages"].append({"role": "system", "content": _system_prompt(call["contact"], call["context"])})
                    call["messages"].append({"role": "assistant", "content": greeting})
                    call["transcript"].append({"role": "jarvis", "text": greeting})
                    _push_event(call_sid, {"type": "transcript", "role": "jarvis", "text": greeting})
                    try:
                        ulaw = await asyncio.get_event_loop().run_in_executor(None, _tts_to_ulaw, greeting)
                        _CHUNK = 160
                        for i in range(0, len(ulaw), _CHUNK):
                            chunk = ulaw[i:i + _CHUNK]
                            if len(chunk) < _CHUNK:
                                chunk += b"\x7F" * (_CHUNK - len(chunk))
                            await ws.send_text(json.dumps({
                                "event":     "media",
                                "streamSid": stream_sid,
                                "media":     {"payload": base64.b64encode(chunk).decode()},
                            }))
                            await asyncio.sleep(0.018)
                    except Exception as exc:
                        logger.error("greeting TTS error: %s", exc)

            elif event == "media":
                track = msg["media"].get("track", "inbound")
                if track != "inbound":
                    continue
                raw_bytes = base64.b64decode(msg["media"]["payload"])
                pcm = _ulaw_to_pcm16(raw_bytes)
                rms = _rms(pcm)

                if rms > ENERGY_THRESH:
                    audio_buf.append(pcm)
                    silence_count = 0
                    has_speech = True
                elif has_speech:
                    audio_buf.append(pcm)
                    silence_count += 1
                    if silence_count >= SILENCE_FRAMES:
                        buf_copy = list(audio_buf)
                        audio_buf.clear()
                        silence_count = 0
                        has_speech = False
                        asyncio.ensure_future(_process_utterance(buf_copy))

            elif event == "stop":
                if call_sid and call_sid in _calls:
                    _calls[call_sid]["status"] = "completed"
                    _push_event(call_sid, {"type": "ended", "reason": "call_ended"})
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("stream error: %s", exc)
    finally:
        if call_sid and _calls.get(call_sid, {}).get("status") not in ("completed",):
            if call_sid in _calls:
                _calls[call_sid]["status"] = "completed"
            _push_event(call_sid, {"type": "ended", "reason": "disconnected"})


@router.get("/transcript/{call_sid}")
async def calls_transcript(call_sid: str) -> StreamingResponse:
    """SSE stream of live transcript events for a call."""
    return StreamingResponse(
        _sse_gen(call_sid),
        media_type="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/end/{call_sid}")
def calls_end(call_sid: str) -> dict:
    call = _calls.get(call_sid)
    if not call:
        return {"error": "not found"}
    _twilio_hangup(call_sid)
    _calls[call_sid]["status"] = "completed"
    _push_event(call_sid, {"type": "ended", "reason": "user_ended"})
    return {"status": "ended"}


@router.get("")
def calls_list() -> dict:
    return {"calls": list(_calls.values())}


@router.get("/{call_sid}")
def calls_get(call_sid: str) -> dict:
    call = _calls.get(call_sid)
    if not call:
        return {"error": "not found"}
    return call
