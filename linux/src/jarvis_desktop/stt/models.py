"""Installs the on-device speech models into ``$XDG_DATA_HOME/jarvis-voice/models/``.

Each model is pinned by URL and SHA-256. A download is hashed as it streams, and nothing is
installed unless the hash and every expected file match. Archives are extracted member by member,
regular files only, so a member cannot escape the model directory or plant a link. The installed
directory carries the hash it was installed from, so a new pin reinstalls. This download is the
only network access dictation makes, and it carries no user content."""

import contextlib
import hashlib
import os
import shutil
import tarfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

Fetch = Callable[[str], Iterator[bytes]]
MARKER = ".installed"
_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    sha256: str
    size: int
    archive: bool  # a .tar.bz2 with one top-level directory, or a single file
    files: tuple[str, ...]  # what must end up in the model directory


PARAKEET_TDT_V2 = ModelSpec(
    name="parakeet-tdt-0.6b-v2-int8",
    url=_RELEASES + "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
    sha256="157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad",
    size=482_468_385,
    archive=True,
    files=("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
)
#: The default engine's model (plan section 2.2): Whisper large-v3-turbo, q5_0 GGML, for
#: whisper.cpp on Vulkan. Pinned to a Hugging Face repo revision, not the moving ``main`` branch.
WHISPER_TURBO_Q5 = ModelSpec(
    name="whisper-large-v3-turbo-q5_0",
    url="https://huggingface.co/ggerganov/whisper.cpp/resolve/"
    "5359861c739e955e79d9a303bcbc70fb988958b1/ggml-large-v3-turbo-q5_0.bin",
    sha256="394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    size=574_041_195,
    archive=False,
    files=("ggml-large-v3-turbo-q5_0.bin",),
)
SILERO_VAD = ModelSpec(
    name="silero-vad",
    url=_RELEASES + "silero_vad.onnx",
    sha256="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    size=643_854,
    archive=False,
    files=("silero_vad.onnx",),
)

_PIPER_VOICES = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    "e7229f7d33a10e145e109b886d661984201ac3c4/en/en_US/lessac/medium/"
)
#: Piper's voice for spoken ask replies (speech.py), and the config Piper loads it with.
PIPER_LESSAC_MEDIUM = ModelSpec(
    name="piper-en_US-lessac-medium",
    url=_PIPER_VOICES + "en_US-lessac-medium.onnx",
    sha256="5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
    size=63_201_294,
    archive=False,
    files=("en_US-lessac-medium.onnx",),
)
PIPER_LESSAC_MEDIUM_CONFIG = ModelSpec(
    name="piper-en_US-lessac-medium-config",
    url=_PIPER_VOICES + "en_US-lessac-medium.onnx.json",
    sha256="efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
    size=4_885,
    archive=False,
    files=("en_US-lessac-medium.onnx.json",),
)

PIPER_VOICE = (PIPER_LESSAC_MEDIUM, PIPER_LESSAC_MEDIUM_CONFIG)

#: What ``jarvis-voice models install`` fetches: the default engine, its CPU fallback, the VAD,
#: and the voice that speaks ask replies.
ALL = (
    WHISPER_TURBO_Q5,
    PARAKEET_TDT_V2,
    SILERO_VAD,
    PIPER_LESSAC_MEDIUM,
    PIPER_LESSAC_MEDIUM_CONFIG,
)


def models_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_DATA_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local/share")
    return Path(base) / "jarvis-voice" / "models"


def is_installed(spec: ModelSpec, root: Path) -> bool:
    d = root / spec.name
    try:
        pinned = (d / MARKER).read_text().strip() == spec.sha256
    except OSError:
        return False
    return pinned and all((d / f).is_file() for f in spec.files)


def http_fetch(url: str) -> Iterator[bytes]:
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
        r.raise_for_status()
        yield from r.iter_bytes(1 << 20)


def install(
    spec: ModelSpec,
    root: Path,
    *,
    fetch: Fetch = http_fetch,
    progress: Callable[[float], None] = lambda _: None,
) -> Path:
    """Returns the model directory, downloading and installing the model first if needed."""
    target = root / spec.name
    if is_installed(spec, root):
        progress(1.0)
        return target
    root.mkdir(parents=True, exist_ok=True)
    part = root / f".{spec.name}.part"
    staging = root / f".{spec.name}.staging"
    try:
        _download(spec, fetch, part, progress)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        if spec.archive:
            _extract(part, staging, spec.files)
        else:
            part.replace(staging / spec.files[0])
        missing = [f for f in spec.files if not (staging / f).is_file()]
        if missing:
            raise ModelError(f"{spec.name}: the download has no {', '.join(missing)}")
        (staging / MARKER).write_text(spec.sha256 + "\n")
        shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            part.unlink()
        shutil.rmtree(staging, ignore_errors=True)
    progress(1.0)
    return target


def _download(spec: ModelSpec, fetch: Fetch, part: Path, progress: Callable[[float], None]) -> None:
    digest = hashlib.sha256()
    done = 0
    with part.open("wb") as fh:
        for chunk in fetch(spec.url):
            fh.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            progress(min(done / spec.size, 0.99) if spec.size else 0.0)
    if digest.hexdigest() != spec.sha256:
        raise ModelError(
            f"{spec.name}: checksum mismatch (got {digest.hexdigest()}, pinned {spec.sha256})"
        )


def _extract(archive: Path, dest: Path, wanted: tuple[str, ...]) -> None:
    """Regular files listed in ``wanted``, from under the archive's top-level directory."""
    with tarfile.open(archive, "r:bz2") as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ModelError(f"unsafe path in the archive: {member.name}")
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ModelError(f"the archive holds a link or special file: {member.name}")
            if not member.isfile() or len(path.parts) != 2 or path.name not in wanted:
                continue  # the top-level directory, test WAVs, anything else
            source = tar.extractfile(member)
            assert source is not None  # a regular file always has a payload
            with source, (dest / path.name).open("wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
