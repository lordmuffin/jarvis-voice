from fastapi.testclient import TestClient

from jarvis_live.app import app

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz() -> None:
    assert client.get("/readyz").status_code == 200
