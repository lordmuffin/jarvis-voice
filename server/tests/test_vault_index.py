import os
import sqlite3
from pathlib import Path, PurePosixPath

import pytest

from jarvis_live.vault.index import (
    NullVault,
    VaultIndex,
    is_excluded,
    obsidian_uri,
    parse_note,
)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write(
        root,
        "20 Areas/Acme Renewal.md",
        "# Acme renewal\n\nContract renewal pricing and SLA terms for Acme.\n",
    )
    _write(root, "20 Areas/Garden.md", "# Garden\n\n## Tomatoes\n\nPlant tomatoes in May.\n")
    _write(root, "Personal Vault/Secret.md", "# Secret\n\nAcme renewal diary entry.\n")
    _write(root, "40 Archives/Old.md", "# Old\n\nAcme renewal from 2019.\n")
    _write(root, ".obsidian/workspace.md", "Acme renewal config\n")
    _write(root, "_Pending Deletion 2026/Gone.md", "# Gone\n\nAcme renewal draft.\n")
    _write(root, "_Pending Deletion/Gone2.md", "# Gone2\n\nAcme renewal draft.\n")
    _write(root, "Work/readme.txt", "Acme renewal not markdown\n")
    return root


def _index(vault: Path, tmp_path: Path, **kw: object) -> VaultIndex:
    idx = VaultIndex(vault, tmp_path / "idx", **kw)  # type: ignore[arg-type]
    idx.refresh()
    return idx


def test_exclusions_honoured(vault: Path, tmp_path: Path) -> None:
    _write(vault, "Work/Private/Hush.md", "# Hush\n\nAcme renewal rumours.\n")
    idx = _index(vault, tmp_path, exclude_globs=["Work/Private*"])
    hits = idx.search_sync(["acme renewal"], set(), 10)
    assert [h.path for h in hits] == ["20 Areas/Acme Renewal.md"]


def test_is_excluded_rules() -> None:
    p = PurePosixPath
    assert is_excluded(p("Personal Vault/x.md"))
    assert is_excluded(p("a/40 Archives/x.md"))
    assert is_excluded(p("_Pending Deletion 2026-01/x.md"))
    assert is_excluded(p("x/y.md"), ["x/*"])
    assert is_excluded(p("Templates/y.md"), ["Templates"])
    assert not is_excluded(p("Work/y.md"), ["Templates"])


def test_symlinks_are_not_followed(vault: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside, "Leak.md", "# Leak\n\nAcme renewal secrets\n")
    os.symlink(outside, vault / "linked")
    os.symlink(outside / "Leak.md", vault / "20 Areas" / "LeakLink.md")
    idx = _index(vault, tmp_path)
    assert "Leak" not in {h.title for h in idx.search_sync(["acme renewal secrets"], set(), 10)}


def test_obsidian_uri_encoding() -> None:
    assert (
        obsidian_uri("Notes", "20 Areas/Q3 & Plans/Acme #1.md")
        == "obsidian://open?vault=Notes&file=20%20Areas%2FQ3%20%26%20Plans%2FAcme%20%231.md"
    )
    assert obsidian_uri("My Vault", "a.md").startswith("obsidian://open?vault=My%20Vault&")


def test_search_result_shape_and_distinct_notes(vault: Path, tmp_path: Path) -> None:
    _write(
        vault,
        "Meetings/Acme call.md",
        "# Acme call\n\n## Pricing\n\nrenewal pricing\n\n## SLA\n\nrenewal SLA\n",
    )
    idx = _index(vault, tmp_path, vault_name="Notes")
    hits = idx.search_sync(["Acme renewal pricing"], set(), 3)
    assert len({h.path for h in hits}) == len(hits) == 2
    top = next(h for h in hits if h.path == "20 Areas/Acme Renewal.md")
    assert top.title == "Acme renewal"
    assert top.uri == "obsidian://open?vault=Notes&file=20%20Areas%2FAcme%20Renewal.md"
    assert "renewal" in top.snippet.lower()


def test_exclude_already_shown_and_limit(vault: Path, tmp_path: Path) -> None:
    for i in range(5):
        _write(vault, f"Extra/n{i}.md", f"# N{i}\n\nplanning roadmap item {i}\n")
    idx = _index(vault, tmp_path)
    first = idx.search_sync(["planning roadmap"], set(), 3)
    assert len(first) == 3
    second = idx.search_sync(["planning roadmap"], {h.path for h in first}, 3)
    assert len(second) == 2 and not {h.path for h in first} & {h.path for h in second}


def test_incremental_refresh_by_mtime(vault: Path, tmp_path: Path) -> None:
    idx = _index(vault, tmp_path)
    assert idx.refresh() == (0, 0)
    f = vault / "20 Areas" / "Garden.md"
    f.write_text("# Garden\n\nNow about zucchini.\n")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 5_000_000_000))
    (vault / "20 Areas" / "Acme Renewal.md").unlink()
    assert idx.refresh() == (1, 1)
    assert idx.search_sync(["zucchini"], set(), 3)[0].path == "20 Areas/Garden.md"
    assert not idx.search_sync(["tomatoes"], set(), 3)
    assert not idx.search_sync(["acme renewal"], set(), 3)


def test_resolve_name(vault: Path, tmp_path: Path) -> None:
    idx = _index(vault, tmp_path)
    assert idx.resolve_name_sync("acme renewal") == "Acme Renewal"
    assert idx.resolve_name_sync("Garden.md") == "Garden"
    assert idx.resolve_name_sync("Nope") is None
    assert idx.resolve_name_sync("Secret") is None


def test_chunking_by_heading_and_size() -> None:
    long = "word " * 400
    title, chunks = parse_note(
        f"---\ntags: [x]\n---\n# Title\n\nintro\n\n## A\n\n{long}\n\n```\n# not a heading\n```\n",
        "fb",
    )
    assert title == "Title"
    assert chunks[0].heading == "Title" and chunks[0].text == "intro"
    a = [c for c in chunks if c.heading == "A"]
    assert len(a) >= 2 and all(len(c.text) <= 800 for c in a)
    assert not any(c.heading == "not a heading" for c in chunks)


def test_title_falls_back_to_filename() -> None:
    assert parse_note("just text\n", "Stem")[0] == "Stem"


# --- passages: the chunks a held ask reads (docs/plans/voice-ask-vault.md) -------------------


async def test_passages_are_the_best_chunk_of_each_note(vault: Path, tmp_path: Path) -> None:
    _write(
        vault,
        "Meetings/Backup review.md",
        "# Backup review\n\n## Schedule\n\nNightly backup schedule moved to 02:00.\n\n"
        "## Retention\n\nBackup retention stays at thirty days.\n",
    )
    _write(vault, "20 Areas/Lunch.md", "# Lunch\n\nSandwiches on Friday.\n")
    idx = _index(vault, tmp_path)
    (top, *rest) = await idx.passages("what is the backup schedule?", 3, 1500)
    assert (top.path, top.title, top.heading) == (
        "Meetings/Backup review.md",
        "Backup review",
        "Schedule",
    )
    assert top.text == "Nightly backup schedule moved to 02:00."
    assert "Meetings/Backup review.md" not in {p.path for p in rest}  # one passage per note
    assert "20 Areas/Lunch.md" not in {p.path for p in rest}


def test_passages_honour_the_exclusions(vault: Path, tmp_path: Path) -> None:
    _write(vault, "Work/Private/Hush.md", "# Hush\n\nAcme renewal rumours.\n")
    idx = _index(vault, tmp_path, exclude_globs=["Work/Private*"])
    hits = idx.passages_sync("acme renewal", 10, 5000)
    assert [p.path for p in hits] == ["20 Areas/Acme Renewal.md"]


def test_passages_keep_within_the_limit_and_max_chars(vault: Path, tmp_path: Path) -> None:
    for i in range(6):
        body = " ".join(["roadmap planning detail"] * 40)
        _write(vault, f"Extra/n{i}.md", f"# N{i}\n\n{body} {i}\n")
    idx = _index(vault, tmp_path)
    assert len(idx.passages_sync("roadmap planning", 3, 10_000)) == 3
    capped = idx.passages_sync("roadmap planning", 6, 700)
    assert capped and sum(len(p.text) for p in capped) <= 700
    assert all(p.text.endswith("…") for p in capped)


def test_no_words_no_passages(vault: Path, tmp_path: Path) -> None:
    idx = _index(vault, tmp_path)
    assert idx.passages_sync("is it?", 3, 1500) == []


async def test_a_broken_index_gives_no_passages(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    idx = _index(vault, tmp_path)

    def broken() -> sqlite3.Connection:
        raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setattr(idx, "_connect", broken)
    assert await idx.passages("acme renewal", 3, 1500) == []


async def test_the_null_vault_has_no_passages() -> None:
    assert await NullVault().passages("acme renewal", 3, 1500) == []
