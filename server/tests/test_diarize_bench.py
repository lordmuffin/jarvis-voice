"""The Phase 0 benchmark's analysis (scripts/diarize_bench.py), against invented voices. The
pyannote layer that feeds it only runs on the Spark; this is everything after it."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "diarize_bench.py"
spec = importlib.util.spec_from_file_location("diarize_bench", SCRIPT)
assert spec and spec.loader
bench = importlib.util.module_from_spec(spec)
sys.modules["diarize_bench"] = bench
spec.loader.exec_module(bench)

DIM = 32


def voice(seed: int) -> np.ndarray:
    v = np.random.RandomState(seed).randn(DIM)
    return v / np.linalg.norm(v)


def say(seed: int, noise: float, n: int = 0) -> list[float]:
    """One clip of voice ``seed``: the voice plus some noise."""
    rng = np.random.RandomState(1000 * seed + n)
    return list(voice(seed) + noise * rng.randn(DIM) / np.sqrt(DIM))


def test_cosine_and_percentile() -> None:
    assert bench.cosine([1, 0], [3, 0]) == pytest.approx(1.0)
    assert bench.cosine([0, 0], [1, 0]) == 0.0
    assert bench.percentile([1, 2, 3, 4, 5], 50) == 3
    assert bench.percentile([], 50) is None


def test_speaker_time_is_summed_per_speaker() -> None:
    turns = [bench.Turn(0, 4, "A"), bench.Turn(4, 5, "B"), bench.Turn(6, 8.5, "A")]
    assert bench.speaker_seconds(turns) == {"A": 6.5, "B": 1.0}


def test_same_and_different_scores_do_not_include_a_clip_matching_itself() -> None:
    clips = {"A": [say(1, 0.2, i) for i in range(6)], "B": [say(2, 0.2, i) for i in range(6)]}
    same, different = bench.score_split(clips)
    assert len(same) == 12 and len(different) == 12  # each clip once against each side
    assert min(same) > max(different)  # well separated voices
    assert max(same) < 1.0  # leave-one-out: never a perfect score from itself


def test_a_speaker_with_one_clip_has_no_same_speaker_score() -> None:
    same, different = bench.score_split(
        {"A": [say(1, 0.1)], "B": [say(2, 0.1, i) for i in range(3)]}
    )
    assert len(same) == 3 and len(different) == 4  # only B can be compared with itself


def test_thresholds_sit_between_separable_voices() -> None:
    s = bench.suggest(same=[0.8, 0.85, 0.9, 0.95], different=[0.0, 0.1, 0.2, 0.3])
    assert s["separable"] is True
    assert 0.3 < s["match_threshold"] < 0.8
    assert s["false_accept"] == 0.0 and s["false_reject"] == 0.0
    assert s["cluster_threshold"] <= s["match_threshold"]  # live clustering merges more readily


def test_overlapping_voices_are_reported_as_not_separable() -> None:
    s = bench.suggest(same=[0.4, 0.5, 0.6, 0.7], different=[0.3, 0.45, 0.55, 0.6])
    assert s["separable"] is False
    assert s["false_accept"] > 0 or s["false_reject"] > 0
    assert "overlap" in s["note"].lower()


def test_too_little_data_makes_no_suggestion() -> None:
    s = bench.suggest(same=[], different=[0.1])
    assert s["match_threshold"] is None and "not enough" in s["note"].lower()


class Clock:
    def __init__(self, *times: float) -> None:
        self.times = list(times)

    def __call__(self) -> float:
        return self.times.pop(0)


def test_one_recording_end_to_end_with_invented_voices() -> None:
    truth = [
        bench.Turn(0, 20, "SPEAKER_00"),
        bench.Turn(20, 40, "SPEAKER_01"),
        bench.Turn(40, 60, "SPEAKER_00"),
    ]
    seed = {"SPEAKER_00": 1, "SPEAKER_01": 2}
    n = iter(range(1000))

    def embed(path: str, start: float, end: float) -> list[float]:
        who = next(t.speaker for t in truth if t.start <= start < t.end)
        return say(seed[who], 0.2, next(n))

    r = bench.bench_file(
        "meeting.wav",
        audio_s=60.0,
        diarize=lambda p: truth,
        embed=embed,
        clock=Clock(100.0, 106.0),  # diarization took 6 s for 60 s of audio
        peak_memory=lambda: {"torch_peak_gb": 1.25},
        clip_s=5.0,
        reference=voice(1).tolist(),
    )

    assert r["file"] == "meeting.wav"
    assert r["rtf"] == pytest.approx(0.1) and r["wall_s"] == pytest.approx(6.0)
    assert r["memory"] == {"torch_peak_gb": 1.25}
    assert r["speakers"]["SPEAKER_00"]["seconds"] == 40 and r["speakers"]["SPEAKER_01"]["clips"] > 0
    # my enrolled voice: close to SPEAKER_00, far from SPEAKER_01
    assert r["reference"]["SPEAKER_00"] > 0.8 > 0.3 > r["reference"]["SPEAKER_01"]
    assert min(r["scores"]["same"]) > max(r["scores"]["different"])


def test_the_report_names_the_findings_and_never_prints_a_voiceprint() -> None:
    clips = {"A": [say(1, 0.2, i) for i in range(5)], "B": [say(2, 0.2, i) for i in range(5)]}
    same, different = bench.score_split(clips)
    result = {
        "file": "m.wav",
        "audio_s": 60.0,
        "wall_s": 6.0,
        "rtf": 0.1,
        "memory": {"torch_peak_gb": 1.25},
        "speakers": {"A": {"seconds": 30.0, "clips": 5}, "B": {"seconds": 30.0, "clips": 5}},
        "scores": {"same": same, "different": different},
        "reference": {"A": 0.93, "B": 0.05},
    }
    text = bench.format_report(
        [result], model={"pipeline": "p", "embedding": "e", "device": "cuda"}
    )
    for needle in ("m.wav", "RTF 0.10", "1.25", "speaker_match_threshold", "A", "0.93"):
        assert needle in text
    assert str(clips["A"][0][0])[:8] not in text  # no raw embedding values


def test_help_runs_without_torch_or_pyannote() -> None:
    out = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "HF_TOKEN" in out.stdout
