import asyncio
import os
import stat
from pathlib import Path

import pytest

from jarvis_live.client import capture as c
from jarvis_live.client.outbox import Outbox
from jarvis_live.protocol import BYTES_PER_MS, MIN_PAYLOAD

FRAME = 100 * BYTES_PER_MS  # 3200 bytes


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_cutter_emits_exact_frames_and_carries_the_remainder() -> None:
    cut = c.FrameCutter(frame_ms=100)
    assert cut.feed(b"\x01" * 1000) == []
    frames = cut.feed(b"\x02" * (FRAME * 2))  # 1000 + 6400 = two frames and 1000 over
    assert [len(f) for f in frames] == [FRAME, FRAME]
    assert frames[0][:1000] == b"\x01" * 1000
    assert len(cut.flush() or b"") == 1000  # the 1000-byte remainder is above the 20 ms minimum


def test_cutter_flush_pads_a_short_tail_to_the_protocol_minimum() -> None:
    cut = c.FrameCutter(frame_ms=100)
    cut.feed(b"\x07" * 100)
    tail = cut.flush()
    assert tail is not None and len(tail) == MIN_PAYLOAD
    assert tail[:100] == b"\x07" * 100 and tail[100:] == b"\x00" * (MIN_PAYLOAD - 100)
    assert cut.flush() is None


def test_cutter_flush_with_nothing_buffered_is_none() -> None:
    assert c.FrameCutter().flush() is None


def test_commands_for_parec_and_pw_record() -> None:
    assert c.build_command("parec", None) == [
        "parec", "--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=20",
    ]  # fmt: skip
    assert c.build_command("parec", "alsa_input.usb")[-1] == "--device=alsa_input.usb"
    pw = c.build_command("pw-record", "mysource")
    assert pw[0] == "pw-record" and "--target" in pw and pw[-1] == "-"
    assert "--rate" in pw and "16000" in pw


def test_find_tool_prefers_parec_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(tmp_path / "pw-record", "")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert c.find_tool() == "pw-record"
    _script(tmp_path / "parec", "")
    assert c.find_tool() == "parec"


def test_find_tool_explains_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(c.CaptureError, match="pulseaudio-utils|pipewire"):
        c.find_tool()


async def test_default_monitor_source_comes_from_the_default_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(tmp_path / "pactl", 'echo "alsa_output.pci-0000_00_1f.3.analog-stereo"\n')
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/usr/bin:/bin")
    assert await c.default_monitor() == "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"


async def test_default_monitor_without_pactl_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(c.CaptureError, match="pactl"):
        await c.default_monitor()


async def _run_until(cap: c.Capturer, cond) -> None:  # type: ignore[no-untyped-def]
    task = asyncio.create_task(cap.run())
    try:
        async with asyncio.timeout(10):
            while not cond() and not task.done():
                await asyncio.sleep(0.02)
        if task.done():
            task.result()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_capturer_stores_contiguous_100ms_frames_with_sample_clock_timestamps(
    tmp_path: Path,
) -> None:
    # 1 s of distinct bytes, then stay alive like a live source does.
    fake = _script(
        tmp_path / "fake", f"head -c {FRAME * 10} /dev/zero | tr '\\0' '\\5'\nexec sleep 30\n"
    )
    ob = Outbox("s", tmp_path / "ob")
    cap = c.Capturer([str(fake)], "mic", ob)
    await _run_until(cap, lambda: ob.next_seq("mic") >= 10)
    frames = ob.unacked("mic")[:10]
    assert [f.seq for f in frames] == list(range(10))
    assert [f.t_ms for f in frames] == [i * 100 for i in range(10)]
    assert all(len(f.pcm) == FRAME and f.pcm[0] == 5 for f in frames)


async def test_capturer_restarts_the_source_keeping_seq_contiguous_and_jumping_t_ms(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ran"
    fake = _script(
        tmp_path / "fake",
        f"if [ -e {marker} ]; then head -c {FRAME * 3} /dev/zero; exec sleep 30; "
        f"else touch {marker}; head -c {FRAME} /dev/zero; fi\n",
    )
    ticks = iter(range(5, 1000, 5))  # a fake monotonic clock that advances 5 s per call
    ob = Outbox("s", tmp_path / "ob")
    cap = c.Capturer(
        [str(fake)], "mic", ob, clock=lambda: float(next(ticks)),
        restart=c.RestartPolicy(initial_delay=0.01, max_delay=0.05),
    )  # fmt: skip
    await _run_until(cap, lambda: ob.next_seq("mic") >= 4)
    frames = ob.unacked("mic")[:4]
    assert [f.seq for f in frames] == [0, 1, 2, 3]
    assert frames[0].t_ms == 0
    assert frames[1].t_ms >= 5_000, "timeline must jump over the capture gap"
    assert frames[2].t_ms - frames[1].t_ms == 100


async def test_capturer_gives_up_when_the_source_keeps_dying(tmp_path: Path) -> None:
    fake = _script(tmp_path / "fake", "echo 'no such source' >&2\nexit 1\n")
    ob = Outbox("s", tmp_path / "ob")
    cap = c.Capturer(
        [str(fake)],
        "mic",
        ob,
        restart=c.RestartPolicy(initial_delay=0.01, max_delay=0.02, max_rapid=3),
    )
    with pytest.raises(c.CaptureError, match="no such source"):
        await asyncio.wait_for(cap.run(), 10)


async def test_capturer_calls_back_after_each_frame(tmp_path: Path) -> None:
    fake = _script(tmp_path / "fake", f"head -c {FRAME * 2} /dev/zero; exec sleep 30\n")
    ob = Outbox("s", tmp_path / "ob")
    seen: list[int] = []
    cap = c.Capturer([str(fake)], "mic", ob, on_frame=lambda f: seen.append(f.seq))
    await _run_until(cap, lambda: len(seen) >= 2)
    assert seen[:2] == [0, 1]
