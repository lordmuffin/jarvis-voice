import importlib.metadata
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from jarvis_live.app import app, create_app
from jarvis_live.config import Settings

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "environment": "live",
        "version": importlib.metadata.version("jarvis-live"),
        "commit": "",
    }


def test_healthz_names_the_commit_the_image_was_built_from() -> None:
    built = TestClient(create_app(Settings(git_sha="0123456789abcdef0123456789abcdef01234567\n")))
    assert built.get("/healthz").json()["commit"] == "0123456"


def test_healthz_names_a_non_live_channel() -> None:
    lab = TestClient(create_app(Settings(environment="lab")))
    assert lab.get("/healthz").json()["environment"] == "lab"


def test_readyz() -> None:
    assert client.get("/readyz").status_code == 200


def _backup_dir_with(tmp_path: Path, age_s: float) -> Path:
    when = datetime.now(UTC) - timedelta(seconds=age_s)
    (tmp_path / "last_success.json").write_text(json.dumps({"finished_at": when.isoformat()}))
    return tmp_path


def test_healthz_says_nothing_of_backups_where_none_are_configured() -> None:
    assert "backup" not in client.get("/healthz").json()


def test_healthz_reports_the_age_of_the_last_backup(tmp_path: Path) -> None:
    c = TestClient(create_app(Settings(backup_dir=_backup_dir_with(tmp_path, 3600))))
    body = c.get("/healthz").json()
    assert body["status"] == "ok"
    assert 3590 < body["backup"]["age_s"] < 3700 and body["backup"]["stale"] is False


def test_a_stale_backup_shows_but_does_not_fail_liveness(tmp_path: Path) -> None:
    c = TestClient(
        create_app(
            Settings(backup_dir=_backup_dir_with(tmp_path, 200_000), backup_max_age_s=100_000)
        )
    )
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"  # a restart would not fix it
    assert r.json()["backup"]["stale"] is True


def test_backup_settings_default_to_off_and_a_day_and_a_half() -> None:
    s = Settings()
    assert s.backup_dir is None and s.backup_max_age_s == 129_600
