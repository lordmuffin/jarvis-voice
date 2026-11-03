"""The held-ask latency report: nearest-rank percentiles per device and model, per stage."""

from jarvis_live.ask_report import AskRow, nearest_rank, render, summarize
from jarvis_live.db.models import CopilotItem
from jarvis_live.voice.ask_pass import ASK_KIND
from tests.fakes import make_session, new_device
from tests.work_item_helpers import SM


def row(device: str, model: str, stt: float, llm: float, latency_ms: int | None) -> AskRow:
    return AskRow(device, model, "answer", {"stt": stt, "llm": llm}, latency_ms)


def test_nearest_rank() -> None:
    assert nearest_rank([3.0, 1.0, 2.0, 4.0], 0.5) == 2.0
    assert nearest_rank([float(i) for i in range(1, 21)], 0.95) == 19.0
    assert nearest_rank([], 0.5) is None


def test_groups_by_device_and_model_and_counts_only_played_asks_for_latency() -> None:
    rows = [
        row("Pixel Fold", "flash", 1.0, 3.0, 4200),
        row("Pixel Fold", "flash", 2.0, 5.0, 7800),
        row("Pixel Fold", "flash", 1.5, 4.0, None),  # not played: no first audio
        row("Mac", "flash", 0.8, 2.0, 3100),
    ]
    (mac, fold) = summarize(rows)
    assert (fold.device, fold.model, fold.n, fold.played) == ("Pixel Fold", "flash", 3, 2)
    assert fold.p50["stt"] == 1.5
    assert fold.p95["llm"] == 5.0
    assert (fold.p50["first_audio"], fold.p95["first_audio"]) == (4.2, 7.8)
    assert mac.n == 1


def test_render_names_the_model() -> None:
    text = render(summarize([row("Mac", "qwen3.8-flash-next", 0.8, 2.0, 3100)]))
    assert "qwen3.8-flash-next" in text
    assert "Mac" in text


async def test_reads_the_stored_asks(sm: SM) -> None:
    from jarvis_live.ask_report import load

    device_id, _ = await new_device(sm, "Pixel Fold")
    sid = await make_session(sm, device_id, status="done")
    async with sm() as db:
        db.add(
            CopilotItem(
                session_id=sid,
                kind=ASK_KIND,
                payload={
                    "id": "a1",
                    "outcome": "answer",
                    "model": "flash",
                    "stages": {"stt": 1.0, "llm": 2.0},
                    "latency_ms": 3500,
                    "played": "done",
                },
            )
        )
        await db.commit()
    rows = await load(sm, days=7)
    assert [(r.device, r.model, r.latency_ms) for r in rows] == [("Pixel Fold", "flash", 3500)]
