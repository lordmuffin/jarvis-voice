"""Installing the on-device speech models: pinned checksums, safe extraction, resumable state."""

import hashlib
import io
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from jarvis_desktop.stt import models
from jarvis_desktop.stt.models import ModelError, ModelSpec, install, is_installed


def tarball(members: dict[str, bytes], extra: list[tarfile.TarInfo] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info)
    return buf.getvalue()


def spec_for(payload: bytes, *, archive: bool, files: tuple[str, ...]) -> ModelSpec:
    return ModelSpec(
        name="test-model",
        url="https://example.invalid/test-model" + (".tar.bz2" if archive else ".onnx"),
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        archive=archive,
        files=files,
    )


class Fetcher:
    """Serves one payload in small chunks and counts how often it was asked."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self, url: str) -> Iterator[bytes]:
        self.calls += 1
        for i in range(0, len(self.payload), 1000):
            yield self.payload[i : i + 1000]


ARCHIVE = tarball({"pkg/encoder.onnx": b"E" * 5000, "pkg/tokens.txt": b"a 0\nb 1\n"})


def test_installs_an_archive_and_reports_progress(tmp_path: Path) -> None:
    spec = spec_for(ARCHIVE, archive=True, files=("encoder.onnx", "tokens.txt"))
    progress: list[float] = []
    path = install(spec, tmp_path, fetch=Fetcher(ARCHIVE), progress=progress.append)
    assert path == tmp_path / "test-model"
    assert (path / "encoder.onnx").read_bytes() == b"E" * 5000
    assert (path / "tokens.txt").exists()
    assert is_installed(spec, tmp_path)
    assert progress == sorted(progress) and progress[-1] == 1.0


def test_installs_a_single_file(tmp_path: Path) -> None:
    payload = b"onnx-bytes" * 100
    spec = spec_for(payload, archive=False, files=("test-model.onnx",))
    path = install(spec, tmp_path, fetch=Fetcher(payload))
    assert (path / "test-model.onnx").read_bytes() == payload


def test_an_installed_model_is_not_downloaded_again(tmp_path: Path) -> None:
    spec = spec_for(ARCHIVE, archive=True, files=("encoder.onnx", "tokens.txt"))
    fetch = Fetcher(ARCHIVE)
    install(spec, tmp_path, fetch=fetch)
    install(spec, tmp_path, fetch=fetch)
    assert fetch.calls == 1


def test_a_checksum_mismatch_installs_nothing(tmp_path: Path) -> None:
    spec = spec_for(ARCHIVE, archive=True, files=("encoder.onnx", "tokens.txt"))
    tampered = ARCHIVE[:-1] + bytes([ARCHIVE[-1] ^ 0xFF])
    with pytest.raises(ModelError, match="checksum"):
        install(spec, tmp_path, fetch=Fetcher(tampered))
    assert not is_installed(spec, tmp_path)
    assert list(tmp_path.iterdir()) == [], "no partial download or directory is left behind"


def test_a_missing_expected_file_installs_nothing(tmp_path: Path) -> None:
    spec = spec_for(ARCHIVE, archive=True, files=("encoder.onnx", "decoder.onnx"))
    with pytest.raises(ModelError, match="decoder.onnx"):
        install(spec, tmp_path, fetch=Fetcher(ARCHIVE))
    assert not is_installed(spec, tmp_path)


@pytest.mark.parametrize(
    "evil",
    [
        tarfile.TarInfo("../escape.txt"),
        tarfile.TarInfo("/etc/evil"),
        tarfile.TarInfo("pkg/link.onnx"),
    ],
)
def test_archive_members_cannot_escape_or_link(tmp_path: Path, evil: tarfile.TarInfo) -> None:
    if evil.name.endswith("link.onnx"):
        evil.type = tarfile.SYMTYPE
        evil.linkname = "/etc/passwd"
    payload = tarball({"pkg/encoder.onnx": b"E"}, extra=[evil])
    spec = spec_for(payload, archive=True, files=("encoder.onnx",))
    with pytest.raises(ModelError):
        install(spec, tmp_path / "models", fetch=Fetcher(payload))
    assert not (tmp_path / "escape.txt").exists()


def test_a_changed_pin_means_not_installed(tmp_path: Path) -> None:
    spec = spec_for(ARCHIVE, archive=True, files=("encoder.onnx", "tokens.txt"))
    install(spec, tmp_path, fetch=Fetcher(ARCHIVE))
    newer = ModelSpec(**{**spec.__dict__, "sha256": "0" * 64})
    assert not is_installed(newer, tmp_path)


def test_default_root_is_under_xdg_data_home(tmp_path: Path) -> None:
    root = models.models_dir({"XDG_DATA_HOME": str(tmp_path), "HOME": "/nowhere"})
    assert root == tmp_path / "jarvis-voice" / "models"


def test_the_shipped_pins_are_sha256_and_https() -> None:
    for spec in (models.PARAKEET_TDT_V2, models.SILERO_VAD):
        assert len(spec.sha256) == 64 and int(spec.sha256, 16) >= 0
        assert spec.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
