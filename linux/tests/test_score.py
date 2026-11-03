"""Scoring an engine on a pulled eval set: agreement with the server's text, and decode latency."""

import json
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis_desktop.stt.score import format_report, score


class ScriptedEngine:
    name = "scripted"

    def __init__(self, answers: dict[int, str]) -> None:
        self.answers = answers  # by number of samples, so each clip gets its own answer

    def transcribe(self, pcm: bytes) -> str:
        return self.answers[len(pcm) // 2]


def ticking_clock(step: float) -> Iterator[float]:
    t = 0.0
    while True:
        yield t
        t += step


def write_eval(tmp_path: Path, clips: list[tuple[str, str, int]]) -> Path:
    """clips: (session, reference, duration_ms)."""
    (tmp_path / "clips").mkdir()
    with (tmp_path / "manifest.jsonl").open("w") as fh:
        for i, (session, ref, ms) in enumerate(clips):
            rel = f"clips/c{i}.wav"
            with wave.open(str(tmp_path / rel), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(ms * 32))
            row = {
                "id": f"c{i}",
                "session": session,
                "audio": rel,
                "reference": ref,
                "duration_ms": ms,
            }
            fh.write(json.dumps(row) + "\n")
    return tmp_path


def test_scores_agreement_and_latency(tmp_path: Path) -> None:
    eval_dir = write_eval(
        tmp_path,
        [("s1", "one two three four", 2_000), ("s1", "five six", 3_000), ("s2", "seven", 4_000)],
    )
    engine = ScriptedEngine({32_000: "one two three four", 48_000: "five seven", 64_000: "seven"})
    clock = ticking_clock(0.25)
    report = score(engine, eval_dir, clock=lambda: next(clock))
    assert report.clips == 3
    assert report.audio_s == pytest.approx(9.0)
    assert report.wer == pytest.approx(1 / 7)
    assert report.per_session == {"s1": pytest.approx(1 / 6), "s2": 0.0}
    assert report.latency_ms_p50 == pytest.approx(250)
    assert report.rtf == pytest.approx(0.75 / 9.0)


def test_writes_hypotheses_privately_next_to_the_eval_set(tmp_path: Path) -> None:
    eval_dir = write_eval(tmp_path, [("s1", "hello", 2_000)])
    clock = ticking_clock(0.1)
    score(ScriptedEngine({32_000: "hullo"}), eval_dir, clock=lambda: next(clock))
    out = eval_dir / "results" / "scripted.jsonl"
    [row] = [json.loads(line) for line in out.read_text().splitlines()]
    assert row["hypothesis"] == "hullo" and row["edits"] == 1 and row["id"] == "c0"
    assert out.stat().st_mode & 0o077 == 0
    assert (eval_dir / "results").stat().st_mode & 0o777 == 0o700


def test_limit_scores_only_the_first_clips(tmp_path: Path) -> None:
    eval_dir = write_eval(tmp_path, [("s1", "a", 2_000), ("s1", "b", 3_000)])
    clock = ticking_clock(0.1)
    report = score(ScriptedEngine({32_000: "a"}), eval_dir, limit=1, clock=lambda: next(clock))
    assert report.clips == 1


def test_an_empty_or_missing_eval_set_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="eval pull"):
        score(ScriptedEngine({}), tmp_path)


def test_the_report_names_what_the_number_means(tmp_path: Path) -> None:
    eval_dir = write_eval(tmp_path, [("s1", "hello", 2_000)])
    clock = ticking_clock(0.1)
    text = format_report(
        score(ScriptedEngine({32_000: "hello"}), eval_dir, clock=lambda: next(clock))
    )
    assert "scripted" in text
    assert "agreement with the server" in text
    assert "p95" in text


def test_a_set_with_exact_text_is_reported_as_wer(tmp_path: Path) -> None:
    eval_dir = write_eval(tmp_path, [("read", "hello", 2_000)])
    rows = [json.loads(line) for line in (eval_dir / "manifest.jsonl").read_text().splitlines()]
    (eval_dir / "manifest.jsonl").write_text(
        "".join(json.dumps({**r, "source": "read"}) + "\n" for r in rows)
    )
    clock = ticking_clock(0.1)
    report = score(ScriptedEngine({32_000: "hello"}), eval_dir, clock=lambda: next(clock))
    assert report.exact
    text = format_report(report)
    assert "WER against the exact text" in text
    assert "server" not in text
