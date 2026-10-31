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


# The deployment owner. Migration 0002 creates this user and gives it every device and session
# that existed before there were users; the owner claims it at their first Pocket-ID login.
OWNER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # OIDC subject from Pocket-ID. NULL only for the owner before they first sign in.
    oidc_sub: Mapped[str | None] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Final notes go to ``<outbox>/<notes_subdir>/``; NULL writes to the outbox root.
    notes_subdir: Mapped[str | None] = mapped_column(Text, unique=True)
    # Open a PR for each new note automatically (owner and admins only; see publish/).
    auto_publish: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginSession(Base):
    """A web dashboard sign-in, carried by the ``jarvis_live_session`` cookie."""

    __tablename__ = "login_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OidcFlow(Base):
    """An authorization request in flight, keyed by its ``state``."""

    __tablename__ = "oidc_flows"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64))
    pkce_verifier: Mapped[str] = mapped_column(String(128))
    client: Mapped[str] = mapped_column(String(16))  # 'web' | 'android'
    app_challenge: Mapped[str | None] = mapped_column(String(128))
    return_to: Mapped[str] = mapped_column(Text, default="/")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceCode(Base):
    """One-time code handed to the Android app after sign-in, redeemed for a device token."""

    __tablename__ = "device_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    app_challenge: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
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
        Index("ix_sessions_user_started", "user_id", text("started_at DESC")),
        # A client retrying an upload (a watch that never saw the ack) gets its session back.
        Index(
            "uq_sessions_user_client_id",
            "user_id",
            "client_id",
            unique=True,
            postgresql_where=text("client_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL for a recording uploaded from the web dashboard, which has no device.
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"))
    # Copied from the device at creation, so a session stays its owner's if the device goes.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="live", server_default="live")
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_only: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # A work call. Never set by the server itself: the client says so when it creates or uploads
    # the session. The note is tagged and filed apart, and nothing leaves the homelab.
    restricted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Not in the A2 column list, but the duplicate guard must know up front whether a system
    # channel exists (it comes from the create request).
    channels: Mapped[list[str]] = mapped_column(
        ARRAY(String(8)), default=lambda: ["mic"], server_default=text("'{mic}'")
    )
    # Which capture surface made an uploaded recording ("watch"); NULL when not stated.
    source: Mapped[str | None] = mapped_column(String(16))
    # The uploader's own id for the recording; makes an upload retry idempotent.
    client_id: Mapped[str | None] = mapped_column(String(64))


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
    stt_ms: Mapped[int | None] = mapped_column(Integer)  # the STT call, fall-throughs included
    latency_ms: Mapped[int | None] = mapped_column(Integer)  # cut to emitted, dup-guard hold out
    created_at: Mapped[datetime] = _now()


class MarkerRow(Base):
    __tablename__ = "markers"
    __table_args__ = (
        Index("ix_markers_session_t", "session_id", "t_ms"),
        # One marker per (session, time, label); md5 keeps long labels inside the btree limit.
        Index("uq_markers_session_t_label", "session_id", "t_ms", text("md5(label)"), unique=True),
    )

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


PUBLICATION_STATES = ("publishing", "open", "merged", "closed", "skipped", "failed")


class NotePublication(Base):
    """A session's note on its way to the vault repo: one branch and PR per session.

    ``repo_path`` is fixed by the first publish, so a republish after a merge updates the same
    file. ``skipped`` keeps the note out of publishing (and out of auto-publish)."""

    __tablename__ = "note_publications"
    __table_args__ = (
        CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in PUBLICATION_STATES) + ")",
            name="ck_note_publications_state",
        ),
        Index("ix_note_publications_state", "state"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(16))
    repo_path: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(Text)
    head_sha: Mapped[str | None] = mapped_column(String(64))
    # sha256 of the note as last pushed, so the dashboard can tell an edit since then.
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


WORK_ITEM_STATES = ("draft", "approved", "discarded", "merged", "done")


class WorkItem(Base):
    """A candidate piece of work the copilot heard in a session: a draft Claude Code prompt.

    The LLM proposes and refines drafts; the session's owner edits, approves, merges or
    discards them (``workitems/``). ``locked_fields`` are fields the owner edited, which the
    LLM no longer changes. Approval freezes the rendered prompt: ``prompt_md`` is exactly what
    is copied or dispatched, and ``prompt_sha256`` proves it has not changed since.

    ``merged`` means folded into the draft at ``merged_into``. ``done`` means the work shipped:
    the owner said so, or ``merged_pr_url`` merged. That URL outlives a reopen, so the same PR
    does not close the item again."""

    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in WORK_ITEM_STATES) + ")",
            name="ck_work_items_state",
        ),
        UniqueConstraint("session_id", "ref", name="uq_work_items_session_ref"),
        Index("ix_work_items_user_state", "user_id", "state"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    ref: Mapped[str] = mapped_column(String(16))  # "w1", "w2"…: the id the LLM sees
    state: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    title: Mapped[str] = mapped_column(Text)
    repo: Mapped[str | None] = mapped_column(Text)  # "owner/name" from the allowlist
    intent: Mapped[str] = mapped_column(Text, default="", server_default="")
    acceptance_criteria: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    done_when: Mapped[str | None] = mapped_column(Text)
    # [{segment_id, start_ms, end_ms, text}], in transcript order.
    sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    locked_fields: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=list, server_default=text("'{}'")
    )
    merged_into: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work_items.id"))
    prompt_md: Mapped[str | None] = mapped_column(Text)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_pr_url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkItemEvent(Base):
    """Append-only audit log of everything that happens to a work item: who (``actor_user_id``
    NULL is the system/LLM), how (``web`` sign-in, ``device`` token, ``system``), what, and the
    details (an approval carries the prompt and its sha256; a refused dispatch, the reason)."""

    __tablename__ = "work_item_events"
    __table_args__ = (Index("ix_work_item_events_item_at", "work_item_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    work_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    at: Mapped[datetime] = _now()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_kind: Mapped[str] = mapped_column(String(16))
    event: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


DISPATCH_STATES = (
    "queued",
    "running",
    "pr_opened",
    "no_changes",
    "failed",
    "rate_limited",
    "timed_out",
    "cancelled",
    "needs_input",
)


class WorkItemDispatch(Base):
    """One run of an approved work item as headless Claude Code: a Kubernetes Job with its own
    git worktree (``dispatch/``). ``prompt_sha256`` is the approved prompt it was queued with;
    a run whose item no longer carries that prompt never starts.

    A follow-up run ``continues`` an earlier one on its branch, with the owner's ``guidance``
    appended to the approved prompt; ``questions`` are what a run asked the owner (it ended
    ``needs_input``)."""

    __tablename__ = "work_item_dispatches"
    __table_args__ = (
        CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in DISPATCH_STATES) + ")",
            name="ck_work_item_dispatches_state",
        ),
        UniqueConstraint("work_item_id", "attempt", name="uq_work_item_dispatches_attempt"),
        Index("ix_work_item_dispatches_state", "state"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_items.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    repo: Mapped[str] = mapped_column(Text)
    branch: Mapped[str] = mapped_column(Text)
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    job_name: Mapped[str | None] = mapped_column(Text)
    pr_url: Mapped[str | None] = mapped_column(Text)
    pr_number: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    questions: Mapped[str | None] = mapped_column(Text)
    guidance: Mapped[str | None] = mapped_column(Text)
    continues: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("work_item_dispatches.id", ondelete="SET NULL")
    )
