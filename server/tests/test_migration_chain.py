"""The migrations form one line. The server upgrades to ``head`` at startup, so a second head
(two PRs that each took the next number) stops it from starting at all."""

from pathlib import Path

from alembic.script import ScriptDirectory

from jarvis_live.db.session import alembic_config


def test_there_is_one_head() -> None:
    script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused"))
    assert len(script.get_heads()) == 1


def test_no_two_files_claim_the_same_revision() -> None:
    versions = Path(ScriptDirectory.from_config(alembic_config("x://")).versions)
    numbers = [f.name.split("_")[0] for f in versions.glob("*.py")]
    assert len(numbers) == len(set(numbers))
