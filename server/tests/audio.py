"""Synthetic PCM16 16 kHz audio: 440 Hz bursts and silence."""

import numpy as np

SR = 16_000


def tone(seconds: float, freq: float = 440.0, amp: float = 0.5) -> bytes:
    t = np.arange(int(SR * seconds)) / SR
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype("<i2").tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(SR * seconds) * 2)


def dominant_freq(pcm: bytes) -> int:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    spectrum = np.abs(np.fft.rfft(x))
    return int(np.argmax(spectrum) * SR / len(x))


# 1 s silence, 1.5 s tone, 1 s silence, 2 s tone, 1 s silence, 0.2 s tone (too short), 1 s silence
TWO_UTTERANCES = (
    silence(1.0) + tone(1.5) + silence(1.0) + tone(2.0) + silence(1.0) + tone(0.2) + silence(1.0)
)
