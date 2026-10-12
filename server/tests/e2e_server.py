"""Run the real app with FakeSTT/FakeLLM for the web dashboard's Playwright smoke test.

    python -m tests.e2e_server serve --port 8765      # blocks; prints nothing secret
    python -m tests.e2e_server wav out.wav            # the audio the smoke test replays

The database is ``JARVIS_LIVE_TEST_DATABASE_URL`` if set, else a Postgres testcontainer.

Sign-in goes through a fake Pocket-ID (``tests.fake_oidc``) on ``port + 1``: its page lists
the test users (alice, bob, root, mallory) as links. ``E2E_TOKEN`` is a fixed device token of
alice's, so the browser test can stream a session as her.

Publishing goes to a fake GitHub (``tests.fake_github``) on ``port + 2``. Each start seeds a
finished session titled ``E2E publish me`` for root (an admin, so it may be published), with
its note in the outbox, and one titled ``E2E work items`` with a short transcript and three
work items to review. Dispatch runs against an in-memory cluster whose runs open a PR at
once. Test doubles only, never for real deployments.
"""

import argparse
import asyncio
import os
import sys
import tempfile
import threading
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from sqlalchemy import select

from jarvis_live.app import create_app
from jarvis_live.auth import hash_token
from jarvis_live.config import Settings
from jarvis_live.copilot.schema import (
    ActionUpsert,
    CopilotDelta,
    FinalAction,
    FinalNote,
    NoteUpsert,
    SuggestionIn,
)
from jarvis_live.db.models import CopilotItem, Device, SegmentRow, User, WorkItem
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.session import make_engine, make_sessionmaker, run_migrations
from jarvis_live.users import create_user
from tests.audio import SR, TWO_UTTERANCES
from tests.fake_github import TOKEN as GITHUB_TOKEN
from tests.fake_github import FakeGitHub
from tests.fake_kube import FakeKube, FakeTokens
from tests.fake_oidc import CLIENT_ID, CLIENT_SECRET, DEFAULT_USERS, FakeIssuer
from tests.fakes import FakeLLM, FakeNotifier


class InstantKube(FakeKube):
    """Every run finishes at once with a draft PR: the browser test sees the whole cycle."""

    async def get_job(self, name: str) -> dict[str, Any] | None:
        job = self.jobs.get(name)
        if job is not None and "succeeded" not in job["status"]:
            self.report(
                name,
                outcome="pr_opened",
                pr_url="https://github.com/lordmuffin/jarvis-voice/pull/999",
                pr_number=999,
                summary="E2E: the change is ready for review.",
            )
        return self.jobs.get(name)


E2E_TOKEN = "e2e-device-token"  # noqa: S105
E2E_USER = "alice"
PUBLISH_TITLE = "E2E publish me"
WORK_TITLE = "E2E work items"
WORK_REPOS = "lordmuffin/jarvis-voice  Jarvis Live\nlordmuffin/np-home-homelab  homelab GitOps"
TRANSCRIPT = [
    (21_000, 30_000, "Gotify pushes vanish when Gotify restarts; retry them with backoff."),
    (65_000, 72_000, "A command line tool to export a transcript as SRT subtitles."),
    (1_263_000, 1_270_000, "And log it when the Gotify retries finally give up."),
]
REPO = Path(__file__).resolve().parents[2]


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(TWO_UTTERANCES)


@contextmanager
def database_url() -> Iterator[str]:
    if url := os.environ.get("JARVIS_LIVE_TEST_DATABASE_URL"):
        run_migrations(url)
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    image = os.environ.get("JARVIS_LIVE_TEST_POSTGRES_IMAGE", "postgres:16-alpine")
    with PostgresContainer(image, driver="asyncpg") as pg:
        url = pg.get_connection_url()
        run_migrations(url)
        yield url


async def ensure_device(url: str) -> None:
    """alice (as the fake issuer will sign her in) and her fixed device token."""
    engine = make_engine(url)
    sm = make_sessionmaker(engine)
    try:
        async with sm() as db:
            alice = (
                await db.execute(select(User).where(User.oidc_sub == E2E_USER))
            ).scalar_one_or_none()
        if alice is None:
            claims = DEFAULT_USERS[E2E_USER]
            alice = await create_user(sm, claims["name"], email=claims["email"], oidc_sub=E2E_USER)
        async with sm() as db:
            found = (
                await db.execute(select(Device).where(Device.token_hash == hash_token(E2E_TOKEN)))
            ).scalar_one_or_none()
            if found is None:
                db.add(Device(name="e2e", token_hash=hash_token(E2E_TOKEN), user_id=alice.id))
            else:
                found.user_id, found.revoked_at = alice.id, None
            await db.commit()
    finally:
        await engine.dispose()


async def seed_publishable(url: str, outbox: Path) -> None:
    """A finished session of root's with a note, ready to publish, and one with work items."""
    engine = make_engine(url)
    sm = make_sessionmaker(engine)
    try:
        async with sm() as db:
            root = (
                await db.execute(select(User).where(User.oidc_sub == "root"))
            ).scalar_one_or_none()
        if root is None:
            claims = DEFAULT_USERS["root"]
            root = await create_user(
                sm, claims["name"], email=claims["email"], oidc_sub="root", is_admin=True
            )
        async with sm() as db:
            device = Device(
                name="e2e-root", token_hash=hash_token(os.urandom(16).hex()), user_id=root.id
            )
            db.add(device)
            await db.flush()
            now = datetime.now(UTC)
            row = SessionRow(
                device_id=device.id,
                user_id=root.id,
                title=PUBLISH_TITLE,
                mode="meeting",
                status="done",
                channels=["mic"],
                started_at=now,
                ended_at=now,
            )
            db.add(row)
            await db.flush()
            name = f"{now:%Y-%m-%d %H%M} - E2E publish me {row.id.hex[:6]}.md"
            rel = f"{root.notes_subdir}/{name}" if root.notes_subdir else name
            (outbox / rel).parent.mkdir(parents=True, exist_ok=True)
            (outbox / rel).write_text(
                f"---\nsession_id: {row.id}\ntype: ai-output\n---\n\n"
                f"# {PUBLISH_TITLE}\n\n## Summary\n\nReady for the vault.\n"
            )
            db.add(
                CopilotItem(
                    session_id=row.id,
                    kind="final_note",
                    payload={"path": rel, "title": PUBLISH_TITLE},
                )
            )
            # A second finished session of root's, holding the work items to review.
            work = SessionRow(
                device_id=device.id,
                user_id=root.id,
                title=WORK_TITLE,
                mode="solo",
                status="done",
                channels=["mic"],
                started_at=now,
                ended_at=now,
            )
            db.add(work)
            await db.flush()
            segs = []
            for start, end, text in TRANSCRIPT:
                seg = SegmentRow(
                    session_id=work.id,
                    channel="mic",
                    speaker="me",
                    start_ms=start,
                    end_ms=end,
                    text=text,
                    source="final",
                    stt_tier="fake",
                )
                db.add(seg)
                segs.append(seg)
            await db.flush()
            src = [
                {
                    "segment_id": str(g.id),
                    "start_ms": g.start_ms,
                    "end_ms": g.end_ms,
                    "text": g.text,
                }
                for g in segs
            ]
            for ref, title, repo, criteria, sources in (
                ("w1", "Retry failed Gotify notifications", "lordmuffin/jarvis-voice",
                 ["A failed push is retried with backoff"], [src[0]]),
                ("w2", "Export transcripts as SRT", None, [], [src[1]]),
                ("w3", "Log when Gotify retries give up", "lordmuffin/jarvis-voice",
                 ["Giving up is logged"], [src[2]]),
            ):  # fmt: skip
                db.add(
                    WorkItem(
                        session_id=work.id,
                        user_id=root.id,
                        ref=ref,
                        title=title,
                        repo=repo,
                        intent=f"{title}, as said in the session.",
                        acceptance_criteria=criteria,
                        sources=sources,
                    )
                )
            await db.commit()
    finally:
        await engine.dispose()


def start_github(port: int) -> None:
    """The fake GitHub API, in a daemon thread."""
    fake = FakeGitHub(f"http://127.0.0.1:{port}")
    server = uvicorn.Server(
        uvicorn.Config(fake.app(), host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()


def start_issuer(port: int) -> None:
    """The fake Pocket-ID, in a daemon thread."""
    fake = FakeIssuer(f"http://127.0.0.1:{port}")
    server = uvicorn.Server(
        uvicorn.Config(fake.app(), host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()


def serve(port: int, web_dist: Path) -> int:
    issuer_port, github_port = port + 1, port + 2
    from tests.conftest import FakeSTT

    llm = (
        FakeLLM()
        .queue(
            "CopilotDelta",
            CopilotDelta(
                notes_upsert=[NoteUpsert(text="Kickoff covered")],
                actions_upsert=[ActionUpsert(text="Send the deck", owner="sam")],
                decisions_upsert=[NoteUpsert(text="Ship on Friday")],
                suggestions=[SuggestionIn(kind="question", text="Who owns QA?", ttl_s=3600)],
                title="Standup kickoff",
            ),
        )
        .queue(
            "FinalNote",
            FinalNote(
                title="E2E standup",
                summary="The team kicked off and agreed to ship on Friday.",
                decisions=["Ship on Friday"],
                actions=[FinalAction(text="Send the deck", owner="sam")],
            ),
        )
    )
    with database_url() as url:
        asyncio.run(ensure_device(url))
        state = Path(tempfile.mkdtemp(prefix="jarvis-e2e-"))
        asyncio.run(seed_publishable(url, state / "outbox"))
        secret = state / "oidc-secret"
        secret.write_text(CLIENT_SECRET)
        github_token = state / "github-token"
        github_token.write_text(GITHUB_TOKEN)
        start_issuer(issuer_port)
        start_github(github_port)
        settings = Settings(
            database_url=url,
            data_dir=state / "data",
            outbox_dir=state / "outbox",
            web_dist_dir=web_dist,
            finalize_recover_max_age_s=0,  # the shared test DB may hold other sessions
            copilot_min_segments=1,
            copilot_poll_s=0.1,
            status_interval_s=0.5,
            public_base_url=f"http://127.0.0.1:{port}",
            oidc_issuer=f"http://127.0.0.1:{issuer_port}",
            oidc_client_id=CLIENT_ID,
            oidc_client_secret_file=secret,
            oidc_admin_group="jarvis-admins",
            publish_repo="lordmuffin/Notes",
            github_api=f"http://127.0.0.1:{github_port}",
            github_token_file=github_token,
            publish_reconcile_interval_s=0,
            work_items_enabled=True,
            work_repos=WORK_REPOS,
            dispatch_enabled=True,
            git_sha="e2e0000",
            dispatch_poll_s=0.2,
        )
        app = create_app(
            settings,
            stt=FakeSTT(),
            llm=llm,
            notifier=FakeNotifier(),
            kube=InstantKube(),
            app_tokens=FakeTokens(),
        )
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e2e_server")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--web-dist", type=Path, default=REPO / "web" / "dist")
    p = sub.add_parser("wav")
    p.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "wav":
        write_wav(args.out)
        return 0
    return serve(args.port, args.web_dist.resolve())


if __name__ == "__main__":
    sys.exit(main())
