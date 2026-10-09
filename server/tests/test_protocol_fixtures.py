import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError

from jarvis_live import protocol as p

V1 = Path(__file__).resolve().parents[2] / "protocol" / "v1"

MODELS: dict[str, type[BaseModel]] = {
    "session_create_request": p.SessionCreateRequest,
    "session_create_response": p.SessionCreateResponse,
    "ticket_request": p.TicketRequest,
    "ticket_response": p.TicketResponse,
    "hello": p.Hello,
    "draft_segment": p.DraftSegment,
    "marker": p.Marker,
    "end": p.End,
    "hello_ack": p.HelloAck,
    "ack": p.Ack,
    "segment": p.Segment,
    "copilot": p.Copilot,
    "status": p.Status,
    "final_note": p.FinalNote,
    "error": p.Error,
}


def _fixtures(kind: str) -> list[Path]:
    return sorted((V1 / "fixtures" / kind).glob("*.json"))


def _schema(fixture: Path) -> dict[str, object]:
    name = fixture.stem.split("__")[0]
    return json.loads((V1 / "schemas" / f"{name}.json").read_text())  # type: ignore[no-any-return]


def _model(fixture: Path) -> type[BaseModel]:
    return MODELS[fixture.stem.split("__")[0]]


def test_every_schema_has_a_model_and_both_fixture_kinds() -> None:
    schemas = {s.stem for s in (V1 / "schemas").glob("*.json")}
    assert schemas == set(MODELS)
    for kind in ("valid", "invalid"):
        covered = {f.stem.split("__")[0] for f in _fixtures(kind)}
        assert covered == schemas, f"{kind} fixtures missing for {schemas - covered}"


@pytest.mark.parametrize("fixture", _fixtures("valid"), ids=lambda f: f.stem)
def test_valid_fixture(fixture: Path) -> None:
    raw = fixture.read_text()
    data = json.loads(raw)
    jsonschema.Draft202012Validator.check_schema(_schema(fixture))
    jsonschema.Draft202012Validator(
        _schema(fixture), format_checker=jsonschema.FormatChecker()
    ).validate(data)
    model = _model(fixture).model_validate_json(raw)
    assert json.loads(model.model_dump_json(exclude_unset=True)) == data


@pytest.mark.parametrize("fixture", _fixtures("invalid"), ids=lambda f: f.stem)
def test_invalid_fixture(fixture: Path) -> None:
    raw = fixture.read_text()
    validator = jsonschema.Draft202012Validator(
        _schema(fixture), format_checker=jsonschema.FormatChecker()
    )
    assert list(validator.iter_errors(json.loads(raw))), "schema accepted invalid fixture"
    with pytest.raises(ValidationError):
        _model(fixture).model_validate_json(raw)


def test_message_unions_dispatch_on_type() -> None:
    assert isinstance(p.client_message_adapter.validate_python({"type": "end"}), p.End)
    assert isinstance(
        p.server_message_adapter.validate_python({"type": "ack", "channel": "mic", "seq": 1}), p.Ack
    )


def test_hex_frame_decodes_to_expected_header() -> None:
    data = bytes.fromhex((V1 / "fixtures" / "frame_mic_seq7.hex").read_text().strip())
    assert len(data) == 12 + 320
    frame = p.decode_frame(data)
    assert (frame.version, frame.channel, frame.flags, frame.seq, frame.t_ms) == (1, 0, 0, 7, 140)
    assert frame.pcm == bytes(320)
    assert p.encode_frame(0, 7, 140, bytes(320)) == data


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"\x01" * 11,
        bytes([2, 0, 0, 0]) + bytes(8),
        bytes([1, 2, 0, 0]) + bytes(8),
        bytes([1, 0, 1, 0]) + bytes(8),
        bytes([1, 0, 0, 0]) + bytes(8) + b"\x00",
    ],
)
def test_decode_rejects_malformed(bad: bytes) -> None:
    with pytest.raises(p.FrameError):
        p.decode_frame(bad)
