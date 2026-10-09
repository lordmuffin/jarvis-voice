import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('live','finalizing','done','failed')", name="ck_sessions_status"
        ),
        CheckConstraint("mode IN ('meeting','solo')", name="ck_sessions_mode"),
        Index("ix_sessions_device_started", "device_id", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"))
    title: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="live", server_default="live")
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_only: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Not in the A2 column list, but the duplicate guard must know up front whether a system
    # channel exists (it comes from the create request).
    channels: Mapped[list[str]] = mapped_column(
        ARRAY(String(8)), default=lambda: ["mic"], server_default=text("'{mic}'")
    )


class AudioChunk(Base):
    __tablename__ = "audio_chunks"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "channel", "seq", name="uq_audio_chunks_session_channel_seq"
        ),
        CheckConstraint("channel IN ('mic','system')", name="ck_audio_chunks_channel"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    channel: Mapped[str] = mapped_column(String(8))
    seq: Mapped[int] = mapped_column(BigInteger)
    t_ms: Mapped[int] = mapped_column(BigInteger)
    byte_offset: Mapped[int] = mapped_column(BigInteger)
    byte_len: Mapped[int] = mapped_column(Integer)


class SegmentRow(Base):
    __tablename__ = "segments"
    __table_args__ = (
        CheckConstraint("channel IN ('mic','system')", name="ck_segments_channel"),
        CheckConstraint("speaker IN ('me','them')", name="ck_segments_speaker"),
        CheckConstraint("source IN ('draft','final')", name="ck_segments_source"),
        Index("ix_segments_session_start", "session_id", "start_ms"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    channel: Mapped[str] = mapped_column(String(8))
    speaker: Mapped[str] = mapped_column(String(8))
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8))
    stt_tier: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _now()


class MarkerRow(Base):
    __tablename__ = "markers"
    __table_args__ = (Index("ix_markers_session_t", "session_id", "t_ms"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    t_ms: Mapped[int] = mapped_column(BigInteger)
    label: Mapped[str] = mapped_column(Text)


class CopilotItem(Base):
    """Copilot state. Payload shape is owned by Phase A3; ``kind='snapshot'`` rows hold the
    full protocol ``copilot`` message and the latest one is served by ``GET /v1/sessions/{id}``."""

    __tablename__ = "copilot_items"
    __table_args__ = (Index("ix_copilot_items_session_created", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
