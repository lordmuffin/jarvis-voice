"""F5-TTS voice clone API — OpenAI-compatible /v1/audio/speech endpoint.

Serves Cortana's voice (Jen Taylor / Halo 3) cloned via F5-TTS.
Reference audio: ~/.f5-tts-voices/cortana_reference.wav
"""

import io
import os
import logging
import numpy as np
import soundfile as sf
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

# ── GB10 Blackwell (sm_121) workaround ────────────────────────────────────────
# PyTorch nightly on GB10 fails to JIT-compile complex-tensor kernels via NVRTC
# (nvrtc: error: invalid value for --gpu-architecture). The only affected op in
# the F5-TTS pipeline is torchaudio.spectrogram().abs() which operates on a
# complex STFT result. Patching torch.abs to route complex tensors through
# view_as_real + norm — both backed by pre-compiled cuBLAS/cuDNN — avoids the JIT.
def _patch_complex_abs():
    import torch
    _orig = torch.abs
    def _safe_abs(input, *, out=None):
        if isinstance(input, torch.Tensor) and input.is_complex():
            result = torch.view_as_real(input).norm(dim=-1)
            if out is not None:
                out.copy_(result)
                return out
            return result
        return _orig(input, out=out) if out is not None else _orig(input)
    torch.abs = _safe_abs
    torch.Tensor.abs = lambda self: _safe_abs(self)
    torch.Tensor.abs_ = lambda self: self.copy_(_safe_abs(self))

_patch_complex_abs()
# ──────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("f5-tts-api")
logging.basicConfig(level=logging.INFO)

VOICES_DIR = Path(os.environ.get("F5_VOICES_DIR", str(Path.home() / ".f5-tts-voices")))

# Voice definitions: name -> (wav_path, transcript_path)
VOICE_MAP = {
    "cortana": (
        VOICES_DIR / "cortana_reference.wav",
        VOICES_DIR / "cortana_ref_transcript.txt",
    ),
}
DEFAULT_VOICE = "cortana"

app = FastAPI(title="F5-TTS Voice API")

# Lazy-loaded model — initialised on first request so startup is instant.
_tts_engine = None


def _get_engine():
    global _tts_engine
    if _tts_engine is None:
        import torch
        # GB10 (Blackwell) NVRTC doesn't support JIT kernels in current torch builds.
        # CPU inference on GB10's ARM CPU + 128 GB unified memory is fast enough.
        device = "cuda" if torch.cuda.is_available() and os.environ.get("F5_FORCE_CPU") != "1" else "cpu"
        logger.info(f"Loading F5-TTS model on {device}…")
        from f5_tts.api import F5TTS
        try:
            _tts_engine = F5TTS(device=device)
        except Exception:
            logger.warning("GPU init failed, falling back to CPU")
            _tts_engine = F5TTS(device="cpu")
        logger.info("F5-TTS model ready")
    return _tts_engine


class SpeechRequest(BaseModel):
    model: str = "f5-tts"
    input: str
    voice: str = DEFAULT_VOICE
    response_format: str = "wav"
    speed: float = 1.0


@app.get("/health")
def health():
    return {"status": "ok", "voices": list(VOICE_MAP.keys())}


@app.post("/v1/audio/speech")
def synthesize(req: SpeechRequest):
    voice = req.voice if req.voice in VOICE_MAP else DEFAULT_VOICE
    ref_wav, ref_txt_path = VOICE_MAP[voice]

    if not ref_wav.exists():
        raise HTTPException(status_code=500, detail=f"Reference audio not found: {ref_wav}")

    ref_text = ref_txt_path.read_text().strip() if ref_txt_path.exists() else ""
    text = req.input.strip()
    if not text:
        raise HTTPException(status_code=400, detail="input text is empty")

    logger.info(f"Synthesizing {len(text)} chars with voice={voice}")

    try:
        engine = _get_engine()
        wav, sr, _ = engine.infer(
            ref_file=str(ref_wav),
            ref_text=ref_text,
            gen_text=text,
            seed=-1,
            speed=req.speed,
        )

        # Convert to 16-bit PCM WAV bytes
        buf = io.BytesIO()
        if hasattr(wav, "numpy"):
            wav_np = wav.numpy()
        else:
            wav_np = np.array(wav)
        sf.write(buf, wav_np, sr, format="WAV", subtype="PCM_16")
        buf.seek(0)

        return Response(content=buf.read(), media_type="audio/wav")

    except Exception as e:
        logger.exception("F5-TTS inference failed")
        raise HTTPException(status_code=500, detail=str(e))
