"""``GET /v1/sessions``: filters, sort and search (titles and transcripts)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.db.models import Device, SegmentRow
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.users import create_user
from tests.conftest import ServerHandle
from tests.fakes import new_device

SM = async_sessionmaker  # type: ignore[type-arg]
# Well in the past, so a session that is still running has been running longer than any ended one.
T0 = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=50)
START, STOP = "", ""  # the match delimiters in a snippet


def at(**delta: float) -> datetime:
    return T0 + timedelta(**delta)


async def seed(
    sm: SM,
    device_id: uuid.UUID,
    title: str | None,
    *,
    status: str = "done",
    mode: str = "meeting",
    started: datetime = T0,
    minutes: float | None = 10,
    says: list[tuple[int, str]] | None = None,
    drafts: list[tuple[int, str]] | None = None,
) -> str:
    """A session, ``minutes`` long (None: still running), that said ``says`` (final segments)."""
    async with sm() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        row = SessionRow(
            device_id=device_id,
            user_id=device.user_id,
            title=title,
            mode=mode,
            status=status,
            channels=["mic"],
            started_at=started,
            ended_at=None if minutes is None else started + timedelta(minutes=minutes),
        )
        db.add(row)
        await db.flush()
        for source, items in (("final", says or []), ("draft", drafts or [])):
            for start_ms, words in items:
                db.add(
                    SegmentRow(
                        session_id=row.id,
                        channel="mic",
                        speaker="me",
                        start_ms=start_ms,
                        end_ms=start_ms + 3000,
                        text=words,
                        source=source,
                    )
                )
        await db.commit()
        return str(row.id)


async def listing(url: str, token: str, **params: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"}) as c:
        r = await c.get("/v1/sessions", params={"limit": 100, **params})
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]


def titles(page: dict[str, Any]) -> list[str | None]:
    return [s["title"] for s in page["items"]]


@pytest.fixture
async def mine(sm: SM) -> tuple[uuid.UUID, str]:
    return await new_device(sm, "list-test")


@pytest.fixture
async def library(server: ServerHandle, sm: SM, mine: tuple[uuid.UUID, str]) -> tuple[str, str]:
    """Six sessions of one user, spread over time, status, mode and length."""
    device, token = mine
    await seed(sm, device, "Platform sync", started=at(days=0), minutes=42)
    await seed(sm, device, "Homelab power budget", mode="solo", started=at(days=-2), minutes=28)
    await seed(sm, device, None, status="failed", mode="solo", started=at(days=-5), minutes=0.5)
    await seed(sm, device, "Vendor call", status="finalizing", started=at(days=-9), minutes=65)
    await seed(
        sm, device, "Live now", status="live", mode="meeting", started=at(days=-12), minutes=None
    )
    await seed(sm, device, "Old retro", started=at(days=-40), minutes=19)
    return server.url, token


async def test_with_no_filters_the_list_is_as_before(library: tuple[str, str]) -> None:
    page = await listing(*library)
    assert titles(page) == [
        "Platform sync",
        "Homelab power budget",
        None,
        "Vendor call",
        "Live now",
        "Old retro",
    ]
    assert page["total"] == 6
    assert all("match" not in s or s["match"] is None for s in page["items"])


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("done", ["Platform sync", "Homelab power budget", "Old retro"]),
        ("failed", [None]),
        ("finalizing", ["Vendor call"]),
        ("live", ["Live now"]),
    ],
)
async def test_filter_by_status(library: tuple[str, str], status: str, expected: list[str]) -> None:
    page = await listing(*library, status=status)
    assert titles(page) == expected and page["total"] == len(expected)


async def test_filter_by_mode(library: tuple[str, str]) -> None:
    solo = await listing(*library, mode="solo")
    assert titles(solo) == ["Homelab power budget", None]
    meeting = await listing(*library, mode="meeting")
    assert meeting["total"] == 4


async def test_filter_by_start_date_includes_the_boundary(library: tuple[str, str]) -> None:
    since = at(days=-9).isoformat()
    page = await listing(*library, since=since)
    assert titles(page) == ["Platform sync", "Homelab power budget", None, "Vendor call"]
    after = await listing(*library, since=(at(days=-9) + timedelta(seconds=1)).isoformat())
    assert "Vendor call" not in titles(after)


async def test_filters_combine(library: tuple[str, str]) -> None:
    page = await listing(*library, status="done", mode="solo", since=at(days=-3).isoformat())
    assert titles(page) == ["Homelab power budget"] and page["total"] == 1


async def test_sort_oldest_first(library: tuple[str, str]) -> None:
    page = await listing(*library, sort="oldest")
    assert titles(page)[0] == "Old retro" and titles(page)[-1] == "Platform sync"


async def test_sort_longest_first_counts_a_running_session_up_to_now(
    library: tuple[str, str],
) -> None:
    page = await listing(*library, sort="longest")
    # "Live now" started in 2026 and has not ended, so it has been running the longest.
    assert titles(page)[0] == "Live now"
    assert titles(page)[1:4] == ["Vendor call", "Platform sync", "Homelab power budget"]
    assert titles(page)[-1] is None  # thirty seconds


async def test_sorting_pages_consistently(library: tuple[str, str]) -> None:
    url, token = library
    first = await listing(url, token, sort="oldest", limit=3, offset=0)
    rest = await listing(url, token, sort="oldest", limit=3, offset=3)
    assert titles(first) + titles(rest) == titles(await listing(url, token, sort="oldest"))


async def test_counts_per_status_ignore_the_status_filter_but_not_the_others(
    library: tuple[str, str],
) -> None:
    page = await listing(*library, status="done")
    assert page["counts"] == {"live": 1, "finalizing": 1, "done": 3, "failed": 1}
    solo = await listing(*library, status="done", mode="solo")
    assert solo["counts"] == {"live": 0, "finalizing": 0, "done": 1, "failed": 1}
    assert solo["total"] == 1


async def test_title_search_is_case_insensitive_and_literal(
    server: ServerHandle, sm: SM, mine: tuple[uuid.UUID, str]
) -> None:
    device, token = mine
    await seed(sm, device, "100% sure plan")
    await seed(sm, device, "Plan_B notes", started=at(days=1))
    await seed(sm, device, "PLAN overview", started=at(days=2))
    await seed(sm, device, "Something else", started=at(days=3))
    assert titles(await listing(server.url, token, q="plan")) == [
        "PLAN overview",
        "Plan_B notes",
        "100% sure plan",
    ]
    # % and _ are not wildcards.
    assert titles(await listing(server.url, token, q="100%")) == ["100% sure plan"]
    assert titles(await listing(server.url, token, q="%")) == ["100% sure plan"]
    assert titles(await listing(server.url, token, q="n_b")) == ["Plan_B notes"]
    assert titles(await listing(server.url, token, q="n.b")) == []


async def test_blank_search_means_no_search(library: tuple[str, str]) -> None:
    assert (await listing(*library, q="   "))["total"] == 6
    assert (await listing(*library, q=""))["total"] == 6


async def test_transcript_search_finds_the_session_and_says_where(
    server: ServerHandle, sm: SM, mine: tuple[uuid.UUID, str]
) -> None:
    device, token = mine
    hit = await seed(
        sm,
        device,
        "ARC runner vendor call",
        says=[
            (10_000, "Welcome everyone."),
            (972_000, "Send me the GPU runner pricing and we'll size it against the DGX node."),
            (1_500_000, "The pricing on the second quote is close."),
        ],
    )
    await seed(
        sm, device, "Unrelated", says=[(5_000, "Nothing about money here.")], started=at(days=1)
    )
    page = await listing(server.url, token, q="pricing", q_in="transcripts")
    assert [s["id"] for s in page["items"]] == [hit] and page["total"] == 1
    m = page["items"][0]["match"]
    assert m["start_ms"] == 972_000  # the first matching line, once per session
    assert f"{START}pricing{STOP}" in m["snippet"]
    assert "GPU runner" in m["snippet"]


async def test_transcript_search_ignores_drafts_and_titles(
    server: ServerHandle, sm: SM, mine: tuple[uuid.UUID, str]
) -> None:
    device, token = mine
    await seed(sm, device, "Pricing review", says=[(1000, "nothing relevant")])
    await seed(sm, device, "Draft only", drafts=[(1000, "pricing in a draft")], started=at(days=1))
    page = await listing(server.url, token, q="pricing", q_in="transcripts")
    assert page["total"] == 0
    by_title = await listing(server.url, token, q="pricing", q_in="titles")
    assert titles(by_title) == ["Pricing review"]
    assert all(s.get("match") is None for s in by_title["items"])


async def test_transcript_search_understands_phrases_and_exclusions(
    server: ServerHandle, sm: SM, mine: tuple[uuid.UUID, str]
) -> None:
    device, token = mine
    await seed(sm, device, "A", says=[(1000, "we pilot on the k3s2 cluster first")])
    await seed(
        sm, device, "B", says=[(1000, "pilot on staging, then the cluster")], started=at(days=1)
    )
    phrase = await listing(server.url, token, q='"pilot on the k3s2"', q_in="transcripts")
    assert titles(phrase) == ["A"]
    exclude = await listing(server.url, token, q="pilot -staging", q_in="transcripts")
    assert titles(exclude) == ["A"]


async def test_transcript_search_combines_with_filters_and_counts(
    server: ServerHandle, sm: SM, mine: tuple[uuid.UUID, str]
) -> None:
    device, token = mine
    await seed(sm, device, "Done one", says=[(1000, "budget talk")])
    await seed(
        sm, device, "Failed one", status="failed", says=[(1000, "budget talk")], started=at(days=1)
    )
    await seed(sm, device, "Other", says=[(1000, "no match")], started=at(days=2))
    page = await listing(server.url, token, q="budget", q_in="transcripts", status="done")
    assert titles(page) == ["Done one"]
    assert page["counts"] == {"live": 0, "finalizing": 0, "done": 1, "failed": 1}


async def test_search_never_crosses_users(server: ServerHandle, sm: SM) -> None:
    dev_a, tok_a = await new_device(sm, "a")
    dev_b, tok_b = await new_device(sm, "b")
    admin = await create_user(sm, "Admin", is_admin=True)
    _, tok_admin = await new_device(sm, "admin", user_id=admin.id)
    # A word nobody else's rows contain, so an admin's "all" view (the whole database) is exact.
    word = f"swordfish{uuid.uuid4().hex[:10]}"
    await seed(sm, dev_a, f"Alice's {word} plan", says=[(1000, f"the launch code is {word}")])
    for q, where in ((word, "transcripts"), (word, "titles")):
        assert (await listing(server.url, tok_b, q=q, q_in=where))["total"] == 0
        assert (await listing(server.url, tok_a, q=q, q_in=where))["total"] == 1
        assert (await listing(server.url, tok_admin, q=q, q_in=where))["total"] == 0  # not "all"
        assert (await listing(server.url, tok_admin, q=q, q_in=where, all="true"))["total"] == 1
    # and a plain user still can't ask for everyone's
    async with httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {tok_b}"}
    ) as c:
        r = await c.get("/v1/sessions", params={"all": "true", "q": word, "q_in": "transcripts"})
        assert r.status_code == 403
    del dev_b


@pytest.mark.parametrize(
    "params",
    [
        {"status": "paused"},
        {"mode": "duet"},
        {"sort": "random"},
        {"q_in": "everything"},
        {"since": "yesterday"},
        {"q": "x" * 201},
    ],
)
async def test_bad_parameters_are_refused(library: tuple[str, str], params: dict[str, str]) -> None:
    url, token = library
    async with httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"}) as c:
        assert (await c.get("/v1/sessions", params=params)).status_code == 422


async def test_total_and_paging_follow_the_filters(library: tuple[str, str]) -> None:
    url, token = library
    p1 = await listing(url, token, mode="meeting", limit=2, offset=0)
    p2 = await listing(url, token, mode="meeting", limit=2, offset=2)
    assert p1["total"] == p2["total"] == 4
    assert len(p1["items"]) == len(p2["items"]) == 2
    assert set(titles(p1)).isdisjoint(titles(p2))


async def test_the_transcript_index_exists_and_the_planner_can_use_it(
    sm: SM, mine: tuple[uuid.UUID, str]
) -> None:
    async with sm() as db:
        names = (
            (
                await db.execute(
                    text("select indexname from pg_indexes where tablename = 'segments'")
                )
            )
            .scalars()
            .all()
        )
        assert "ix_segments_text_fts" in names
        # With sequential scans discouraged (the table is tiny here), the planner picks the index.
        await db.execute(text("set local enable_seqscan = off"))
        plan = "\n".join(
            (
                await db.execute(
                    text(
                        "explain select 1 from segments where source = 'final' and "
                        "to_tsvector('simple'::regconfig, text) @@ "
                        "websearch_to_tsquery('simple'::regconfig, 'pricing')"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "ix_segments_text_fts" in plan
