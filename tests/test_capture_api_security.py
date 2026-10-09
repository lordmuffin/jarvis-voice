import importlib

import pytest
from fastapi.testclient import TestClient

KEY = "test-key-123"


def load_app(monkeypatch, tmp_path, key=KEY, dangerous=None):
    vault = tmp_path / "vault"
    (vault / "00 Inbox" / "Voice Notes").mkdir(parents=True)
    (vault / "note.md").write_text("hello")
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("VAULT_INBOX", str(vault / "00 Inbox" / "Voice Notes"))
    for name, val in (("JARVIS_CAPTURE_KEY", key), ("JARVIS_ENABLE_DANGEROUS_TOOLS", dangerous)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    import jarvis_voice.vault as vault_mod
    import jarvis_voice.capture_api as api

    importlib.reload(vault_mod)
    api = importlib.reload(api)
    return api, TestClient(api.app)


def test_unset_key_returns_503(monkeypatch, tmp_path):
    _, c = load_app(monkeypatch, tmp_path, key=None)
    assert c.get("/api/v1/vault/note", params={"path": "note.md"}, headers={"X-Jarvis-Key": "x"}).status_code == 503
    assert c.get("/api/v1/vault/note", params={"path": "note.md"}).status_code == 503


def test_empty_key_returns_503(monkeypatch, tmp_path):
    _, c = load_app(monkeypatch, tmp_path, key="")
    assert c.get("/api/v1/vault/note", params={"path": "note.md"}, headers={"X-Jarvis-Key": ""}).status_code == 503


def test_wrong_key_401(monkeypatch, tmp_path):
    _, c = load_app(monkeypatch, tmp_path)
    assert c.get("/api/v1/vault/note", params={"path": "note.md"}, headers={"X-Jarvis-Key": "nope"}).status_code == 401
    assert c.get("/api/v1/vault/note", params={"path": "note.md"}).status_code == 401


def test_right_key_200(monkeypatch, tmp_path):
    _, c = load_app(monkeypatch, tmp_path)
    r = c.get("/api/v1/vault/note", params={"path": "note.md"}, headers={"X-Jarvis-Key": KEY})
    assert r.status_code == 200 and r.json()["content"] == "hello"


def test_health_open(monkeypatch, tmp_path):
    _, c = load_app(monkeypatch, tmp_path, key=None)
    assert c.get("/health").status_code == 200


DANGEROUS = [
    ("post", "/api/v1/system/exec", {"command": "echo hi"}),
    ("post", "/api/v1/git/clone", {"repo": "a/b"}),
    ("post", "/api/v1/git/write", {"repo": "a/b", "path": "x", "content": "y"}),
    ("get", "/api/v1/git/status?repo=a/b", None),
    ("post", "/api/v1/git/commit", {"repo": "a/b", "message": "m"}),
    ("post", "/api/v1/git/pr", {"repo": "a/b", "title": "t"}),
]


def _call(c, method, url, body):
    return getattr(c, method)(url, headers={"X-Jarvis-Key": KEY}, **({"json": body} if body is not None else {}))


@pytest.mark.parametrize("method,url,body", DANGEROUS)
def test_dangerous_routes_404_by_default(monkeypatch, tmp_path, method, url, body):
    api, c = load_app(monkeypatch, tmp_path)
    assert _call(c, method, url, body).status_code == 404
    names = {t["function"]["name"] for t in api._AGENT_TOOLS}
    assert not names & {"system_exec", "git_clone", "git_write", "git_status", "git_commit", "git_pr"}
    assert "vault_read" in names
    assert api._execute_tool("system_exec", {"command": "echo hi"}).startswith("Refused")
    assert api._execute_tool("git_status", {"repo": "a/b"}).startswith("Refused")


@pytest.mark.parametrize("method,url,body", DANGEROUS)
def test_dangerous_routes_present_with_flag(monkeypatch, tmp_path, method, url, body):
    api, c = load_app(monkeypatch, tmp_path, dangerous="true")
    registered = {(m.lower(), r.path) for r in api.app.routes for m in getattr(r, "methods", ())}
    assert (method, url.split("?")[0]) in registered
    assert any(t["function"]["name"] == "system_exec" for t in api._AGENT_TOOLS)


def test_system_exec_works_with_flag(monkeypatch, tmp_path):
    _, c = load_app(monkeypatch, tmp_path, dangerous="true")
    r = c.post("/api/v1/system/exec", json={"command": "echo hi"}, headers={"X-Jarvis-Key": KEY})
    assert r.status_code == 200 and "hi" in r.text


def test_vault_traversal_400(monkeypatch, tmp_path):
    _, c = load_app(monkeypatch, tmp_path)
    (tmp_path / "secret.md").write_text("secret")
    h = {"X-Jarvis-Key": KEY}
    assert c.get("/api/v1/vault/note", params={"path": "../secret.md"}, headers=h).status_code == 400
    assert c.get("/api/v1/vault/note", params={"path": "/etc/passwd"}, headers=h).status_code == 400
    assert c.get("/api/v1/vault/search", params={"query": "x", "directory": ".."}, headers=h).status_code == 400
    assert c.post("/api/v1/vault/note/write", json={"path": "../evil.md", "content": "x"}, headers=h).status_code == 400
    assert c.post("/api/v1/vault/note/append", json={"path": "../secret.md", "text": "x"}, headers=h).status_code == 400
    assert not (tmp_path / "evil.md").exists()
    assert (tmp_path / "secret.md").read_text() == "secret"


def test_vault_rglob_fallback_symlink_escape(monkeypatch, tmp_path):
    api, c = load_app(monkeypatch, tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (api.VAULT_ROOT / "link.md").symlink_to(outside)
    r = c.get("/api/v1/vault/note", params={"path": "missing/link.md"}, headers={"X-Jarvis-Key": KEY})
    assert r.status_code == 400


def test_execute_tool_vault_traversal_refused(monkeypatch, tmp_path):
    api, _ = load_app(monkeypatch, tmp_path)
    (tmp_path / "secret.md").write_text("secret")
    assert "Refused" in api._execute_tool("vault_read", {"path": "../secret.md"})
    assert "Refused" in api._execute_tool("vault_write", {"path": "../evil.md", "content": "x"})
    assert "Refused" in api._execute_tool("vault_append", {"path": "../secret.md", "content": "x"})
    assert not (tmp_path / "evil.md").exists()
    assert api._execute_tool("vault_read", {"path": "note.md"}) == "hello"
