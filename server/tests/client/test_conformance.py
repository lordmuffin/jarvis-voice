"""The Linux client against the shared protocol fixtures (the same ones the server and Swift
tests use): what it sends validates against the schemas, what it receives parses, and the
binary frame is byte-exact."""

import json
from pathlib import Path

import jsonschema
import pytest

from jarvis_live.client import transport as t
from jarvis_live.protocol import decode_frame, encode_frame

V1 = Path(__file__).resolve().parents[3] / "protocol" / "v1"

# Which side produces each schema. A new schema must be added here, or the guard fails.
CLIENT_SENT = {
    "session_create_request",
    "ticket_request",
    "hello",
    "draft_segment",
    "marker",
    "end",
}
SERVER_EVENTS = {"hello_ack", "ack", "segment", "copilot", "status", "final_note", "error"}
REST_RESPONSES = {"session_create_response", "ticket_response"}


def _schema(name: str) -> dict[str, object]:
    return json.loads((V1 / "schemas" / f"{name}.json").read_text())  # type: ignore[no-any-return]


def _validate(name: str, instance: object) -> None:
    jsonschema.Draft202012Validator(
        _schema(name), format_checker=jsonschema.FormatChecker()
    ).validate(instance)


def _fixtures(kind: str, names: set[str]) -> list[Path]:
    return sorted(
        f for f in (V1 / "fixtures" / kind).glob("*.json") if f.stem.split("__")[0] in names
    )


def test_every_schema_is_classified() -> None:
    schemas = {s.stem for s in (V1 / "schemas").glob("*.json")}
    assert schemas == CLIENT_SENT | SERVER_EVENTS | REST_RESPONSES


@pytest.mark.parametrize("fixture", _fixtures("valid", SERVER_EVENTS), ids=lambda f: f.stem)
def test_server_event_fixtures_parse(fixture: Path) -> None:
    msg = t.parse_server_message(fixture.read_text())
    assert msg is not None
    assert msg.type == fixture.stem.split("__")[0]
    assert json.loads(msg.model_dump_json(exclude_none=False)).keys() >= {"type"}


@pytest.mark.parametrize("fixture", _fixtures("invalid", SERVER_EVENTS), ids=lambda f: f.stem)
def test_invalid_server_event_fixtures_are_rejected(fixture: Path) -> None:
    assert t.parse_server_message(fixture.read_text()) is None


def test_messages_the_client_sends_validate() -> None:
    _validate(
        "hello",
        json.loads(t.make_hello("linux-box", {"mic": None, "system": None}).model_dump_json()),
    )
    _validate(
        "hello", json.loads(t.make_hello("linux-box", {"mic": 41, "system": 7}).model_dump_json())
    )
    _validate("marker", json.loads(t.Marker(t_ms=1500, label="decision").model_dump_json()))
    _validate("end", json.loads(t.End().model_dump_json()))
    draft = t.DraftSegment(channel="mic", start_ms=0, end_ms=900, text="hi", final=True)
    _validate("draft_segment", json.loads(draft.model_dump_json()))
    _validate("ticket_request", t.ticket_body())
    _validate(
        "session_create_request", t.session_create_body("Standup", meeting=False, system=False)
    )
    _validate("session_create_request", t.session_create_body(None, meeting=True, system=True))


def test_the_client_never_builds_a_system_only_session() -> None:
    body = t.session_create_body(None, meeting=False, system=True)
    assert body["channels"] == ["mic", "system"]  # system always rides with mic


@pytest.mark.parametrize("fixture", _fixtures("invalid", {"hello"}), ids=lambda f: f.stem)
def test_invalid_hello_fixtures_are_still_invalid_for_the_schema(fixture: Path) -> None:
    with pytest.raises(jsonschema.ValidationError):
        _validate("hello", json.loads(fixture.read_text()))


def test_binary_frame_fixture_round_trips_byte_exact() -> None:
    raw = bytes.fromhex((V1 / "fixtures" / "frame_mic_seq7.hex").read_text().strip())
    f = decode_frame(raw)
    assert (f.channel, f.seq, f.t_ms, len(f.pcm)) == (0, 7, 140, 640)
    assert encode_frame(f.channel, f.seq, f.t_ms, f.pcm) == raw
