"""Filter, sort and search for the session list.

Titles match with ``ILIKE``. Transcripts match with Postgres full text over *final* segments
(drafts are provisional), using the ``simple`` configuration so no language is assumed; the
partial GIN index ``ix_segments_text_fts`` serves exactly the expression used here. Queries are
read as web search syntax: words, ``"phrases"`` and ``-exclusions``.
"""

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    ColumnClause,
    ColumnElement,
    exists,
    func,
    literal_column,
    select,
    true,
)
from sqlalchemy.sql.selectable import LateralFromClause

from jarvis_live.db.models import SegmentRow
from jarvis_live.db.models import Session as SessionRow

Status = Literal["live", "finalizing", "done", "failed"]
Mode = Literal["meeting", "solo"]
Sort = Literal["newest", "oldest", "longest"]
SearchIn = Literal["titles", "transcripts"]

STATUSES: tuple[Status, ...] = ("live", "finalizing", "done", "failed")
MAX_QUERY = 200

# Private-use characters mark the match in a snippet; no HTML ever leaves the server.
MATCH_START, MATCH_STOP = "", ""

_CONFIG: ColumnClause[Any] = literal_column("'simple'::regconfig")
_HEADLINE = f"StartSel={MATCH_START},StopSel={MATCH_STOP},MaxWords=24,MinWords=8,MaxFragments=1"


def _vector() -> ColumnElement[Any]:
    return func.to_tsvector(_CONFIG, SegmentRow.text)


def _query(q: str) -> ColumnElement[Any]:
    return func.websearch_to_tsquery(_CONFIG, q)


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _said(q: str) -> list[ColumnElement[bool]]:
    return [SegmentRow.source == "final", _vector().op("@@")(_query(q))]


def filters(
    *,
    mode: Mode | None,
    since: datetime | None,
    q: str | None,
    search_in: SearchIn,
) -> list[ColumnElement[bool]]:
    """Every filter except status (the status counts are computed without it)."""
    where: list[ColumnElement[bool]] = []
    if mode is not None:
        where.append(SessionRow.mode == mode)
    if since is not None:
        where.append(SessionRow.started_at >= since)
    if q:
        if search_in == "titles":
            where.append(SessionRow.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
        else:
            where.append(exists().where(SegmentRow.session_id == SessionRow.id, *_said(q)))
    return where


def order_by(sort: Sort) -> list[ColumnElement[Any]]:
    if sort == "oldest":
        return [SessionRow.started_at.asc(), SessionRow.id.asc()]
    if sort == "longest":
        # A session still running counts up to now.
        length = func.coalesce(SessionRow.ended_at, func.now()) - SessionRow.started_at
        return [length.desc(), SessionRow.started_at.desc(), SessionRow.id.desc()]
    return [SessionRow.started_at.desc(), SessionRow.id.desc()]


def first_match(q: str) -> LateralFromClause:
    """The first matching final line of each session, with the match marked in a snippet."""
    return (
        select(
            SegmentRow.start_ms.label("start_ms"),
            func.ts_headline(_CONFIG, SegmentRow.text, _query(q), _HEADLINE).label("snippet"),
        )
        .where(SegmentRow.session_id == SessionRow.id, *_said(q))
        .order_by(SegmentRow.start_ms)
        .limit(1)
        .correlate(SessionRow)
        .lateral("match")
    )


def with_match(stmt: Any, q: str) -> Any:
    """Add each row's first match (``start_ms``, ``snippet``) as two extra columns."""
    m = first_match(q)
    return stmt.add_columns(m.c.start_ms, m.c.snippet).join(m, true())


def blank_to_none(q: str | None) -> str | None:
    q = (q or "").strip()
    return q or None
