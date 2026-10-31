#!/usr/bin/env python3
"""Phase 0 of docs/plans/diarization.md: what pyannote costs on this machine, and where the
thresholds belong.

Run it on the Spark (or any GPU box) against a few of your own recordings:

    HF_TOKEN=hf_... python3 diarize_bench.py meeting1.m4a meeting2.wav \\
        --reference-voice me.wav --warmup --json bench.json

It reports, per recording, wall time and real-time factor, peak GPU memory, how many speakers it
found and for how long each spoke; and, pooled over all recordings, how well voiceprints of the
same speaker agree with each other versus with other speakers, and the thresholds that follow
(speaker_match_threshold, speaker_match_margin, speaker_live_cluster_threshold in the server's
settings). With --reference-voice (a clip of just you) it also says which diarized speaker is you.

Audio never leaves the machine and the report holds scores only, never a voiceprint. The token is
read from HF_TOKEN and never printed. The model terms must be accepted on Hugging Face first
(pyannote/speaker-diarization-community-1 and the embedding model).

Everything after the models is plain Python (tested in server/tests/test_diarize_bench.py);
`load_models` and `main` are the only parts that need torch and pyannote.audio, and they were
written without being able to run them: if a pyannote call differs in your installed version,
that is the place to adjust.
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_PIPELINE = "pyannote/speaker-diarization-community-1"
DEFAULT_EMBEDDING = "pyannote/wespeaker-voxceleb-resnet34-LM"


@dataclass(frozen=True)
class Turn:
    start: float  # seconds
    end: float
    speaker: str


# --- analysis (no torch) ------------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    nx, ny = float(np.linalg.norm(x)), float(np.linalg.norm(y))
    if nx == 0 or ny == 0:
        return 0.0
    return float(np.dot(x, y) / (nx * ny))


def percentile(values: Sequence[float], p: float) -> float | None:
    return float(np.percentile(values, p)) if len(values) else None


def speaker_seconds(turns: Sequence[Turn]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in turns:
        out[t.speaker] = out.get(t.speaker, 0.0) + (t.end - t.start)
    return out


def _mean(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    return np.mean(np.asarray(vectors, dtype=float), axis=0)


def score_split(clips: dict[str, list[list[float]]]) -> tuple[list[float], list[float]]:
    """Cosine scores of short-clip voiceprints: ``same`` (a clip against the mean of the *other*
    clips of its own speaker, so it never matches itself) and ``different`` (a clip against the
    mean of each other speaker). These are the two populations a threshold has to separate."""
    same: list[float] = []
    different: list[float] = []
    for name, own in clips.items():
        for i, clip in enumerate(own):
            rest = own[:i] + own[i + 1 :]
            if rest:
                same.append(cosine(clip, _mean(rest)))
            for other, theirs in clips.items():
                if other != name and theirs:
                    different.append(cosine(clip, _mean(theirs)))
    return same, different


def suggest(same: Sequence[float], different: Sequence[float]) -> dict[str, Any]:
    """Thresholds from the two score populations. Heuristics, not truth: the point is to see how
    far apart the populations are on *your* recordings and room."""
    if len(same) < 3 or len(different) < 3:
        return {
            "separable": False,
            "match_threshold": None,
            "match_margin": None,
            "cluster_threshold": None,
            "false_accept": None,
            "false_reject": None,
            "n_same": len(same),
            "n_different": len(different),
            "note": "Not enough clips to say: need at least 3 same-speaker and 3 different-speaker "
            "scores. Use longer recordings with more than one speaker.",
        }
    s_lo = percentile(same, 5)
    d_hi = percentile(different, 95)
    assert s_lo is not None and d_hi is not None
    separable = s_lo > d_hi
    if separable:
        threshold = (s_lo + d_hi) / 2
        margin = round(min(0.1, max(0.02, (s_lo - d_hi) / 4)), 2)
        note = "Voices separate cleanly on these recordings."
    else:
        # Pick the threshold with the fewest mistakes, and say plainly that it is a compromise.
        candidates = sorted({*same, *different})
        threshold = min(
            candidates,
            key=lambda t: _rate(different, lambda x: x >= t) + _rate(same, lambda x: x < t),
        )
        margin = 0.05
        note = (
            "The score populations overlap: some different speakers look more alike than some "
            "same-speaker clips. Expect wrong or missed names; prefer a higher threshold (unknown "
            "beats wrong) and longer clips."
        )
    s10, d90 = percentile(same, 10), percentile(different, 90)
    assert s10 is not None and d90 is not None
    return {
        "separable": separable,
        "match_threshold": round(threshold, 3),
        "match_margin": margin,
        "cluster_threshold": round(min(threshold, (s10 + d90) / 2), 3),
        "false_accept": _rate(different, lambda x: x >= threshold),
        "false_reject": _rate(same, lambda x: x < threshold),
        "n_same": len(same),
        "n_different": len(different),
        "note": note,
    }


def _rate(values: Sequence[float], pred: Callable[[float], bool]) -> float:
    return sum(1 for v in values if pred(v)) / len(values) if len(values) else 0.0


def _windows(turns: Sequence[Turn], clip_s: float, min_clip_s: float) -> list[tuple[float, float]]:
    """Back-to-back ``clip_s`` windows inside the turns; a leftover shorter than ``min_clip_s``
    is dropped (short speech gives unreliable voiceprints)."""
    out: list[tuple[float, float]] = []
    for t in turns:
        start = t.start
        while t.end - start >= min_clip_s:
            end = min(start + clip_s, t.end)
            out.append((start, end))
            start = end
    return out


def bench_file(
    path: str,
    *,
    audio_s: float,
    diarize: Callable[[str], list[Turn]],
    embed: Callable[[str, float, float], list[float]],
    clock: Callable[[], float] = time.perf_counter,
    peak_memory: Callable[[], dict[str, float] | None] = lambda: None,
    clip_s: float = 5.0,
    min_clip_s: float = 1.0,
    max_clips: int = 40,
    reference: Sequence[float] | None = None,
) -> dict[str, Any]:
    """One recording: time the diarization, then take voiceprints of short clips of each speaker
    (the live path's situation) and score them against each other."""
    began = clock()
    turns = diarize(path)
    wall = clock() - began
    clips: dict[str, list[list[float]]] = {}
    for name in speaker_seconds(turns):
        windows = _windows([t for t in turns if t.speaker == name], clip_s, min_clip_s)
        if len(windows) > max_clips:  # spread over the recording, not just its start
            step = len(windows) / max_clips
            windows = [windows[int(i * step)] for i in range(max_clips)]
        clips[name] = [embed(path, s, e) for s, e in windows]
    same, different = score_split(clips)
    seconds = speaker_seconds(turns)
    result: dict[str, Any] = {
        "file": Path(path).name,
        "audio_s": audio_s,
        "wall_s": wall,
        "rtf": wall / audio_s if audio_s else math.inf,
        "memory": peak_memory(),
        "speakers": {n: {"seconds": seconds[n], "clips": len(clips[n])} for n in seconds},
        "scores": {"same": same, "different": different},
    }
    if reference is not None:
        result["reference"] = {n: cosine(reference, _mean(v)) for n, v in clips.items() if v}
    return result


def format_report(results: Sequence[dict[str, Any]], *, model: dict[str, Any]) -> str:
    lines = [
        "Diarization benchmark",
        f"  pipeline {model.get('pipeline')}  embedding {model.get('embedding')}  "
        f"device {model.get('device')}"
        + (f"  load {model['load_s']:.1f} s" if model.get("load_s") is not None else ""),
        "",
    ]
    same: list[float] = []
    different: list[float] = []
    for r in results:
        mem = r.get("memory")
        mem_text = (
            ", ".join(f"{k} {v:.2f}" for k, v in mem.items()) if mem else "memory not measured"
        )
        lines.append(
            f"{r['file']}: {r['audio_s']:.0f} s audio, {r['wall_s']:.1f} s wall, "
            f"RTF {r['rtf']:.2f}  ({mem_text})"
        )
        for name, info in r["speakers"].items():
            ref = r.get("reference", {}).get(name)
            who = f"   <- reference voice {ref:.2f}" if ref is not None else ""
            lines.append(f"    {name}: {info['seconds']:.0f} s spoken, {info['clips']} clips{who}")
        same += r["scores"]["same"]
        different += r["scores"]["different"]
    lines.append("")
    s = suggest(same, different)
    lines.append(f"Clip scores: {s['n_same']} same-speaker, {s['n_different']} different-speaker")
    for label, vals in (("same speaker", same), ("different speakers", different)):
        if vals:
            lines.append(
                f"  {label}: p5 {percentile(vals, 5):.2f}  median {percentile(vals, 50):.2f}  "
                f"p95 {percentile(vals, 95):.2f}"
            )
    lines.append(s["note"])
    if s["match_threshold"] is not None:
        lines += [
            "",
            "Suggested settings (JARVIS_LIVE_...):",
            f"  speaker_match_threshold         {s['match_threshold']}",
            f"  speaker_match_margin            {s['match_margin']}",
            f"  speaker_live_cluster_threshold  {s['cluster_threshold']}",
            f"  at that threshold: wrong name {s['false_accept']:.1%} of different-speaker clips, "
            f"missed name {s['false_reject']:.1%} of same-speaker clips",
        ]
    return "\n".join(lines)


# --- the pyannote layer (needs torch and pyannote.audio; not exercised by the tests) ------------


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def ensure_wav(src: Path, workdir: Path) -> Path:
    """The recording as 16 kHz mono PCM16 WAV, converting with ffmpeg if it is not already."""
    try:
        with wave.open(str(src), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000):
                return src
    except (wave.Error, EOFError):
        pass
    if shutil.which("ffmpeg") is None:
        sys.exit(f"{src.name} is not 16 kHz mono WAV and ffmpeg is not installed to convert it.")
    dst = workdir / (src.stem + ".16k.wav")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src)]
        + ["-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(dst)],
        check=True,
    )
    return dst


def _from_pretrained(cls: Any, name: str, token: str) -> Any:
    try:
        return cls.from_pretrained(name, token=token)
    except TypeError:  # pyannote.audio 3.x calls it use_auth_token
        return cls.from_pretrained(name, use_auth_token=token)


def load_models(args: argparse.Namespace) -> tuple[Any, Any, Any, dict[str, Any]]:
    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("Set HF_TOKEN to a Hugging Face token that has accepted the model terms.")
    try:
        import torch
        from pyannote.audio import Inference, Model, Pipeline
    except ImportError as e:
        sys.exit(f"Needs torch and pyannote.audio installed: {e}")
    began = time.perf_counter()
    device = torch.device(args.device)
    pipeline = _from_pretrained(Pipeline, args.pipeline, token)
    pipeline.to(device)
    inference = Inference(_from_pretrained(Model, args.embedding, token), window="whole")
    inference.to(device)
    info = {
        "pipeline": args.pipeline,
        "embedding": args.embedding,
        "device": args.device,
        "load_s": time.perf_counter() - began,
    }
    return torch, pipeline, inference, info


def make_callables(
    torch: Any, pipeline: Any, inference: Any
) -> tuple[
    Callable[[str], list[Turn]],
    Callable[[str, float, float], list[float]],
    Callable[[], dict[str, float] | None],
]:
    from pyannote.core import Segment

    cuda = torch.cuda.is_available()

    def diarize(path: str) -> list[Turn]:
        if cuda:
            torch.cuda.reset_peak_memory_stats()
        out = pipeline(path)
        annotation = getattr(out, "speaker_diarization", out)  # 4.x wraps it, 3.x returns it
        return [
            Turn(seg.start, seg.end, str(label))
            for seg, _, label in annotation.itertracks(yield_label=True)
        ]

    def embed(path: str, start: float, end: float) -> list[float]:
        return [float(x) for x in np.asarray(inference.crop(path, Segment(start, end))).ravel()]

    def peak_memory() -> dict[str, float] | None:
        if not cuda:
            return None
        # On unified-memory machines (the Spark) torch's numbers may under-count: also watch
        # nvidia-smi while it runs.
        return {
            "torch_peak_gb": torch.cuda.max_memory_allocated() / 1e9,
            "torch_reserved_gb": torch.cuda.max_memory_reserved() / 1e9,
        }

    return diarize, embed, peak_memory


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0] + " Needs HF_TOKEN (Hugging Face) in the environment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="The token is read from HF_TOKEN and never printed. See the docstring for details.",
    )
    p.add_argument("recordings", nargs="+", type=Path, help="audio files (any format ffmpeg reads)")
    p.add_argument("--pipeline", default=DEFAULT_PIPELINE)
    p.add_argument("--embedding", default=DEFAULT_EMBEDDING)
    p.add_argument("--device", default="cuda", help="torch device (default cuda)")
    p.add_argument("--reference-voice", type=Path, help="a clip of only you: says which speaker is")
    p.add_argument("--clip-seconds", type=float, default=5.0, help="length of the live-style clips")
    p.add_argument("--warmup", action="store_true", help="diarize the first file once, untimed")
    p.add_argument("--json", type=Path, help="also write the results (scores only) as JSON")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    torch, pipeline, inference, model = load_models(args)
    diarize, embed, peak_memory = make_callables(torch, pipeline, inference)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        files = [ensure_wav(f, work) for f in args.recordings]
        reference = None
        if args.reference_voice:
            ref = ensure_wav(args.reference_voice, work)
            reference = embed(str(ref), 0.0, wav_seconds(ref))
        if args.warmup:
            diarize(str(files[0]))
        results = [
            bench_file(
                str(f),
                audio_s=wav_seconds(f),
                diarize=diarize,
                embed=embed,
                peak_memory=peak_memory,
                clip_s=args.clip_seconds,
                reference=reference,
            )
            for f in files
        ]
    print(format_report(results, model=model))
    if args.json:
        args.json.write_text(json.dumps({"model": model, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
