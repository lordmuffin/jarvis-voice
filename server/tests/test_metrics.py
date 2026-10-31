import ast
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from prometheus_client.core import Metric

from jarvis_live import metrics
from jarvis_live.app import create_app
from jarvis_live.config import Settings
from jarvis_live.ingest.segmenter import Segmenter, SegmenterConfig

SRC = Path(__file__).parent.parent / "src" / "jarvis_live"
EXPECTED = {
    "jarvis_live_draft_first_seconds",
    "jarvis_live_draft_interval_seconds",
    "jarvis_live_draft_lag_seconds",
    "jarvis_live_segment_seconds",
    "jarvis_live_stt_request_seconds",
    "jarvis_live_stt_served",
    "jarvis_live_stt_fallthrough",
    "jarvis_live_stt_tier_healthy",
    "jarvis_live_stt_current_tier",
    "jarvis_live_authoritative_seconds",
    "jarvis_live_dup_hold_seconds",
    "jarvis_live_lag_ms_max",
    "jarvis_live_copilot_roundtrip_seconds",
    "jarvis_live_copilot_staleness_seconds",
    "jarvis_live_finalize_seconds",
    "jarvis_live_live_sessions",
    "jarvis_live_slo_breach",
}


def _families() -> list[Metric]:
    return list(metrics.REGISTRY.collect())


def test_the_metric_set_is_complete() -> None:
    assert EXPECTED <= {f.name for f in _families()}


def test_no_metric_has_a_label_outside_the_closed_sets() -> None:
    for f in _families():
        for s in f.samples:
            assert set(s.labels) <= metrics.ALLOWED_LABELS | {"le"}, (f.name, s.labels)


def test_stt_request_is_recorded_per_tier_and_outcome() -> None:
    before = metrics.REGISTRY.get_sample_value(
        "jarvis_live_stt_request_seconds_count", {"tier": "t1", "outcome": "ok"}
    )
    metrics.stt_request("t1", "ok", 0.8)
    after = metrics.REGISTRY.get_sample_value(
        "jarvis_live_stt_request_seconds_count", {"tier": "t1", "outcome": "ok"}
    )
    assert after == (before or 0) + 1


def test_a_failing_recorder_never_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("registry broke")

    monkeypatch.setattr(metrics._STT_REQUEST, "labels", boom)
    metrics.stt_request("t1", "ok", 0.1)  # must not raise


def test_metrics_are_served_on_their_own_port_not_the_app_port() -> None:
    metrics.stt_served("t1")
    server = metrics.serve(0)  # 0: any free port
    try:
        r = httpx.get(f"http://127.0.0.1:{server.port}/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "jarvis_live_stt_served_total" in r.text
    finally:
        server.close()
    # The app port is what the Ingress routes to the internet: no /metrics there.
    app = TestClient(create_app(Settings()))
    assert "jarvis_live_stt_served_total" not in app.get("/metrics").text


def test_the_port_setting_is_off_by_default() -> None:
    assert Settings().metrics_port == 0


def test_the_audio_path_does_not_touch_metrics() -> None:
    """feed() and the segmenter must stay free of instrumentation."""
    for rel in ("ingest/segmenter.py",):
        tree = ast.parse((SRC / rel).read_text())
        names = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
        }
        assert not any("metrics" in n for n in names), rel
    pipeline = ast.parse((SRC / "ingest/pipeline.py").read_text())
    feed = next(
        n for n in ast.walk(pipeline) if isinstance(n, ast.FunctionDef) and n.name == "feed"
    )
    used = {n.id for n in ast.walk(feed) if isinstance(n, ast.Name) and n.id in {"metrics", "slo"}}
    assert not used, "SessionPipeline.feed() must not be instrumented"


def test_segmenter_feed_cost_is_unchanged_by_metrics() -> None:
    """The segmenter never calls into metrics, so feeding it costs the same either way; this
    guards against someone wiring a recorder into the per-frame path."""
    cfg = SegmenterConfig(
        aggressiveness=2, frame_ms=30, close_silence_ms=700, max_ms=15_000,
        overlap_ms=0, min_speech_ms=200,
    )  # fmt: skip
    seg = Segmenter(cfg)
    frame = b"\x00\x00" * 480  # 30 ms of silence at 16 kHz
    calls = 5_000
    t0 = time.perf_counter()
    for _ in range(calls):
        seg.feed(frame)
    per_call = (time.perf_counter() - t0) / calls
    assert per_call < 0.0005  # 0.5 ms/frame: orders of magnitude under a 30 ms frame
