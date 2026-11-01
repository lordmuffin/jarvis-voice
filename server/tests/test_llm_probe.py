"""The LLM path probe (scripts/llm_probe.py), against a local stub that plays scripted streams.

What matters is telling stalls apart: a slow first text, a freeze mid-stream and a late final
chunk have different causes, so a probe that reports only first-text and total time cannot
localise anything."""

import asyncio
import importlib.util
import json
import random
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient
from jarvis_live.voice.schema import VoiceRequest
from jarvis_live.voice.service import VOICE_SYSTEM, voice_user

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "llm_probe.py"
spec = importlib.util.spec_from_file_location("llm_probe", SCRIPT)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
sys.modules["llm_probe"] = probe
spec.loader.exec_module(probe)


class Stub(BaseHTTPRequestHandler):
    """Plays ``script``: (delay_s, delta dict or None for a finish chunk) pairs, as SSE."""

    script: ClassVar[list[tuple[float, dict[str, Any] | None]]] = []
    bodies: ClassVar[list[dict[str, Any]]] = []
    status: ClassVar[int] = 200
    hosts: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        Stub.hosts.append(self.headers["host"])
        body = self.rfile.read(int(self.headers["content-length"]))
        Stub.bodies.append(json.loads(body))
        self.send_response(Stub.status)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        for delay, delta in Stub.script:
            time.sleep(delay)
            choice: dict[str, Any] = (
                {"delta": {}, "finish_reason": "stop"}
                if delta is None
                else {"delta": delta, "finish_reason": None}
            )
            self._chunk(f"data: {json.dumps({'choices': [choice]})}\n\n")
        self._chunk("data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self) -> None:
        text = (
            b'vllm:num_requests_running{engine="0"} 2.0\n'
            b'vllm:num_requests_waiting{engine="0"} 3.0\n'
            b'vllm:kv_cache_usage_perc{engine="0"} 0.25\n'
            b'vllm:prompt_tokens_total{engine="0"} 100.0\n'
        )
        self.send_response(200)
        self.send_header("content-length", str(len(text)))
        self.end_headers()
        self.wfile.write(text)

    def _chunk(self, text: str) -> None:
        data = text.encode()
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def stub() -> Iterator[str]:
    Stub.bodies = []
    Stub.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


TEXT = {"content": "word "}
BODY = {"model": "m", "messages": [], "stream": True}


def test_records_when_each_chunk_arrived(stub: str) -> None:
    Stub.script = [(0.02, TEXT), (0.05, TEXT), (0, None)]
    r = probe.stream_once(stub + "/v1", BODY, timeout_s=5)
    assert r["error"] is None
    assert [c[1] for c in r["chunks"]] == ["text", "text", "finish"]
    times = [c[0] for c in r["chunks"]]
    assert times == sorted(times)
    assert times[1] - times[0] >= 0.04
    assert r["first_text_s"] == times[0]
    assert r["total_s"] >= times[-1]


def test_reasoning_is_not_first_text(stub: str) -> None:
    Stub.script = [(0, {"reasoning_content": "hmm"}), (0.05, TEXT), (0, None)]
    r = probe.stream_once(stub + "/v1", BODY, timeout_s=5)
    assert [c[1] for c in r["chunks"]] == ["reasoning", "text", "finish"]
    assert r["first_text_s"] == r["chunks"][1][0]


def test_http_error_is_recorded_not_raised(stub: str) -> None:
    Stub.script = []
    Stub.status = 500
    r = probe.stream_once(stub + "/v1", BODY, timeout_s=5)
    assert "500" in r["error"]
    assert r["first_text_s"] is None


def test_unreachable_is_recorded_not_raised() -> None:
    r = probe.stream_once("http://127.0.0.1:9/v1", BODY, timeout_s=1)
    assert r["error"]


def chunks(*spec: tuple[float, str]) -> list[list[Any]]:
    return [[t, kind] for t, kind in spec]


@pytest.mark.parametrize(
    ("spec", "total", "expected"),
    [
        # Healthy.
        (((0.4, "text"), (0.6, "text"), (0.7, "finish")), 0.7, []),
        # First text late: queueing or a blocked prefill.
        (((5.5, "text"), (5.7, "text"), (5.8, "finish")), 5.8, ["first_text"]),
        # Frozen between two text chunks.
        (((0.4, "text"), (5.4, "text"), (5.5, "finish")), 5.5, ["mid_stream"]),
        # All the text arrived, the end did not.
        (((0.4, "text"), (0.6, "text"), (5.6, "finish")), 5.6, ["late_final"]),
        # Last chunk before the end of the body, then nothing.
        (((0.4, "text"), (0.6, "finish")), 5.6, ["late_final"]),
        # Both at once are both reported.
        (((5.5, "text"), (10.5, "text"), (10.6, "finish")), 10.6, ["first_text", "mid_stream"]),
    ],
)
def test_classify(spec: tuple[tuple[float, str], ...], total: float, expected: list[str]) -> None:
    r = {"chunks": chunks(*spec), "first_text_s": spec[0][0], "total_s": total, "error": None}
    assert probe.classify(r, first_text_s=2.0, gap_s=1.0) == expected


def test_classify_flags_an_error_as_such() -> None:
    r = {"chunks": [], "first_text_s": None, "total_s": 1.0, "error": "HTTP 500"}
    assert probe.classify(r, first_text_s=2.0, gap_s=1.0) == ["error"]


def test_max_gap_reports_the_worst_pause_and_where_it_was() -> None:
    r = {
        "chunks": chunks((0.4, "text"), (0.5, "text"), (3.5, "text"), (3.6, "finish")),
        "first_text_s": 0.4,
        "total_s": 3.6,
        "error": None,
    }
    assert probe.max_gap(r) == pytest.approx((3.0, 2))


def test_parse_metrics_reads_engine_load() -> None:
    text = (
        '# HELP x\nvllm:num_requests_running{engine="0",model_name="m"} 2.0\n'
        'vllm:num_requests_waiting{engine="0",model_name="m"} 7.0\n'
        'vllm:num_requests_waiting_by_reason{engine="0",reason="capacity"} 9.0\n'
        'vllm:kv_cache_usage_perc{engine="0",model_name="m"} 0.31\n'
        'vllm:prompt_tokens_total{engine="0",model_name="m"} 1.5e+03\n'
    )
    assert probe.parse_metrics(text) == {
        "running": 2.0,
        "waiting": 7.0,
        "kv": 0.31,
        "prompt_tokens": 1500.0,
    }


def test_load_window_summarises_samples_during_a_request() -> None:
    samples = [
        (0.0, {"running": 1.0, "waiting": 0.0, "kv": 0.1, "prompt_tokens": 100.0}),
        (1.0, {"running": 3.0, "waiting": 2.0, "kv": 0.2, "prompt_tokens": 100.0}),
        (2.0, {"running": 2.0, "waiting": 1.0, "kv": 0.4, "prompt_tokens": 9000.0}),
        (3.0, {"running": 9.0, "waiting": 9.0, "kv": 0.9, "prompt_tokens": 9999.0}),
    ]
    assert probe.load_window(samples, 0.5, 2.5) == {
        "running_at_start": 1.0,
        "waiting_at_start": 0.0,
        "running_max": 3.0,
        "waiting_max": 2.0,
        "kv_max": 0.4,
        "prompt_tokens_delta": 8900.0,
        "samples": 3,
    }


def test_load_window_with_no_metrics_is_empty() -> None:
    assert probe.load_window([], 0.0, 1.0) == {}


def test_body_is_identical_across_paths_and_turns_thinking_off() -> None:
    a = probe.build_body("m", "short", 60, nonce=None)
    assert a == probe.build_body("m", "short", 60, nonce=None)
    assert a["stream"] is True
    assert a["temperature"] == 0
    assert a["max_tokens"] == 60
    assert a["chat_template_kwargs"] == {"enable_thinking": False}


def test_nonce_changes_the_start_of_the_prompt_so_prefix_cache_cannot_hit() -> None:
    a = probe.build_body("m", "short", 60, nonce="a1")
    b = probe.build_body("m", "short", 60, nonce="b2")
    assert a["messages"][0]["content"][:8] != b["messages"][0]["content"][:8]


def test_long_prompt_is_much_longer_than_short() -> None:
    short = probe.build_body("m", "short", 60, nonce=None)
    long = probe.build_body("m", "long", 60, nonce=None)
    assert len(json.dumps(long)) > 10 * len(json.dumps(short))


def test_pair_order_alternates_so_neither_path_always_warms_the_cache() -> None:
    orders = [probe.pair_order(i) for i in range(4)]
    assert orders == [
        ("direct", "gateway"),
        ("gateway", "direct"),
        ("direct", "gateway"),
        ("gateway", "direct"),
    ]


def test_run_pairs_sends_identical_bodies_and_writes_one_row_per_request(
    stub: str, tmp_path: Path
) -> None:
    Stub.script = [(0, TEXT), (0, None)]
    out = tmp_path / "run.jsonl"
    probe.run(
        probe.Options(
            direct_url=stub + "/v1",
            gateway_url=stub + "/v1",
            metrics_url=stub + "/metrics",
            model="m",
            prompt="short",
            max_tokens=60,
            pairs=2,
            pause_s=0,
            out=out,
            label="idle",
        )
    )
    assert Stub.bodies[0] == Stub.bodies[1]
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [(r["pair"], r["path"]) for r in rows] == [
        (0, "direct"),
        (0, "gateway"),
        (1, "gateway"),
        (1, "direct"),
    ]
    assert {r["label"] for r in rows} == {"idle"}
    assert all(r["prompt"] == "short" and r["max_tokens"] == 60 for r in rows)
    assert all(isinstance(r["wall_ts"], float) for r in rows)
    assert all("stalls" in r and "load" in r for r in rows)


def row(path: str, first: float, total: float, stalls: list[str], running: float = 1) -> dict:
    return {
        "path": path,
        "label": "busy",
        "prompt": "long",
        "max_tokens": 200,
        "first_text_s": first,
        "total_s": total,
        "stalls": stalls,
        "max_gap_s": 0.1,
        "error": None,
        "load": {"running_max": running, "waiting_max": 0},
    }


def test_summarise_groups_by_condition_and_counts_each_stall_kind() -> None:
    rows = [row("direct", 0.4, 1.0, []) for _ in range(9)]
    rows.append(row("direct", 5.5, 6.0, ["first_text"]))
    rows += [row("gateway", 0.5, 1.2, []) for _ in range(8)]
    rows.append(row("gateway", 0.5, 6.0, ["mid_stream"]))
    rows.append(row("gateway", 0.5, 6.0, ["late_final"]))
    summary = {s["path"]: s for s in probe.summarise(rows)}
    assert summary["direct"]["n"] == 10
    assert summary["direct"]["first_text_p50"] == pytest.approx(0.4)
    assert summary["direct"]["first_text_max"] == pytest.approx(5.5)
    assert summary["direct"]["stalls"] == {"first_text": 1}
    assert summary["gateway"]["stalls"] == {"mid_stream": 1, "late_final": 1}
    assert summary["gateway"]["stalled"] == 2


def test_summarise_counts_errors_apart_from_latency() -> None:
    rows = [row("gateway", 0.5, 1.0, []) for _ in range(3)]
    rows.append(
        {**row("gateway", 0, 0, ["error"]), "first_text_s": None, "total_s": None, "error": "x"}
    )
    s = probe.summarise(rows)[0]
    assert s["n"] == 4
    assert s["errors"] == 1
    assert s["first_text_p50"] == pytest.approx(0.5)


def test_percentile_uses_nearest_rank_so_p99_of_200_is_a_real_sample() -> None:
    values = sorted(float(i) for i in range(1, 201))
    assert probe.percentile(values, 0.5) == 100.0
    assert probe.percentile(values, 0.95) == 190.0
    assert probe.percentile(values, 0.99) == 198.0


def test_summarise_by_load_splits_what_the_engine_was_doing() -> None:
    rows = [
        {**row("direct", 0.4, 1.0, []), "load": {"running_max": 1, "waiting_max": 0}},
        {**row("direct", 0.4, 1.0, []), "load": {"running_max": 3, "waiting_max": 0}},
        {**row("direct", 0.4, 1.0, []), "load": {"running_max": 7, "waiting_max": 0}},
        {**row("direct", 9.0, 9.0, ["first_text"]), "load": {"running_max": 8, "waiting_max": 4}},
    ]
    assert [s["load"] for s in probe.summarise(rows, by_load=True)] == [
        "1-3 others",
        "4+ others",
        "alone",
        "queued",
    ]


def test_paired_separates_gateway_only_stalls_from_stalls_on_both() -> None:
    def pair(n: int, direct: list[str], gateway: list[str]) -> list[dict]:
        return [
            {**row("direct", 1, 1, direct), "pair": n},
            {**row("gateway", 1, 1, gateway), "pair": n},
        ]

    rows = pair(0, [], []) + pair(1, [], ["mid_stream"]) + pair(2, ["first_text"], ["late_final"])
    rows += pair(3, ["mid_stream"], [])
    assert probe.paired(rows) == {
        "pairs": 4,
        "neither": 1,
        "direct_only": 1,
        "gateway_only": 1,
        "both": 1,
    }


def test_report_prints_a_row_for_each_path(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    f = tmp_path / "r.jsonl"
    rows = [{**row("direct", 0.4, 1.0, []), "pair": 0}, {**row("gateway", 0.5, 1.2, []), "pair": 0}]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    assert probe.main(["report", str(f)]) == 0
    out = capsys.readouterr().out
    assert "direct" in out
    assert "gateway" in out


def test_burst_after_gap_tells_held_output_from_a_slow_engine() -> None:
    # Held back and released at once: the tokens had been generated during the gap.
    held = {
        "chunks": chunks(
            (0.4, "text"), (3.9, "text"), (3.9001, "text"), (3.9002, "text"), (3.9003, "finish")
        )
    }
    assert probe.burst_after_gap(held, window_s=0.02) == 4
    # Resumed at the normal pace: the engine itself had stopped.
    slow = {"chunks": chunks((0.4, "text"), (3.9, "text"), (4.0, "text"), (4.1, "finish"))}
    assert probe.burst_after_gap(slow, window_s=0.02) == 1
    assert probe.burst_after_gap({"chunks": []}, window_s=0.02) == 0


def test_heartbeat_window_reports_the_slowest_beat_touching_the_request() -> None:
    # (when the beat was sent, how long the server took to answer)
    beats = [(0.0, 0.01), (1.0, 4.0), (5.5, 0.01), (9.0, 7.0)]
    assert probe.heartbeat_window(beats, 2.0, 6.0) == pytest.approx(4.0)
    assert probe.heartbeat_window(beats, 5.6, 6.0) == 0.0


def test_heartbeat_counts_a_beat_still_unanswered_when_the_request_ended() -> None:
    assert probe.heartbeat_window([(1.0, 9.0)], 0.5, 2.0) == pytest.approx(9.0)


def test_heartbeat_thread_records_latency(stub: str) -> None:
    with probe.Heartbeat(stub + "/metrics", interval_s=0.01) as beat:
        time.sleep(0.1)
    assert beat.beats
    assert all(latency >= 0 for _, latency in beat.beats)


def test_run_records_heartbeats_beside_each_request(stub: str, tmp_path: Path) -> None:
    Stub.script = [(0.05, TEXT), (0.05, TEXT), (0, None)]
    out = tmp_path / "run.jsonl"
    probe.run(
        probe.Options(
            direct_url=stub + "/v1",
            gateway_url=stub + "/v1",
            metrics_url=stub + "/metrics",
            gateway_heartbeat_url=stub + "/metrics",
            model="m",
            prompt="short",
            max_tokens=60,
            pairs=1,
            pause_s=0,
            out=out,
            label="x",
            sample_s=0.01,
        )
    )
    row = json.loads(out.read_text().splitlines()[0])
    assert row["engine_api_beat_s"] is not None
    assert row["gateway_api_beat_s"] is not None
    assert "gap_burst" in row


def test_run_records_dns_beside_each_request(stub: str, tmp_path: Path) -> None:
    Stub.script = [(0.05, TEXT), (0, None)]
    out = tmp_path / "run.jsonl"
    probe.run(
        probe.Options(
            direct_url=stub + "/v1",
            gateway_url=stub + "/v1",
            metrics_url=None,
            dns_watch_host="localhost",
            model="m",
            prompt="short",
            max_tokens=60,
            pairs=1,
            pause_s=0,
            out=out,
            label="x",
            sample_s=0.01,
        )
    )
    assert "dns_beat_s" in json.loads(out.read_text().splitlines()[0])


def test_pin_resolves_once_and_keeps_the_name_for_the_host_header() -> None:
    url, host = probe.pin("http://localhost:4000/v1", lookup=lambda name: "10.0.0.1")
    assert url == "http://10.0.0.1:4000/v1"
    assert host == "localhost:4000"


def test_pin_leaves_https_alone_because_the_certificate_names_the_host() -> None:
    url, host = probe.pin("https://gateway.example/v1", lookup=lambda name: "10.0.0.1")
    assert url == "https://gateway.example/v1"
    assert host is None


def test_stream_once_sends_the_pinned_host_header(stub: str) -> None:
    Stub.script = [(0, TEXT), (0, None)]
    Stub.hosts = []
    probe.stream_once(stub + "/v1", BODY, timeout_s=5, host="gateway.example:4000")
    assert Stub.hosts == ["gateway.example:4000"]


def test_dns_watch_times_lookups_and_counts_a_failure_as_a_slow_beat() -> None:
    def fail(name: str) -> str:
        time.sleep(0.02)
        raise OSError("Try again")

    with probe.DnsWatch("x.example", interval_s=0.01, lookup=fail) as watch:
        time.sleep(0.1)
    assert watch.beats
    assert watch.beats[0][1] >= 0.02
    assert watch.failures >= 1


def test_run_makes_no_lookup_on_the_request_path(
    stub: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lookup per request would put DNS trouble into the numbers this probe exists to separate."""
    Stub.script = [(0, TEXT), (0, None)]
    calls: list[str] = []
    real = probe.socket.getaddrinfo

    def counting(host: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(host)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(probe.socket, "getaddrinfo", counting)
    named = stub.replace("127.0.0.1", "localhost")
    probe.run(
        probe.Options(
            direct_url=named + "/v1",
            gateway_url=named + "/v1",
            metrics_url=named + "/metrics",
            model="m",
            prompt="short",
            max_tokens=60,
            pairs=3,
            pause_s=0,
            out=tmp_path / "r.jsonl",
            label="x",
            sample_s=0.01,
        )
    )
    assert calls.count("localhost") <= 3  # once per distinct url, at the start


def test_load_body_is_new_text_every_time_so_the_prefix_cache_cannot_serve_it() -> None:
    rng = random.Random(1)
    a = probe.build_load_body("m", 50, rng)
    b = probe.build_load_body("m", 50, rng)
    assert a["messages"][0]["content"][:40] != b["messages"][0]["content"][:40]
    assert a["max_tokens"] == 1
    assert a["chat_template_kwargs"] == {"enable_thinking": False}


def test_load_sends_from_each_client_until_the_deadline(stub: str) -> None:
    Stub.script = [(0, TEXT), (0, None)]
    Stub.bodies = []
    rows = probe.load(stub + "/v1", "m", words=10, clients=2, duration_s=0.2, pause_s=0.02)
    assert len(Stub.bodies) >= 2
    assert {r["client"] for r in rows} == {0, 1}
    assert all(r["error"] is None for r in rows)


def test_a_request_past_its_deadline_is_recorded_not_waited_for(stub: str) -> None:
    Stub.script = [(0.03, TEXT) for _ in range(20)] + [(0, None)]
    r = probe.stream_once(stub + "/v1", BODY, timeout_s=5, deadline_s=0.1)
    assert "deadline" in r["error"]
    assert r["total_s"] < 0.5


# --- Arm V: the request voice actually makes. ---------------------------------------------------


REPOS = {
    "lordmuffin/jarvis-voice": "Jarvis Live: transcription server, copilot, web dashboard, "
    "Android and macOS apps, CI",
}


def sent_by_the_real_client(model: str, user: str) -> dict[str, Any]:
    """What ``LiteLLMClient`` puts on the wire for voice, read off a fake transport."""
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        reply = {"role": "assistant", "content": '{"target": "none"}'}
        return httpx.Response(200, json={"choices": [{"message": reply}]})

    async def call() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            await LiteLLMClient("http://gateway/v1", client).complete_json(
                model=model, system=VOICE_SYSTEM, user=user, schema=VoiceRequest, timeout_s=30.0
            )

    asyncio.run(call())
    return sent[0]


def test_voice_prompt_and_schema_are_the_servers_own() -> None:
    assert probe.VOICE_SYSTEM == VOICE_SYSTEM
    assert probe.VOICE_SCHEMA == VoiceRequest.model_json_schema()


def test_voice_user_renders_as_the_server_does() -> None:
    args: dict[str, Any] = {
        "today": "2026-11-01",
        "repos": REPOS,
        "commentable": [],
        "known": [],
        "said": "open a pie session on the backup job",
    }
    assert probe.voice_user(**args) == voice_user(**args)


def test_voice_timeout_is_the_servers_default() -> None:
    assert probe.VOICE_TIMEOUT_S == Settings.model_fields["voice_timeout_s"].default


def test_voice_body_is_exactly_what_the_client_sends() -> None:
    body = probe.build_voice_body("fast-chat", pair=3, today="2026-11-01")
    assert body == sent_by_the_real_client("fast-chat", body["messages"][1]["content"])


def test_voice_body_has_no_streaming_thinking_or_token_arguments() -> None:
    body = probe.build_voice_body("fast-chat", pair=0, today="2026-11-01")
    assert not {"stream", "stream_options", "chat_template_kwargs", "max_tokens"} & set(body)


def test_voice_body_differs_only_in_the_model_between_paths() -> None:
    gateway = probe.build_voice_body("fast-chat", pair=5, today="2026-11-01")
    direct = probe.build_voice_body("flash", pair=5, today="2026-11-01")
    assert {**gateway, "model": "flash"} == direct


def test_voice_requests_vary_by_pair_and_stay_invented() -> None:
    said = {
        probe.build_voice_body("m", pair=p, today="2026-11-01")["messages"][1]["content"]
        for p in range(200)
    }
    assert len(said) >= 12
    assert not any("192.168." in s or ".lab." in s for s in said)
