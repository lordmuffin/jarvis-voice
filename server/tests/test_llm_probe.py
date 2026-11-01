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
from pydantic import ValidationError

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient, LLMError, _extract_json
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


def reply(
    content: Any = '{"target": "none"}',
    *,
    status: int = 200,
    delay: float = 0.0,
    usage: dict[str, int] | None = None,
    **message: Any,
) -> dict[str, Any]:
    return {
        "delay": delay,
        "status": status,
        "body": {
            "model": "qwen3-heavy",
            "choices": [{"message": {"role": "assistant", "content": content, **message}}],
            "usage": usage or {"prompt_tokens": 700, "completion_tokens": 20},
        },
    }


GARBLED: dict[str, Any] = {"delay": 0.0, "status": 200, "body": "<html>not json</html>"}
GOOD = reply()
BAD = reply("not json at all")
SERVER_ERROR = reply(status=500)
NO_CONTENT = reply(None, reasoning_content="long thought")
SLOW = reply(delay=5.0)


class ChatStub(BaseHTTPRequestHandler):
    """Answers each POST with the next scripted reply, whole, as a non-streaming endpoint does."""

    replies: ClassVar[list[dict[str, Any]]] = []
    posts: ClassVar[int] = 0
    headers_out: ClassVar[dict[str, str]] = {}

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["content-length"]))
        step = ChatStub.replies[min(ChatStub.posts, len(ChatStub.replies) - 1)]
        ChatStub.posts += 1
        time.sleep(step["delay"])
        body = step["body"]
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        try:
            self.send_response(step["status"])
            for name, value in ChatStub.headers_out.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # the caller gave up, which is the point of a slow reply

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def chat() -> Iterator[str]:
    ChatStub.replies = [GOOD]
    ChatStub.posts = 0
    ChatStub.headers_out = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), ChatStub)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()


BODY = probe.build_voice_body("fast-chat", pair=0, today="2026-11-01")


def test_a_good_reply_records_time_tokens_and_who_answered(chat: str) -> None:
    ChatStub.replies = [reply(delay=0.05)]
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["outcome"] == "ok" and result["error"] is None
    assert result["attempts"] == 1
    assert result["total_s"] >= 0.05
    assert (result["prompt_tokens"], result["completion_tokens"]) == (700, 20)
    assert result["answered_by"] == "qwen3-heavy"


def test_the_caller_waits_for_both_attempts_after_invalid_json(chat: str) -> None:
    ChatStub.replies = [reply("nope", delay=0.1), reply(delay=0.1)]
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["outcome"] == "ok" and result["attempts"] == 2
    assert result["total_s"] >= 0.2
    assert len(result["attempt_s"]) == 2
    assert result["completion_tokens"] == 40


def test_invalid_json_twice_is_a_failure(chat: str) -> None:
    ChatStub.replies = [BAD]
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["outcome"] == "invalid_json" and result["error"]
    assert result["attempts"] == 2


def test_a_timeout_is_a_failure_and_is_not_retried(chat: str) -> None:
    ChatStub.replies = [SLOW]
    result = probe.voice_call(chat, BODY, timeout_s=0.2)
    assert result["outcome"] == "timeout" and result["error"]
    assert result["attempts"] == 1 and ChatStub.posts == 1
    assert 0.2 <= result["total_s"] < 1


def test_a_timeout_on_the_retry_costs_both_attempts(chat: str) -> None:
    ChatStub.replies = [reply("nope", delay=0.1), SLOW]
    result = probe.voice_call(chat, BODY, timeout_s=0.3)
    assert result["outcome"] == "timeout" and result["attempts"] == 2
    assert result["total_s"] >= 0.4


def test_an_http_error_is_a_failure_and_is_not_retried(chat: str) -> None:
    ChatStub.replies = [SERVER_ERROR]
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["outcome"] == "http" and "500" in result["error"]
    assert ChatStub.posts == 1


def test_no_text_content_is_a_failure_and_is_not_retried(chat: str) -> None:
    ChatStub.replies = [NO_CONTENT]
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["outcome"] == "no_content" and result["error"]
    assert ChatStub.posts == 1
    assert result["reasoning"] is True


def test_an_unreadable_body_is_a_failure_and_is_not_retried(chat: str) -> None:
    ChatStub.replies = [GARBLED]
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["outcome"] == "bad_response" and ChatStub.posts == 1


def test_unreachable_is_recorded_not_raised_for_voice() -> None:
    result = probe.voice_call("http://127.0.0.1:1/v1", BODY, timeout_s=1)
    assert result["outcome"] == "transport" and result["attempts"] == 1


@pytest.mark.parametrize(
    "script",
    [[GOOD], [BAD, GOOD], [BAD, BAD], [SERVER_ERROR], [NO_CONTENT], [GARBLED], [BAD, SERVER_ERROR]],
    ids=["good", "retry-good", "retry-bad", "http", "no-content", "garbled", "retry-http"],
)
def test_attempts_match_the_real_client(chat: str, script: list[dict[str, Any]]) -> None:
    ChatStub.replies = script
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert ChatStub.posts == result["attempts"]

    ChatStub.posts = 0

    async def call() -> bool:
        async with httpx.AsyncClient() as client:
            llm = LiteLLMClient(chat, client)
            try:
                await llm.complete_json(
                    model="fast-chat", system="s", user="u", schema=VoiceRequest, timeout_s=2
                )
            except LLMError:
                return False
            return True

    answered = asyncio.run(call())
    assert ChatStub.posts == result["attempts"]
    assert answered == (result["outcome"] == "ok")


def test_a_read_timeout_ends_the_real_client_without_a_retry(chat: str) -> None:
    ChatStub.replies = [SLOW]

    async def call() -> None:
        async with httpx.AsyncClient() as client:
            await LiteLLMClient(chat, client).complete_json(
                model="m", system="s", user="u", schema=VoiceRequest, timeout_s=0.2
            )

    with pytest.raises(LLMError):
        asyncio.run(call())
    assert ChatStub.posts == 1


@pytest.mark.parametrize(
    "content",
    [
        '{"target": "cloud", "title": "x", "task": "y"}',
        "{}",
        '```json\n{"target": "pi"}\n```',
        '<think>\n\n</think>\n{"target": "none"}',
        '<think>a thought</think>{"target": "none"}',
        '{"target": "made up"}',
        '{"target": ["list"]}',
        '{"repo": null, "done_when": null}',
        '{"repo": 3}',
        '{"done_when": 3}',
        '{"title": 3}',
        '{"title": null}',
        '{"task": true}',
        '{"acceptance_criteria": ["a", "b"]}',
        '{"acceptance_criteria": "a"}',
        '{"acceptance_criteria": [1]}',
        '{"acceptance_criteria": null}',
        '{"proposals": "chatty"}',
        '{"proposals": [{"kind": "nonsense"}]}',
        '{"extra": 1}',
        "[]",
        "3",
        "null",
        '"text"',
        "",
        "not json",
        "<think>never closed",
        '{"target": "none"',
    ],
)
def test_reply_check_agrees_with_the_real_schema(content: str) -> None:
    try:
        VoiceRequest.model_validate_json(_extract_json(content))
        valid = True
    except ValidationError:
        valid = False
    assert probe.voice_reply_valid(content) == valid


@pytest.mark.parametrize(
    ("message", "chars"),
    [
        ({"content": "{}"}, 0),
        ({"content": "{}", "reasoning_content": "thinking"}, 8),
        ({"content": "{}", "reasoning": "thinking"}, 8),
        ({"content": "<think>abc</think>{}"}, 3),
        ({"content": "<think>\n\n</think>\n{}"}, 0),
        ({"content": "<think>abc"}, 3),
        ({"content": None, "reasoning_content": "ab"}, 2),
        ({"content": "{}", "reasoning_content": "  \n"}, 0),
    ],
)
def test_reasoning_is_counted_wherever_the_reply_puts_it(
    message: dict[str, Any], chars: int
) -> None:
    assert probe.reasoning_chars(message) == chars


def test_reasoning_across_attempts_is_recorded(chat: str) -> None:
    ChatStub.replies = [reply('{"target": "none"}', reasoning_content="abcd")]
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["reasoning"] is True and result["reasoning_chars"] == 4


def test_a_reply_with_no_reasoning_says_so(chat: str) -> None:
    result = probe.voice_call(chat, BODY, timeout_s=2)
    assert result["reasoning"] is False and result["reasoning_chars"] == 0


def test_the_gateways_fallback_count_is_recorded(chat: str) -> None:
    ChatStub.headers_out = {"x-litellm-attempted-fallbacks": "1"}
    assert probe.voice_call(chat, BODY, timeout_s=2)["fallbacks"] == 1


def test_a_direct_reply_has_no_fallback_count(chat: str) -> None:
    assert probe.voice_call(chat, BODY, timeout_s=2)["fallbacks"] is None


def test_no_reply_text_is_kept(chat: str) -> None:
    ChatStub.replies = [reply('{"task": "SECRET-WORDS"}', reasoning_content="SECRET-THOUGHT")]
    assert "SECRET" not in json.dumps(probe.voice_call(chat, BODY, timeout_s=2))


def voice_options(chat: str, tmp_path: Path, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "direct_url": chat,
        "gateway_url": chat,
        "metrics_url": None,
        "direct_model": "flash",
        "pairs": 2,
        "pause_s": 0,
        "out": tmp_path / "voice.jsonl",
        "label": "normal",
        "client_node": "node-a",
        "proxy_node": "node-b",
    }
    return probe.VoiceOptions(**{**settings, **overrides})


def rows_from(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_run_voice_writes_a_row_per_path_and_records_both_nodes(chat: str, tmp_path: Path) -> None:
    options = voice_options(chat, tmp_path)
    probe.run_voice(options)
    rows = rows_from(options.out)
    assert [r["path"] for r in rows] == ["direct", "gateway", "gateway", "direct"]
    assert {r["client_node"] for r in rows} == {"node-a"}
    assert {r["proxy_node"] for r in rows} == {"node-b"}
    assert {r["prompt"] for r in rows} == {"voice"}
    assert rows[0]["model"] == "flash" and rows[1]["model"] == "fast-chat"


def test_run_voice_sends_the_same_body_down_both_paths(
    chat: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    real = probe.voice_call

    def spy(url: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        seen.append(body)
        return real(url, body, **kwargs)

    monkeypatch.setattr(probe, "voice_call", spy)
    probe.run_voice(voice_options(chat, tmp_path, pairs=1))
    direct, gateway = seen
    assert direct["model"] == "flash" and gateway["model"] == "fast-chat"
    assert {**direct, "model": ""} == {**gateway, "model": ""}


def test_run_voice_uses_the_voice_timeout(
    chat: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: list[float] = []
    real = probe.voice_call

    def spy(url: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        timeouts.append(kwargs["timeout_s"])
        return real(url, body, **kwargs)

    monkeypatch.setattr(probe, "voice_call", spy)
    probe.run_voice(voice_options(chat, tmp_path, pairs=1))
    assert timeouts == [30.0, 30.0]


def test_run_voice_stops_at_the_time_cap_with_whole_pairs(chat: str, tmp_path: Path) -> None:
    ChatStub.replies = [reply(delay=0.06)]
    options = voice_options(chat, tmp_path, pairs=50, max_s=0.05)
    probe.run_voice(options)
    assert len(rows_from(options.out)) == 2


def test_run_voice_keeps_going_after_a_failure(chat: str, tmp_path: Path) -> None:
    ChatStub.replies = [SERVER_ERROR]
    options = voice_options(chat, tmp_path)
    probe.run_voice(options)
    rows = rows_from(options.out)
    assert len(rows) == 4
    assert all(r["outcome"] == "http" and r["stalls"] == ["error"] for r in rows)


def test_voice_cli_takes_the_nodes_and_the_cap(chat: str, tmp_path: Path) -> None:
    out = tmp_path / "cli.jsonl"
    probe.main(
        [
            "voice",
            "--direct-url",
            chat,
            "--gateway-url",
            chat,
            "--direct-model",
            "flash",
            "--client-node",
            "node-a",
            "--proxy-node",
            "node-b",
            "--pairs",
            "1",
            "--max-hours",
            "1",
            "--out",
            str(out),
        ]
    )
    assert rows_from(out)[0]["client_node"] == "node-a"


@pytest.mark.parametrize(
    ("hits", "n", "low", "high"),
    [(0, 200, 0.0, 1.9), (4, 97, 1.6, 10.1), (1, 96, 0.2, 5.7), (84, 97, 78.4, 92.0)],
)
def test_wilson_matches_the_plans_worked_figures(
    hits: int, n: int, low: float, high: float
) -> None:
    got = probe.wilson(hits, n)
    assert (round(got[0] * 100, 1), round(got[1] * 100, 1)) == (low, high)


def test_wilson_of_nothing_says_nothing() -> None:
    assert probe.wilson(0, 0) == (0.0, 1.0)


def vrow(
    path: str,
    total: float,
    *,
    outcome: str = "ok",
    pair: int = 0,
    waiting: float = 0,
    running: float = 3,
    wall: float = 1_000_000.0,
    reasoning: bool = False,
    chars: int = 0,
    attempts: int = 1,
) -> dict[str, Any]:
    return {
        "pair": pair,
        "path": path,
        "label": "normal",
        "prompt": "voice",
        "max_tokens": None,
        "wall_ts": wall,
        "first_text_s": None,
        "total_s": total,
        "chunks": [],
        "max_gap_s": 0.0,
        "stalls": [] if outcome == "ok" else ["error"],
        "error": None if outcome == "ok" else outcome,
        "outcome": outcome,
        "attempts": attempts,
        "prompt_tokens": 600,
        "completion_tokens": 200 if outcome == "ok" else None,
        "reasoning": reasoning,
        "reasoning_chars": chars,
        "answered_by": "flash" if outcome == "ok" else None,
        "fallbacks": 1 if path == "gateway" else None,
        "load": {"waiting_max": waiting, "running_max": running},
    }


def test_voice_summary_counts_a_failure_at_the_time_it_cost() -> None:
    rows = [vrow("gateway", 1.0, pair=i) for i in range(19)]
    rows.append(vrow("gateway", 30.0, outcome="timeout", pair=19))
    summary = probe.voice_summary(rows)[0]
    assert summary["caller_p95"] == 1.0 and summary["caller_max"] == 30.0
    assert summary["ok_max"] == 1.0
    assert summary["timeouts"] == 1 and summary["failed"] == 1


def test_voice_summary_splits_failures_by_kind_with_an_interval() -> None:
    rows = [
        vrow("direct", 1.0),
        vrow("direct", 30.0, outcome="timeout"),
        vrow("direct", 2.0, outcome="invalid_json", attempts=2),
        vrow("direct", 0.1, outcome="http"),
    ]
    summary = probe.voice_summary(rows)[0]
    assert (summary["timeouts"], summary["invalid_json"], summary["other_failures"]) == (1, 1, 1)
    assert summary["failed"] == 3
    assert summary["failure_rate"] == 0.75
    assert summary["failure_ci"] == list(probe.wilson(3, 4))
    assert summary["retried"] == 1


def test_voice_summary_judges_the_target_on_every_request() -> None:
    rows = [vrow("gateway", 1.0, pair=i) for i in range(18)]
    rows += [vrow("gateway", 30.0, outcome="timeout", pair=18 + i) for i in range(2)]
    summary = probe.voice_summary(rows)[0]
    assert summary["caller_p95"] == 30.0 and summary["target_met"] is False
    assert summary["over_target"] == 2


def test_voice_summary_meets_the_target_when_p95_is_under_it() -> None:
    summary = probe.voice_summary([vrow("gateway", 1.0, pair=i) for i in range(20)])[0]
    assert summary["target_met"] is True and summary["over_target"] == 0


def test_voice_summary_reports_reasoning_over_replies_that_came_back() -> None:
    rows = [
        vrow("gateway", 1.0, reasoning=True, chars=800),
        vrow("gateway", 1.0),
        vrow("gateway", 30.0, outcome="timeout"),
    ]
    summary = probe.voice_summary(rows)[0]
    assert (summary["replies"], summary["with_reasoning"]) == (2, 1)
    assert summary["reasoning_ci"] == list(probe.wilson(1, 2))
    assert summary["reasoning_chars_p50"] == 800


def test_voice_summary_records_tokens_and_who_answered() -> None:
    summary = probe.voice_summary([vrow("gateway", 1.0)])[0]
    assert summary["prompt_tokens_p50"] == 600 and summary["completion_tokens_p95"] == 200
    assert summary["answered_by"] == {"flash": 1}
    assert summary["fell_back"] == 1


def test_voice_summary_by_load_splits_each_path() -> None:
    rows = [vrow("gateway", 1.0, waiting=2), vrow("gateway", 1.0, running=1)]
    loads = {s["load"] for s in probe.voice_summary(rows, by_load=True)}
    assert loads == {"queued", "alone"}


def gated(queued: int, alone: int, busy: int) -> list[dict[str, Any]]:
    rows = [vrow("gateway", 1.0, waiting=3) for _ in range(queued)]
    rows += [vrow("direct", 1.0, running=1) for _ in range(alone)]
    return rows + [vrow("direct", 1.0, running=3) for _ in range(busy)]


def test_gate_passes_at_the_plans_thresholds() -> None:
    result = probe.gate(gated(40, 40, 20))
    assert result["passed"] is True
    assert (result["all"]["queued"], result["all"]["alone"]) == (0.4, 0.4)


def test_gate_fails_on_too_few_queued() -> None:
    assert probe.gate(gated(39, 20, 41))["passed"] is False


def test_gate_fails_on_too_many_alone() -> None:
    assert probe.gate(gated(45, 41, 14))["passed"] is False


def test_gate_shows_each_path_on_its_own() -> None:
    result = probe.gate(gated(10, 10, 0))
    assert result["gateway"]["queued"] == 1.0 and result["direct"]["alone"] == 1.0


def test_gate_with_no_metrics_fails() -> None:
    rows = [{**vrow("gateway", 1.0), "load": {}} for _ in range(5)]
    assert probe.gate(rows)["passed"] is False


def test_reopen_fires_only_when_the_wilson_intervals_do_not_overlap() -> None:
    fine = [vrow("direct", 1.0, pair=i) for i in range(200)]
    bad = [vrow("gateway", 30.0, outcome="timeout", pair=i) for i in range(8)]
    good = [vrow("gateway", 1.0, pair=i) for i in range(192)]
    result = probe.reopen(fine + bad + good)
    assert result["failures"]["exceeds"] is True
    assert result["failures"]["gateway"] == [8, 200]

    few = bad[:2] + [vrow("gateway", 1.0, pair=i) for i in range(198)]
    assert probe.reopen(fine + few)["failures"]["exceeds"] is False


def test_reopen_compares_requests_over_target_too() -> None:
    slow = [vrow("gateway", 9.0, pair=i) for i in range(50)]
    fast = [vrow("direct", 1.0, pair=i) for i in range(50)]
    result = probe.reopen(slow + fast)
    assert result["over_target"]["exceeds"] is True
    assert result["failures"]["exceeds"] is False


def test_voice_paired_separates_failures_on_one_path_from_both() -> None:
    rows = [
        vrow("direct", 1.0, pair=0),
        vrow("gateway", 1.5, pair=0),
        vrow("direct", 1.0, pair=1),
        vrow("gateway", 30.0, outcome="timeout", pair=1),
        vrow("direct", 30.0, outcome="timeout", pair=2),
        vrow("gateway", 30.0, outcome="timeout", pair=2),
        vrow("direct", 30.0, outcome="timeout", pair=3),
    ]
    assert probe.voice_paired(rows) == {
        "pairs": 3,
        "neither": 1,
        "direct_only": 0,
        "gateway_only": 1,
        "both": 1,
        "gateway_minus_direct_p50": 0.5,
    }


def test_voice_periods_put_each_request_in_its_load_period() -> None:
    start = 1_000_000.0
    rows = [
        vrow("gateway", 20.0, wall=start, waiting=4),
        vrow("direct", 18.0, wall=start + 60, waiting=4),
        vrow("gateway", 1.0, wall=start + 16 * 60),
        vrow("direct", 30.0, outcome="timeout", wall=start + 17 * 60),
    ]
    first, second = probe.voice_periods(rows, minutes=15)
    assert (first["n"], first["queued"]) == (2, 1.0)
    assert (first["gateway_p50"], first["direct_p50"]) == (20.0, 18.0)
    assert (second["n"], second["queued"], second["failed"]) == (2, 0.0, 1)


def test_report_adds_the_voice_section_and_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    file = tmp_path / "v.jsonl"
    file.write_text("\n".join(json.dumps(vrow(p, 1.0)) for p in ("direct", "gateway")) + "\n")
    assert probe.main(["report", str(file), "--by-load"]) == 0
    out = capsys.readouterr().out
    assert "voice\tgateway" in out
    assert "gate:" in out and "reopen:" in out and "periods:" in out


def test_report_json_carries_the_voice_section(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    file = tmp_path / "v.jsonl"
    file.write_text(json.dumps(vrow("gateway", 1.0)) + "\n")
    assert probe.main(["report", str(file), "--json"]) == 0
    assert "gate" in json.loads(capsys.readouterr().out)["voice"]


def test_report_of_streaming_rows_has_no_voice_section(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    file = tmp_path / "s.jsonl"
    file.write_text(json.dumps({**row("direct", 0.1, 1.0, []), "pair": 0}) + "\n")
    assert probe.main(["report", str(file), "--json"]) == 0
    assert "voice" not in json.loads(capsys.readouterr().out)
