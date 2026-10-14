import importlib.metadata

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
        "audio_retention_days": 90,
    }


def test_healthz_names_the_commit_the_image_was_built_from() -> None:
    built = TestClient(create_app(Settings(git_sha="0123456789abcdef0123456789abcdef01234567\n")))
    assert built.get("/healthz").json()["commit"] == "0123456"


def test_healthz_names_a_non_live_channel() -> None:
    lab = TestClient(create_app(Settings(environment="lab")))
    assert lab.get("/healthz").json()["environment"] == "lab"


def test_healthz_says_how_long_audio_is_kept() -> None:
    short = TestClient(create_app(Settings(audio_retention_days=30)))
    assert short.get("/healthz").json()["audio_retention_days"] == 30


def test_readyz() -> None:
    assert client.get("/readyz").status_code == 200
