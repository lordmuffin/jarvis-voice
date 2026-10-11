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
    }


def test_healthz_names_a_non_live_channel() -> None:
    lab = TestClient(create_app(Settings(environment="lab")))
    assert lab.get("/healthz").json()["environment"] == "lab"


def test_readyz() -> None:
    assert client.get("/readyz").status_code == 200
