"""ffmpeg turns one audio track of a pushed stream into the wire codec on stdout.

The output is what ``jarvis_live.client.capture.Capturer`` reads from ``parec`` on Linux, so the
same frame cutter, seq and ``t_ms`` rules apply to a stream as to a microphone.
"""


def ffmpeg_command(url: str, *, track: int, ffmpeg: str = "ffmpeg") -> list[str]:
    """Read audio track ``track`` (0-based, video ignored) of ``url`` as PCM16LE 16 kHz mono."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if url.startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", "tcp"]  # the cluster network, no UDP port juggling
    return cmd + [
        "-i",
        url,
        "-map",
        f"0:a:{track}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "s16le",
        # Raw output is otherwise buffered in 32 KiB blocks: a second of 16 kHz audio held back.
        "-flush_packets",
        "1",
        "pipe:1",
    ]
