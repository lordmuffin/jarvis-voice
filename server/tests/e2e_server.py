"""Run the real app with FakeSTT/FakeLLM for the web dashboard's Playwright smoke test.

    python -m tests.e2e_server serve --port 8765      # blocks; prints nothing secret
    python -m tests.e2e_server wav out.wav            # the audio the smoke test replays

The database is ``JARVIS_LIVE_TEST_DATABASE_URL`` if set, else a Postgres testcontainer.
The device token is the fixed ``E2E_TOKEN`` so the browser test can sign in; this is a test
double, never for real deployments.
"""

import argparse
import asyncio
import os
import sys
import tempfile
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
from jarvis_live.db.models import Device
from jarvis_live.db.session import make_engine, make_sessionmaker, run_migrations
from tests.audio import SR, TWO_UTTERANCES
from tests.fakes import FakeLLM, FakeNotifier

E2E_TOKEN = "e2e-device-token"  # noqa: S105
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
    engine = make_engine(url)
    sm = make_sessionmaker(engine)
    try:
        async with sm() as db:
            found = (
                await db.execute(select(Device).where(Device.token_hash == hash_token(E2E_TOKEN)))
            ).scalar_one_or_none()
            if found is None:
                db.add(Device(name="e2e", token_hash=hash_token(E2E_TOKEN)))
                await db.commit()
    finally:
        await engine.dispose()


def serve(port: int, web_dist: Path) -> int:
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
        settings = Settings(
            database_url=url,
            data_dir=state / "data",
            outbox_dir=state / "outbox",
            web_dist_dir=web_dist,
            finalize_recover_max_age_s=0,  # the shared test DB may hold other sessions
            copilot_min_segments=1,
            copilot_poll_s=0.1,
            status_interval_s=0.5,
        )
        app = create_app(settings, stt=FakeSTT(), llm=llm, notifier=FakeNotifier())
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
