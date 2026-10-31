"""What a session's transcription and note are doing right now, derived from the jobs actually in
flight in this process (single replica): a producer streaming, an upload being fed, the hub
draining an ended session, or an orchestrator run (finalize, regenerate, reprocess)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Phase = Literal["idle", "queued", "running", "failed"]
JobSource = Literal["live", "upload", "finalize", "regenerate", "reprocess"]
Stage = Literal["transcription", "summary"]


@dataclass
class Job:
    """An orchestrator run on one session. A ``reprocess`` moves from transcription to summary."""

    source: JobSource
    stage: Stage
    started_at: datetime
    # While it transcribes: how much of the recording is done (0-1).
    progress: Callable[[], float | None] | None = None


class ProcessingState(BaseModel):
    transcription: Phase
    summary: Phase
    started_at: datetime | None  # when the job in flight began
    job_source: JobSource | None
    # How much of a recording of known length is transcribed (0-1): an upload, an ending
    # session draining, a reprocess. None otherwise (a live stream has no end).
    progress: float | None = None

    @property
    def busy(self) -> bool:
        return bool({self.transcription, self.summary} & {"queued", "running"})


def derive(
    status: str,
    *,
    started_at: datetime,
    local_only: bool,
    streaming: bool,
    uploading_since: datetime | None,
    draining_since: datetime | None,
    job: Job | None,
    transcription_failed: bool,
    progress: float | None = None,
) -> ProcessingState:
    """``started_at`` is the session's; the rest describe what is in flight for it. ``progress``
    is the in-flight transcription's, if its length is known."""
    state = _derive(
        status,
        started_at=started_at,
        local_only=local_only,
        streaming=streaming,
        uploading_since=uploading_since,
        draining_since=draining_since,
        job=job,
        transcription_failed=transcription_failed,
    )
    if state.transcription == "running" and state.job_source != "live":
        state.progress = progress
    return state


def fraction(done_ms: int, total_ms: int) -> float | None:
    """The share of a recording done, capped at all of it; None when there is nothing to do."""
    return min(1.0, done_ms / total_ms) if total_ms > 0 else None


def _derive(
    status: str,
    *,
    started_at: datetime,
    local_only: bool,
    streaming: bool,
    uploading_since: datetime | None,
    draining_since: datetime | None,
    job: Job | None,
    transcription_failed: bool,
) -> ProcessingState:
    # Local-only sessions are never sent to an LLM: no note is ever queued for them.
    then: Phase = "idle" if local_only else "queued"
    if job is not None:
        if job.stage == "transcription":
            return ProcessingState(
                transcription="running",
                summary=then,
                started_at=job.started_at,
                job_source=job.source,
            )
        return ProcessingState(
            transcription="idle",
            summary="running",
            started_at=job.started_at,
            job_source=job.source,
        )
    if status == "finalizing" and draining_since is not None:
        # ended: the last segments are transcribed, then the note
        return ProcessingState(
            transcription="running", summary=then, started_at=draining_since, job_source="finalize"
        )
    if uploading_since is not None:  # until its session has been ended
        return ProcessingState(
            transcription="running", summary=then, started_at=uploading_since, job_source="upload"
        )
    if status == "live" and streaming:  # the note waits until the recording is ended
        return ProcessingState(
            transcription="running", summary="idle", started_at=started_at, job_source="live"
        )
    return ProcessingState(
        transcription="failed" if transcription_failed else "idle",
        summary="failed" if status == "failed" else "idle",
        started_at=None,
        job_source=None,
    )
