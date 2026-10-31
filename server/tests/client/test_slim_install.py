"""The Linux capture client installs without the server stack, so its modules must import
without it. Server-only packages are blocked in a subprocess to prove it."""

import subprocess
import sys
import textwrap

SERVER_ONLY = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "numpy",
    "webrtcvad",
    "pydantic_settings",
]


def _run(code: str) -> subprocess.CompletedProcess[str]:
    blocker = textwrap.dedent(
        f"""
        import sys
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {SERVER_ONLY!r}:
                    raise ImportError("server-only dependency: " + name)
        sys.meta_path.insert(0, _Block())
        """
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", blocker + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_imports_and_parses_without_server_dependencies() -> None:
    res = _run(
        """
        from jarvis_live import cli
        import argparse
        try:
            cli.main(["--help"])
        except SystemExit as e:
            assert e.code == 0
        """
    )
    assert res.returncode == 0, res.stderr


def test_protocol_and_replay_import_without_server_dependencies() -> None:
    res = _run("import jarvis_live.protocol, jarvis_live.replay")
    assert res.returncode == 0, res.stderr
