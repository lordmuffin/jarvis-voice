import json
from pathlib import Path

from jarvis_live.client.render import TailRenderer
from jarvis_live.client.transport import parse_server_message

V1 = Path(__file__).resolve().parents[3] / "protocol" / "v1" / "fixtures" / "valid"


def _msg(name: str):  # type: ignore[no-untyped-def]
    m = parse_server_message((V1 / f"{name}.json").read_text())
    assert m is not None
    return m


def test_segment_line_has_time_speaker_and_text() -> None:
    seg = json.loads((V1 / "segment.json").read_text())
    line = TailRenderer().render(_msg("segment"))
    mm, ss = divmod(seg["start_ms"] // 1000, 60)
    assert line == [f"[{mm:02d}:{ss:02d}] {seg['speaker']}: {seg['text']}"]


def test_copilot_suggestions_print_once() -> None:
    r = TailRenderer()
    first = r.render(_msg("copilot"))
    assert first and all(isinstance(x, str) for x in first)
    assert r.render(_msg("copilot")) == []  # a repeated snapshot adds nothing


def test_final_note_prints_the_path() -> None:
    out = TailRenderer().render(_msg("final_note"))
    assert (
        out
        and "final note" in out[0]
        and json.loads((V1 / "final_note.json").read_text())["path"] in out[0]
    )


def test_status_and_error() -> None:
    assert TailRenderer().render(_msg("status")) == []
    out = TailRenderer().render(_msg("error"))
    assert out and out[0].startswith("error")
