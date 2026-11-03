"""Wire protocol v1 models. Mirrors protocol/v1/schemas; fixtures keep both in sync."""

import struct
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

PROTOCOL_VERSION = 1
HEADER_SIZE = 12
_HEADER = struct.Struct("<BBHII")
BYTES_PER_MS = 32  # 16 kHz * 2 bytes, mono
MIN_PAYLOAD = 20 * BYTES_PER_MS
MAX_PAYLOAD = 200 * BYTES_PER_MS

ChannelName = Literal["mic", "system"]
NonNegInt = Annotated[int, Field(ge=0)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# --- REST ---------------------------------------------------------------------------------


class SessionCreateRequest(_Model):
    title: str | None = None
    mode: Literal["meeting", "solo"]
    channels: list[ChannelName]
    # A work call: recorded on the device, uploaded on purpose. The server keeps it on the
    # homelab: no vault PR, no work items, no Gotify body, and its note is filed apart.
    restricted: bool | None = None

    @field_validator("channels")
    @classmethod
    def _check_channels(cls, v: list[ChannelName]) -> list[ChannelName]:
        if v not in (["mic"], ["mic", "system"]):
            raise ValueError('channels must be ["mic"] or ["mic", "system"]')
        return v


class SessionCreateResponse(_Model):
    id: UUID


class TicketRequest(_Model):
    role: Literal["producer", "viewer"]


class TicketResponse(_Model):
    ticket: str = Field(min_length=1)
    expires_in: Literal[60]


# --- Client -> server ----------------------------------------------------------------------


class Cursor(_Model):
    mic: NonNegInt | None
    system: NonNegInt | None


class Hello(_Model):
    type: Literal["hello"] = "hello"
    protocol: Literal[1]
    device: str
    codec: Literal["pcm16le_16k"]
    resume: Cursor


class DraftSegment(_Model):
    type: Literal["draft_segment"] = "draft_segment"
    channel: ChannelName
    start_ms: NonNegInt
    end_ms: NonNegInt
    text: str
    final: bool


class Marker(_Model):
    type: Literal["marker"] = "marker"
    t_ms: NonNegInt
    label: str


class End(_Model):
    type: Literal["end"] = "end"


AskId = Annotated[str, Field(min_length=1, max_length=64)]


class AskBegin(_Model):
    """The ask key went down. ``t_ms`` is on the audio frames' clock."""

    type: Literal["ask_begin"] = "ask_begin"
    id: AskId
    t_ms: NonNegInt


class AskEnd(_Model):
    type: Literal["ask_end"] = "ask_end"
    id: AskId
    t_ms: NonNegInt


class AskPlayed(_Model):
    """What the client did with a reply. ``latency_ms``: key up to first audio, client clock."""

    type: Literal["ask_played"] = "ask_played"
    id: AskId
    state: Literal["started", "done", "skipped"]
    t_ms: NonNegInt
    latency_ms: NonNegInt | None = None
    reason: Literal["route", "muted", "stale", "tts_failed", "interrupted", "superseded"] | None = (
        None
    )


ClientMessage = Annotated[
    Hello | DraftSegment | Marker | End | AskBegin | AskEnd | AskPlayed,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# --- Server -> client ----------------------------------------------------------------------


class HelloAck(_Model):
    type: Literal["hello_ack"] = "hello_ack"
    session: str
    acked: Cursor


class Ack(_Model):
    type: Literal["ack"] = "ack"
    channel: ChannelName
    seq: NonNegInt


class Segment(_Model):
    type: Literal["segment"] = "segment"
    id: str
    channel: ChannelName
    speaker: Literal["me", "them"]
    start_ms: NonNegInt
    end_ms: NonNegInt
    text: str
    stt_tier: str


class SpeakerInfo(_Model):
    id: str
    label: str | None  # None = not named: shown as "Speaker N"
    enrolled: bool
    is_owner: bool
    ordinal: Annotated[int, Field(ge=1)] | None = None  # "Speaker <ordinal>" while unnamed


class SpeakerAssignment(_Model):
    segment_id: str
    speaker_id: str | None
    rev: NonNegInt  # a client keeps an assignment only if its rev is higher than the one it holds
    final: bool
    mixed: bool | None = None


class SpeakerUpdate(_Model):
    """Who spoke a segment. Sent only to clients that opted in with ``features=speaker_update``."""

    type: Literal["speaker_update"] = "speaker_update"
    speakers: list[SpeakerInfo]
    assignments: list[SpeakerAssignment]


class Note(_Model):
    id: str
    text: str


class Action(_Model):
    id: str
    text: str
    owner: str | None = None
    due: str | None = None


class Suggestion(_Model):
    id: str
    kind: Literal["question", "gap", "counterpoint", "fact_check"]
    text: str
    expires_at_ms: NonNegInt


class Related(_Model):
    path: str
    title: str
    snippet: str
    uri: str


class Copilot(_Model):
    type: Literal["copilot"] = "copilot"
    version: NonNegInt
    notes: list[Note]
    actions: list[Action]
    decisions: list[Note]
    suggestions: list[Suggestion]
    related: list[Related]


class Status(_Model):
    type: Literal["status"] = "status"
    stt_tier: str | None
    llm_ok: bool
    lag_ms: NonNegInt


class FinalNote(_Model):
    type: Literal["final_note"] = "final_note"
    path: str
    title: str


class Error(_Model):
    type: Literal["error"] = "error"
    code: str
    message: str


class VoiceState(_Model):
    """Whether this session takes asks. Sent only to clients with ``features=voice_ask``."""

    type: Literal["voice_state"] = "voice_state"
    available: bool


AskOutcome = Literal["answer", "filed", "empty", "no_stt", "timeout", "failed"]


class AskReply(_Model):
    """The answer to one ask, to be spoken on the device. Never stored as transcript."""

    type: Literal["ask_reply"] = "ask_reply"
    id: AskId
    outcome: AskOutcome
    text: str


ServerMessage = Annotated[
    HelloAck
    | Ack
    | Segment
    | SpeakerUpdate
    | Copilot
    | Status
    | FinalNote
    | Error
    | VoiceState
    | AskReply,
    Field(discriminator="type"),
]
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


# --- Binary frames -------------------------------------------------------------------------

CHANNEL_MIC = 0
CHANNEL_SYSTEM = 1


class FrameError(ValueError):
    """Malformed binary frame."""


@dataclass(frozen=True)
class Frame:
    channel: int
    seq: int
    t_ms: int
    pcm: bytes
    version: int = PROTOCOL_VERSION
    flags: int = 0


def _check_payload(pcm: bytes) -> None:
    if len(pcm) % 2:
        raise FrameError("PCM16 payload must have even length")
    if not MIN_PAYLOAD <= len(pcm) <= MAX_PAYLOAD:
        raise FrameError(
            f"payload must be {MIN_PAYLOAD}-{MAX_PAYLOAD} bytes (20-200 ms), got {len(pcm)}"
        )


def encode_frame(channel: int, seq: int, t_ms: int, pcm: bytes) -> bytes:
    if channel not in (CHANNEL_MIC, CHANNEL_SYSTEM):
        raise FrameError(f"invalid channel {channel}")
    _check_payload(pcm)
    try:
        return _HEADER.pack(PROTOCOL_VERSION, channel, 0, seq, t_ms) + pcm
    except struct.error as e:
        raise FrameError(str(e)) from e


def decode_frame(data: bytes) -> Frame:
    if len(data) < HEADER_SIZE:
        raise FrameError("frame shorter than 12-byte header")
    version, channel, flags, seq, t_ms = _HEADER.unpack_from(data)
    if version != PROTOCOL_VERSION:
        raise FrameError(f"unsupported version {version}")
    if channel not in (CHANNEL_MIC, CHANNEL_SYSTEM):
        raise FrameError(f"invalid channel {channel}")
    if flags != 0:
        raise FrameError(f"non-zero flags {flags:#x}")
    pcm = data[HEADER_SIZE:]
    _check_payload(pcm)
    return Frame(channel=channel, seq=seq, t_ms=t_ms, pcm=pcm, version=version, flags=flags)
