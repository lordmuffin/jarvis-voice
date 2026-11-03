"""The decode path a pushed stream takes: ffmpeg pulls one audio track and turns it into the wire
codec (PCM16LE 16 kHz mono), cut into frames by the same frame cutter the Linux client uses."""

import subprocess
from pathlib import Path

from jarvis_live.client.capture import FrameCutter
from jarvis_live.protocol import BYTES_PER_MS
from jarvis_live.streams.decode import ffmpeg_command
from tests.audio import TWO_UTTERANCES, dominant_freq
from tests.streams.clips import obs_clip, system_tone

SECONDS = len(TWO_UTTERANCES) / (2 * 16_000)


def test_reads_one_audio_track_as_16k_mono_pcm() -> None:
    cmd = ffmpeg_command("rtsp://reader:secret@mediamtx:8554/obs-desk", track=1)
    assert cmd[cmd.index("-i") + 1] == "rtsp://reader:secret@mediamtx:8554/obs-desk"
    assert cmd[cmd.index("-map") + 1] == "0:a:1"
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    i = cmd.index("-f", cmd.index("-i"))
    assert cmd[i : i + 2] == ["-f", "s16le"]
    assert cmd[cmd.index("-ar") + 1] == "16000" and cmd[cmd.index("-ac") + 1] == "1"
    # PCM is flushed per packet: an output buffer would hold back ~1 s of 16 kHz audio.
    assert cmd[cmd.index("-flush_packets") + 1] == "1"
    assert cmd[-1] == "pipe:1"


def test_a_file_input_takes_no_rtsp_options() -> None:
    assert "-rtsp_transport" not in ffmpeg_command("/clips/x.ts", track=0)


def _decode(clip: Path, track: int) -> bytes:
    out = subprocess.run(  # noqa: S603
        ffmpeg_command(str(clip), track=track), check=True, capture_output=True
    ).stdout
    frames = FrameCutter().feed(out)
    assert frames and all(len(f) == 100 * BYTES_PER_MS for f in frames)
    return b"".join(frames)


def test_each_obs_track_decodes_to_its_own_audio(tmp_path: Path) -> None:
    clip = obs_clip(tmp_path / "obs.ts", TWO_UTTERANCES, system_tone(SECONDS))
    mic = _decode(clip, 0)
    system = _decode(clip, 1)
    for pcm in (mic, system):
        assert abs(len(pcm) / BYTES_PER_MS - SECONDS * 1000) < 150  # AAC priming, nothing more
    # The mic track's tone is 440 Hz, the desktop track's 880 Hz: neither leaks into the other.
    speech = slice(int(1.2 * 32_000), int(2.3 * 32_000))  # inside the first 1.5 s burst
    assert abs(dominant_freq(mic[speech]) - 440) < 10
    assert abs(dominant_freq(system[speech]) - 880) < 10
