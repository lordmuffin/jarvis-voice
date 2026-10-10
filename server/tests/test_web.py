"""Static web dashboard: served at `/` with SPA fallback, behind the API routes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis_live.app import create_app, find_web_dist
from jarvis_live.config import Settings


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Jarvis Live</title>")
    (d / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    return d


@pytest.fixture
def client(dist: Path) -> TestClient:
    # No `with`: the lifespan (DB, STT) is not started; these routes need none of it.
    return TestClient(create_app(Settings(web_dist_dir=dist)))


def test_index_and_spa_fallback(client: TestClient) -> None:
    for path in ("/", "/sessions/3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b", "/anything/else"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "<title>Jarvis Live</title>" in r.text
        assert r.headers["cache-control"] == "no-cache"


def test_assets_are_served_immutable(client: TestClient) -> None:
    r = client.get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_missing_files_404_instead_of_index(client: TestClient) -> None:
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/favicon.ico").status_code == 404


def test_api_routes_win_and_unknown_api_paths_404(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/v1/nope")
    assert r.status_code == 404
    assert "<title>" not in r.text
    assert client.get("/v1/sessions").status_code == 401  # real route, not the SPA


def test_no_path_traversal(client: TestClient) -> None:
    r = client.get("/..%2fsecret.txt")
    assert "nope" not in r.text
    r = client.get("/assets/..%2f..%2fsecret.txt")
    assert "nope" not in r.text


def test_not_served_without_a_build(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_web_dist(Settings(web_dist_dir=empty)) is None
    c = TestClient(create_app(Settings(web_dist_dir=empty)))
    assert c.get("/").status_code == 404
