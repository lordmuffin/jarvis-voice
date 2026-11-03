"""Score an engine on a pulled eval set (``jarvis-voice eval pull``).

The reference is the server's transcript, so ``wer`` is disagreement with the server, not true
WER: use it to compare engines on the same clips. Per-clip hypotheses are written to
``<eval>/results/<engine>.jsonl``, owner-only, because they are transcripts of your voice."""

import json
import math
import os
import time
import wave
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_desktop.stt.engine import Engine
from jarvis_desktop.stt.wer import edits, normalise


@dataclass
class Report:
    engine: str = ""
    clips: int = 0
    audio_s: float = 0.0
    wer: float = 0.0
    latency_ms_p50: float = 0.0
    latency_ms_p95: float = 0.0
    latency_ms_max: float = 0.0
    rtf: float = 0.0
    per_session: dict[str, float] = field(default_factory=dict)


def score(
    engine: Engine,
    eval_dir: Path,
    *,
    limit: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> Report:
    manifest = eval_dir / "manifest.jsonl"
    if not manifest.exists():
        raise FileNotFoundError(f"no eval set in {eval_dir}: run `jarvis-voice eval pull` first")
    rows = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
    rows = rows[:limit] if limit is not None else rows
    results = eval_dir / "results"
    results.mkdir(exist_ok=True)
    results.chmod(0o700)
    out = results / f"{engine.name}.jsonl"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    latencies: list[float] = []
    audio_s = 0.0
    errors: dict[str, int] = defaultdict(int)
    words: dict[str, int] = defaultdict(int)
    with os.fdopen(fd, "w") as fh:
        for row in rows:
            with wave.open(str(eval_dir / row["audio"]), "rb") as w:
                pcm = w.readframes(w.getnframes())
            start = clock()
            hypothesis = engine.transcribe(pcm)
            latencies.append(clock() - start)
            audio_s += len(pcm) / 32_000
            n = edits(row["reference"], hypothesis)
            errors[row["session"]] += n
            words[row["session"]] += len(normalise(row["reference"]))
            fh.write(json.dumps({"id": row["id"], "hypothesis": hypothesis, "edits": n}) + "\n")
    out.chmod(0o600)
    total_words = sum(words.values())
    ms = sorted(t * 1000 for t in latencies)
    return Report(
        engine=engine.name,
        clips=len(rows),
        audio_s=audio_s,
        wer=sum(errors.values()) / total_words if total_words else 0.0,
        latency_ms_p50=_percentile(ms, 50),
        latency_ms_p95=_percentile(ms, 95),
        latency_ms_max=ms[-1] if ms else 0.0,
        rtf=sum(latencies) / audio_s if audio_s else 0.0,
        per_session={s: errors[s] / words[s] if words[s] else 0.0 for s in words},
    )


def format_report(report: Report) -> str:
    lines = [
        f"engine {report.engine}: {report.clips} clips, {report.audio_s / 60:.1f} min of audio",
        f"  disagreement with the server (WER vs its transcript): {report.wer:.1%}",
        f"  decode latency per clip: p50 {report.latency_ms_p50:.0f} ms, "
        f"p95 {report.latency_ms_p95:.0f} ms, max {report.latency_ms_max:.0f} ms; "
        f"RTF {report.rtf:.3f}",
        "  by session (agreement with the server is lowest where the audio isn't your voice):",
    ]
    for session, wer in sorted(report.per_session.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {session[:8]}  {wer:6.1%}")
    return "\n".join(lines)


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]
