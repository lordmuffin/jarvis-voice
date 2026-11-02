"""The Jarvis Live dashboard's LLM engine row (Task 21, docs/plans/llm-path-alerts.md)."""

import json
from pathlib import Path
from typing import Any

import pytest

DASHBOARD = Path(__file__).resolve().parents[1] / "deploy/grafana/jarvis-live.json"
ROW = "LLM engine"
SEATS = 8

Panel = dict[str, Any]


@pytest.fixture
def panels() -> list[Panel]:
    panels: list[Panel] = json.loads(DASHBOARD.read_text())["panels"]
    return panels


@pytest.fixture
def row(panels: list[Panel]) -> list[Panel]:
    """The panels under the LLM engine row, which must be the last row."""
    start = next(i for i, p in enumerate(panels) if p["type"] == "row" and p["title"] == ROW)
    below = panels[start + 1 :]
    assert all(p["type"] != "row" for p in below), "the LLM engine row is not at the bottom"
    return below


def titled(row: list[Panel], word: str) -> Panel:
    return next(p for p in row if word in p["title"])


def exprs(panel: Panel) -> list[str]:
    return [t["expr"] for t in panel["targets"]]


def test_the_row_has_the_six_panels(row: list[Panel]) -> None:
    assert len(row) == 6


def test_panel_ids_are_unique(panels: list[Panel]) -> None:
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids))


def test_no_panels_overlap(panels: list[Panel]) -> None:
    cells = [
        (x, y)
        for p in panels
        for x in range(p["gridPos"]["x"], p["gridPos"]["x"] + p["gridPos"]["w"])
        for y in range(p["gridPos"]["y"], p["gridPos"]["y"] + p["gridPos"]["h"])
    ]
    assert len(cells) == len(set(cells))


def test_seats_show_running_and_waiting_against_eight(row: list[Panel]) -> None:
    seats = titled(row, "Seats")
    joined = " ".join(exprs(seats))
    assert "vllm:num_requests_running" in joined
    assert "vllm:num_requests_waiting" in joined
    steps = seats["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert steps[-1]["value"] == SEATS


def test_tokens_are_rates_of_both_counters(row: list[Panel]) -> None:
    joined = " ".join(exprs(titled(row, "Tokens")))
    assert "rate(vllm:generation_tokens_total[" in joined
    assert "rate(vllm:prompt_tokens_total[" in joined


def test_kv_cache_reads_the_gauge_as_a_share(row: list[Panel]) -> None:
    cache = titled(row, "KV cache")
    assert "vllm:kv_cache_usage_perc" in " ".join(exprs(cache))
    assert cache["fieldConfig"]["defaults"]["unit"] == "percentunit"


def test_seat_wait_shows_p50_and_p95(row: list[Panel]) -> None:
    queries = exprs(titled(row, "Wait for a seat"))
    assert any(q.startswith("histogram_quantile(0.5,") for q in queries)
    assert any(q.startswith("histogram_quantile(0.95,") for q in queries)
    assert all("vllm:request_queue_time_seconds_bucket" in q for q in queries)


def test_token_gap_share_matches_the_label_as_the_engine_serves_it(row: list[Panel]) -> None:
    # Prometheus 3.14 stored this engine's bucket as le="1.0", not le="1".
    query = exprs(titled(row, "token gaps"))[0]
    assert "vllm:inter_token_latency_seconds_bucket" in query
    assert r'le=~"1(\\.0)?"' in query
    assert 'le="+Inf"' in query


def test_probe_panel_uses_range_functions(row: list[Panel]) -> None:
    # A 5 minute sample sits on the edge of Prometheus's default 5 minute lookback.
    joined = " ".join(exprs(titled(row, "probe")))
    assert 'max_over_time(probe_duration_seconds{llm_check="inference"}[6m])' in joined
    assert 'min_over_time(probe_success{llm_check="inference"}[6m])' in joined


def test_the_row_does_not_name_a_model_or_a_pod(row: list[Panel]) -> None:
    # It must follow a profile flip, and this repo is public.
    text = json.dumps(row)
    assert "model_name=" not in text
    assert "pod=" not in text
