"""Local-only is enforced at the transport layer: only transport.py may reach the network."""

import ast
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[2] / "src" / "jarvis_live" / "client"
NETWORK = {
    "httpx",
    "websockets",
    "socket",
    "ssl",
    "urllib",
    "http",
    "requests",
    "aiohttp",
    "smtplib",
}
ALLOWED = {"transport.py"}


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def test_only_the_transport_module_imports_network_libraries() -> None:
    offenders = {
        p.name: sorted(_imports(p) & NETWORK)
        for p in CLIENT.glob("*.py")
        if p.name not in ALLOWED and _imports(p) & NETWORK
    }
    assert offenders == {}


def test_the_guard_can_fail() -> None:
    assert _imports(CLIENT / "transport.py") & NETWORK, "guard would pass vacuously"
