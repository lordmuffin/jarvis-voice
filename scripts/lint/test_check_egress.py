"""Tests for the network-egress check (scripts/lint/check_egress.py)."""

from pathlib import Path

import check_egress as chk


def scan(tmp_path: Path, rel: str, text: str) -> list[str]:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return [v.rule for v in chk.check_file(f, tmp_path)]


SWIFT_APP = "macos/JarvisVoiceMac/Sources/App/Thing.swift"
KOTLIN_APP = "android/app/src/main/java/x/Thing.kt"


def test_new_swift_url_session_is_flagged(tmp_path):
    assert scan(tmp_path, SWIFT_APP, "let s = URLSession(configuration: .ephemeral)") == ["url-session"]


def test_shared_session_and_raw_connections_are_flagged(tmp_path):
    text = "let a = URLSession.shared\nlet c = NWConnection(host: h, port: p, using: .tcp)\n"
    assert scan(tmp_path, SWIFT_APP, text) == ["url-session", "raw-connection"]


def test_the_gated_transport_files_may_open_connections(tmp_path):
    kit = "macos/JarvisLiveKit/Sources/JarvisLiveKit/Transport/LiveConnection.swift"
    assert scan(tmp_path, kit, "URLSession(configuration: .ephemeral)") == []


def test_tests_are_not_checked(tmp_path):
    assert scan(tmp_path, "macos/JarvisLiveKit/Tests/X/StubTests.swift", "URLSession.shared") == []


def test_new_kotlin_http_client_is_flagged(tmp_path):
    text = "val c = OkHttpClient.Builder().build()\nval u = url.openConnection()\n"
    assert scan(tmp_path, KOTLIN_APP, text) == ["http-client", "http-client"]


def test_known_ungoverned_android_files_pass(tmp_path):
    ok = "android/app/src/main/java/com/lordmuffin/jarvisvoice/chat/LlmRepository.kt"
    assert scan(tmp_path, ok, "OkHttpClient.Builder()") == []


def test_an_inline_reason_allows_one_line(tmp_path):
    text = "val s = URLSession.shared // egress-lint:ignore speech model install, no user data"
    assert scan(tmp_path, SWIFT_APP, text) == []


def test_the_real_tree_is_clean():
    root = Path(__file__).resolve().parents[2]
    assert chk.check_tree(root) == []
