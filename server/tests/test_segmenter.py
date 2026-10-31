import numpy as np

from jarvis_live.ingest.segmenter import Segmenter, SegmenterConfig
from tests.audio import TWO_UTTERANCES, silence, tone

FRAME_TOL = 30  # one VAD frame
HANGOVER_TOL = 150  # webrtcvad keeps flagging speech for a few frames after a burst ends


def run(pcm: bytes, cfg: SegmenterConfig | None = None, chunk: int | None = None):  # type: ignore[no-untyped-def]
    seg = Segmenter(cfg)
    out = []
    step = chunk or len(pcm)
    for i in range(0, len(pcm), step):
        out.extend(seg.feed(pcm[i : i + step]))
    out.extend(seg.flush())
    return out


def test_boundaries_of_two_utterances() -> None:
    segs = run(TWO_UTTERANCES)
    assert len(segs) == 2  # the 200 ms burst is dropped
    a, b = segs
    assert abs(a.start_ms - 1000) <= FRAME_TOL
    assert 2500 - FRAME_TOL <= a.end_ms <= 2500 + HANGOVER_TOL
    assert abs(b.start_ms - 3500) <= FRAME_TOL
    assert 5500 - FRAME_TOL <= b.end_ms <= 5500 + HANGOVER_TOL
    # trailing silence is not part of the segment
    assert len(a.pcm) == (a.end_ms - a.start_ms) * 32


def test_short_burst_under_min_speech_is_dropped() -> None:
    assert run(silence(1) + tone(0.3) + silence(1.5)) == []


def test_min_speech_threshold_comes_from_config() -> None:
    cfg = SegmenterConfig(min_speech_ms=100)
    assert len(run(silence(1) + tone(0.3) + silence(1.5), cfg)) == 1


def test_gap_shorter_than_close_threshold_does_not_split() -> None:
    segs = run(tone(1.0) + silence(0.5) + tone(1.0) + silence(1.0))
    assert len(segs) == 1
    assert segs[0].end_ms >= 2500 - FRAME_TOL


def test_gap_longer_than_close_threshold_splits() -> None:
    # (the VAD flags ~100 ms after each burst as speech, so 1.0 s of silence is ~0.9 s of gap)
    assert len(run(tone(1.0) + silence(1.0) + tone(1.0) + silence(1.0))) == 2


def test_close_threshold_comes_from_config() -> None:
    cfg = SegmenterConfig(close_silence_ms=1200)
    assert len(run(tone(1.0) + silence(1.0) + tone(1.0) + silence(1.5), cfg)) == 1


def test_force_close_at_max_with_overlap() -> None:
    segs = run(tone(20.0) + silence(1.0))
    assert len(segs) == 2
    a, b = segs
    assert a.end_ms - a.start_ms == 15_000
    overlap = a.end_ms - b.start_ms
    assert 500 <= overlap <= 500 + FRAME_TOL
    assert b.end_ms > 19_500
    # the carried overlap is literally the same audio
    assert b.pcm[: overlap * 32] == a.pcm[-overlap * 32 :]


def test_overlap_only_remainder_is_not_emitted() -> None:
    # Speech ends exactly where the force-close happens: the leftover would be pure overlap.
    segs = run(tone(15.0) + silence(2.0))
    assert len(segs) == 1


def test_force_close_and_overlap_come_from_config() -> None:
    cfg = SegmenterConfig(max_ms=3000, overlap_ms=300)
    segs = run(tone(7.0) + silence(1.0), cfg)
    assert [s.end_ms - s.start_ms for s in segs[:2]] == [3000, 3000]
    assert 300 <= segs[0].end_ms - segs[1].start_ms <= 330


def test_result_is_independent_of_chunking() -> None:
    whole = run(TWO_UTTERANCES)
    odd = run(TWO_UTTERANCES, chunk=3201 * 2)
    assert [(s.start_ms, s.end_ms, s.pcm) for s in whole] == [
        (s.start_ms, s.end_ms, s.pcm) for s in odd
    ]


def test_flush_closes_open_segment() -> None:
    seg = Segmenter()
    assert seg.feed(silence(0.5) + tone(1.0)) == []
    assert seg.open_start_ms is not None
    (s,) = seg.flush()
    assert s.start_ms < 600 and s.end_ms > 1400
    assert seg.open_start_ms is None


def test_silence_only_yields_nothing_and_tracks_position() -> None:
    seg = Segmenter()
    assert seg.feed(silence(2.0)) == []
    assert seg.open_start_ms is None
    assert seg.position_ms == 1980  # whole 30 ms frames only


def test_pcm_is_int16() -> None:
    (s,) = run(silence(0.5) + tone(1.0) + silence(1.0))
    assert np.frombuffer(s.pcm, dtype="<i2").size * 2 == len(s.pcm)
