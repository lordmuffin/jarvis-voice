"""SQLite FTS5 index over a read-only clone of the Obsidian vault.

Rebuildable from scratch at any time; the database lives on local disk (never NFS)."""

import asyncio
import fnmatch
import logging
import os
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote

from jarvis_live.protocol import Related

log = logging.getLogger(__name__)

EXCLUDED_DIRS = ("Personal Vault", "40 Archives", ".obsidian", ".git")
EXCLUDED_PREFIXES = ("_Pending Deletion",)
CHUNK_CHARS = 800
SNIPPET_CHARS = 240
MAX_FILE_BYTES = 1_000_000

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
_STOP = frozenset(
    "the and for are but not you all can had her was one our out has his how its may who did "
    "this that with from have they will what when your about would there their been were into "
    "than then them these those some very just also more most such only over here".split()
)


class VaultContext(Protocol):
    async def search(self, topics: list[str], exclude: set[str], limit: int) -> list[Related]: ...

    async def resolve_name(self, name: str) -> str | None:
        """Wikilink target (file stem) of the indexed note called ``name``, or None."""
        ...


class NullVault:
    async def search(self, topics: list[str], exclude: set[str], limit: int) -> list[Related]:
        return []

    async def resolve_name(self, name: str) -> str | None:
        return None


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str


def obsidian_uri(vault_name: str, path: str) -> str:
    return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(path, safe='')}"


def is_excluded(rel: PurePosixPath, globs: Iterable[str] = ()) -> bool:
    """True if any component of ``rel`` is an excluded folder or matches a configured glob."""
    parts = rel.parts
    for part in parts:
        if part in EXCLUDED_DIRS or part.startswith(EXCLUDED_PREFIXES):
            return True
    for g in globs:
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            if fnmatch.fnmatchcase(prefix, g) or fnmatch.fnmatchcase(prefix, g.rstrip("/")):
                return True
    return False


def parse_note(raw: str, fallback_title: str) -> tuple[str, list[Chunk]]:
    """Return ``(title, chunks)``. Frontmatter is dropped; sections split at headings and long
    sections at paragraph boundaries so chunks stay near ``CHUNK_CHARS``."""
    lines = raw.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                lines = lines[i + 1 :]
                break
    title = ""
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            if not title and len(m.group(1)) == 1:
                title = m.group(2)
            sections.append((m.group(2), []))
        else:
            sections[-1][1].append(line)
    chunks: list[Chunk] = []
    for heading, body in sections:
        for piece in _split_text("\n".join(body).strip(), CHUNK_CHARS):
            chunks.append(Chunk(heading, piece))
    return title or fallback_title, chunks


def _split_text(text: str, limit: int) -> Iterator[str]:
    if not text:
        return
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > limit:  # one huge paragraph: cut at whitespace
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if cur:
                yield cur
                cur = ""
            yield para[:cut].strip()
            para = para[cut:].strip()
        if cur and len(cur) + len(para) + 2 > limit:
            yield cur
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        yield cur


def _terms(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in _WORD.findall(text.lower()):
        if w not in _STOP:
            seen[w] = None
    return list(seen)


def _fts_queries(topics: list[str]) -> list[str]:
    """Strict query (all words of a topic, topics OR'd) then a loose one (any word)."""
    per_topic = [t for t in (_terms(topic) for topic in topics) if t]
    if not per_topic:
        return []
    strict = " OR ".join("(" + " AND ".join(f'"{w}"' for w in t) + ")" for t in per_topic)
    loose_terms = list(dict.fromkeys(w for t in per_topic for w in t))
    loose = " OR ".join(f'"{w}"' for w in loose_terms)
    return [strict] if strict == loose else [strict, loose]


def _snippet(heading: str, text: str) -> str:
    body = " ".join(text.split())
    if len(body) > SNIPPET_CHARS:
        body = body[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
    return f"{heading}: {body}" if heading else body


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL,
    heading TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    title, heading, body, content='chunks', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO fts(rowid, title, heading, body) VALUES (new.id, new.title, new.heading, new.body);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO fts(fts, rowid, title, heading, body)
    VALUES ('delete', old.id, old.title, old.heading, old.body);
END;
"""


class VaultIndex:
    def __init__(
        self,
        clone_dir: Path,
        index_dir: Path,
        *,
        vault_name: str = "Notes",
        exclude_globs: Iterable[str] = (),
    ) -> None:
        self.clone_dir = clone_dir
        self.db_path = index_dir / "vault.sqlite"
        self.vault_name = vault_name
        self._globs = list(exclude_globs)
        self._write_lock = threading.Lock()
        index_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # --- building ---------------------------------------------------------------------------

    def _scan(self) -> dict[str, int]:
        found: dict[str, int] = {}
        root = str(self.clone_dir)
        for dirpath, dirnames, filenames in os.walk(root):  # does not follow symlinked dirs
            rel_dir = PurePosixPath(Path(dirpath).relative_to(root).as_posix())
            dirnames[:] = [d for d in dirnames if not is_excluded(rel_dir / d, self._globs)]
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                rel = rel_dir / name
                full = Path(dirpath) / name
                if is_excluded(rel, self._globs) or full.is_symlink():
                    continue
                st = full.stat()
                if st.st_size <= MAX_FILE_BYTES:
                    found[str(rel)] = st.st_mtime_ns
        return found

    def refresh(self) -> tuple[int, int]:
        """Sync the index with the working tree by mtime. Returns ``(reindexed, removed)``."""
        found = self._scan()
        with self._write_lock, self._connect() as db:
            known = dict(db.execute("SELECT path, mtime_ns FROM files"))
            gone = [p for p in known if p not in found]
            changed = [p for p, m in found.items() if known.get(p) != m]
            for path in gone:
                self._drop(db, path)
            for path in changed:
                try:
                    raw = (self.clone_dir / path).read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    log.warning("cannot read %s: %s", path, e)
                    continue
                self._drop(db, path)
                title, chunks = parse_note(raw, PurePosixPath(path).stem)
                db.executemany(
                    "INSERT INTO chunks(path, title, heading, body) VALUES (?,?,?,?)",
                    [(path, title, c.heading, c.text) for c in chunks],
                )
                db.execute("INSERT INTO files VALUES (?,?)", (path, found[path]))
        if changed or gone:
            log.info("vault index: %d reindexed, %d removed", len(changed), len(gone))
        return len(changed), len(gone)

    @staticmethod
    def _drop(db: sqlite3.Connection, path: str) -> None:
        db.execute("DELETE FROM chunks WHERE path = ?", (path,))
        db.execute("DELETE FROM files WHERE path = ?", (path,))

    async def run_refresh_loop(self, interval_s: float) -> None:
        while True:
            try:
                await asyncio.to_thread(self.refresh)
            except Exception:
                log.exception("vault index refresh failed")
            await asyncio.sleep(interval_s)

    # --- querying ---------------------------------------------------------------------------

    def search_sync(self, topics: list[str], exclude: set[str], limit: int) -> list[Related]:
        hits: dict[str, Related] = {}
        with self._connect() as db:
            for q in _fts_queries(topics):
                rows = db.execute(
                    "SELECT c.path, c.title, c.heading, c.body FROM fts "
                    "JOIN chunks c ON c.id = fts.rowid WHERE fts MATCH ? "
                    "ORDER BY bm25(fts, 6.0, 3.0, 1.0) LIMIT 60",
                    (q,),
                ).fetchall()
                for path, title, heading, body in rows:
                    if path in exclude or path in hits:
                        continue
                    hits[path] = Related(
                        path=path,
                        title=title,
                        snippet=_snippet(heading, body),
                        uri=obsidian_uri(self.vault_name, path),
                    )
                    if len(hits) >= limit:
                        return list(hits.values())
        return list(hits.values())

    async def search(self, topics: list[str], exclude: set[str], limit: int) -> list[Related]:
        try:
            return await asyncio.to_thread(self.search_sync, topics, exclude, limit)
        except sqlite3.Error:
            log.exception("vault search failed")
            return []

    def resolve_name_sync(self, name: str) -> str | None:
        wanted = name.strip().removesuffix(".md").lower()
        if not wanted:
            return None
        with self._connect() as db:
            for (path,) in db.execute("SELECT path FROM files"):
                stem = PurePosixPath(path).stem
                if stem.lower() == wanted:
                    return stem
        return None

    async def resolve_name(self, name: str) -> str | None:
        return await asyncio.to_thread(self.resolve_name_sync, name)
