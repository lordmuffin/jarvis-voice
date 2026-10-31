"""Deriving a session's processing state from the jobs actually in flight."""

from datetime import UTC, datetime

from jarvis_live.processing import Job, ProcessingState, derive, fraction

T0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 24, 9, 5, tzinfo=UTC)


def state(status: str = "done", **kw: object) -> ProcessingState:
    args: dict[str, object] = {
        "started_at": T0,
        "local_only": False,
        "streaming": False,
        "uploading_since": None,
        "draining_since": None,
        "job": None,
        "transcription_failed": False,
        "progress": None,
    }
    args.update(kw)
    return derive(status, **args)  # type: ignore[arg-type]


def test_a_finished_session_with_nothing_in_flight_is_idle() -> None:
    s = state("done")
    assert (s.transcription, s.summary, s.job_source, s.started_at) == ("idle", "idle", None, None)
    assert not s.busy


def test_a_failed_note_reads_summary_failed() -> None:
    s = state("failed")
    assert (s.transcription, s.summary) == ("idle", "failed")
    assert not s.busy


def test_a_live_streaming_session_is_transcribing_live() -> None:
    s = state("live", streaming=True)
    assert (s.transcription, s.summary, s.job_source, s.started_at) == (
        "running",
        "idle",
        "live",
        T0,
    )
    assert s.busy


def test_a_live_session_whose_recorder_dropped_is_idle() -> None:
    assert not state("live").busy


def test_an_upload_is_transcribing_with_its_summary_queued() -> None:
    s = state("live", streaming=True, uploading_since=T1)
    assert (s.transcription, s.summary, s.job_source, s.started_at) == (
        "running",
        "queued",
        "upload",
        T1,
    )


def test_an_ending_session_drains_its_transcription_before_the_note() -> None:
    s = state("finalizing", draining_since=T1)
    assert (s.transcription, s.summary, s.job_source, s.started_at) == (
        "running",
        "queued",
        "finalize",
        T1,
    )


def test_a_finalizer_run_is_summarizing() -> None:
    s = state("finalizing", job=Job("finalize", "summary", T1))
    assert (s.transcription, s.summary, s.job_source, s.started_at) == (
        "idle",
        "running",
        "finalize",
        T1,
    )


def test_a_regeneration_is_summarizing() -> None:
    s = state("finalizing", job=Job("regenerate", "summary", T1))
    assert (s.summary, s.job_source) == ("running", "regenerate")


def test_a_reprocess_transcribes_then_summarizes() -> None:
    s = state("finalizing", job=Job("reprocess", "transcription", T1))
    assert (s.transcription, s.summary, s.job_source) == ("running", "queued", "reprocess")
    s = state("finalizing", job=Job("reprocess", "summary", T1))
    assert (s.transcription, s.summary) == ("idle", "running")


def test_a_failed_retranscription_is_reported_until_the_next_run() -> None:
    s = state("done", transcription_failed=True)
    assert (s.transcription, s.summary) == ("failed", "idle")
    assert not s.busy
    s = state("finalizing", job=Job("reprocess", "transcription", T1), transcription_failed=True)
    assert s.transcription == "running"


def test_a_session_stuck_in_finalizing_with_nothing_running_is_idle() -> None:
    # e.g. a restart beyond the recovery window: the user may process it again.
    assert not state("finalizing").busy


def test_a_local_only_session_is_never_summarized() -> None:
    s = state("live", streaming=True, uploading_since=T1, local_only=True)
    assert (s.transcription, s.summary) == ("running", "idle")
    s = state("finalizing", draining_since=T1, local_only=True)
    assert s.summary == "idle"


def test_a_transcription_of_known_length_reports_how_far_it_is() -> None:
    assert state("live", streaming=True, uploading_since=T1, progress=0.42).progress == 0.42
    assert state("finalizing", draining_since=T1, progress=0.9).progress == 0.9
    job = Job("reprocess", "transcription", T1)
    assert state("finalizing", job=job, progress=0.1).progress == 0.1


def test_progress_is_only_reported_while_a_recording_is_being_transcribed() -> None:
    assert state("live", streaming=True, progress=0.5).progress is None  # a stream has no end
    assert state("finalizing", job=Job("reprocess", "summary", T1), progress=1.0).progress is None
    assert state("done", progress=0.5).progress is None
    assert state("live", streaming=True, uploading_since=T1).progress is None  # length unknown


def test_fraction_is_the_share_of_a_recording_done_capped_at_all_of_it() -> None:
    assert fraction(500, 2000) == 0.25
    assert fraction(2500, 2000) == 1.0
    assert fraction(0, 0) is None  # nothing to transcribe: no measure
