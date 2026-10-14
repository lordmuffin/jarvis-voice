import pytest

from jarvis_live.config import Settings


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ],
)
def test_database_url_defaults_to_the_asyncpg_driver(given: str, expected: str) -> None:
    assert Settings(database_url=given).database_url == expected


def test_database_url_is_normalised_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LIVE_DATABASE_URL", "postgresql://u:p@h/d")
    assert Settings().database_url == "postgresql+asyncpg://u:p@h/d"


def test_auto_migrate_is_off_by_default() -> None:
    assert Settings().auto_migrate is False


def test_publishing_is_off_until_repo_and_token_are_set(tmp_path: object) -> None:
    from pathlib import Path

    assert Settings().publish_enabled is False
    assert Settings(publish_repo="o/r").publish_enabled is False
    assert Settings(publish_repo="o/r", github_token_file=Path("/t")).publish_enabled is True


@pytest.mark.parametrize("bad", ["../x", "a/../b", "./a"])
def test_publish_subdir_stays_in_the_repo(bad: str) -> None:
    with pytest.raises(ValueError, match="inside"):
        Settings(publish_subdir=bad)


def test_notes_publish_to_the_vault_ai_output_inbox() -> None:
    assert Settings().publish_subdir == "00 Inbox/AI Output"


def test_publish_subdir_and_repo_are_normalised() -> None:
    s = Settings(publish_subdir="/00 Inbox/AI Output/", publish_repo=" o/r ")
    assert s.publish_subdir == "00 Inbox/AI Output"
    assert s.publish_repo == "o/r"
    with pytest.raises(ValueError, match="owner/name"):
        Settings(publish_repo="just-a-name")


def test_environment_defaults_to_live_with_no_title_prefix() -> None:
    assert Settings().environment == "live"
    assert Settings().notify_title_prefix == "[LIVE] "


def test_non_live_environment_prefixes_notification_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LIVE_ENVIRONMENT", " Lab ")
    s = Settings()
    assert s.environment == "lab"
    assert s.notify_title_prefix == "[LAB] "


def test_environment_must_not_be_empty() -> None:
    with pytest.raises(ValueError):
        Settings(environment=" ")


def test_work_repo_allowlist() -> None:
    cfg = Settings(work_repos="# targets\nlordmuffin/jarvis-voice\tthe app\n\nlordmuffin/notes\n")
    assert cfg.work_repo_allowlist == {"lordmuffin/jarvis-voice": "the app", "lordmuffin/notes": ""}
    with pytest.raises(ValueError, match="owner/name"):
        Settings(work_repos="jarvis-voice  no owner")


def test_dispatch_repos_default_to_the_work_repos_and_may_narrow_them() -> None:
    work = "lordmuffin/jarvis-voice  app\nlordmuffin/np-home-homelab  homelab"
    assert set(Settings(work_repos=work).dispatch_repo_allowlist) == {
        "lordmuffin/jarvis-voice",
        "lordmuffin/np-home-homelab",
    }
    lab = Settings(work_repos=work, dispatch_repos="lordmuffin/jarvis-dispatch-sandbox  lab only")
    assert lab.dispatch_repo_allowlist == {"lordmuffin/jarvis-dispatch-sandbox": "lab only"}
    with pytest.raises(ValueError, match="owner/name"):
        Settings(dispatch_repos="no-owner")


def test_dispatch_image_follows_the_server_commit_unless_set() -> None:
    assert Settings(git_sha="").dispatch_runner_image == ""
    assert Settings(git_sha="0123456789abcdef").dispatch_runner_image == (
        "ghcr.io/lordmuffin/jarvis-dispatch-runner:sha-0123456"
    )
    pinned = Settings(git_sha="0123456789", dispatch_image="ghcr.io/x/runner:1.2.3")
    assert pinned.dispatch_runner_image == "ghcr.io/x/runner:1.2.3"
