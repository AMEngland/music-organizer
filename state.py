#!/usr/bin/env python3
"""SQLite-backed state for the music organizer.

Three things are persisted:

* a **scan cache** keyed by ``(path, mtime, size)`` so unchanged files are not
  re-hashed and re-tagged on every run;
* a **library index** of audio hashes already present in the organized
  collection, which is what makes incremental runs cheap and duplicate-free;
* an **operation log** of every copy and move, so a run can be undone.

The database lives inside the organized collection as ``.music_organizer.db``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

__all__ = ["LibraryDatabase", "DB_FILENAME", "SCHEMA_VERSION"]

DB_FILENAME = ".music_organizer.db"
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    path        TEXT PRIMARY KEY,
    mtime       REAL NOT NULL,
    size        INTEGER NOT NULL,
    audio_hash  TEXT,
    hash_scope  TEXT,
    bitrate     INTEGER,
    tags        TEXT,
    scanned_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library (
    audio_hash  TEXT PRIMARY KEY,
    target_path TEXT NOT NULL,
    source_path TEXT,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    source_root TEXT,
    arguments   TEXT,
    dry_run     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS operations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    action      TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    audio_hash  TEXT,
    performed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fingerprints (
    audio_hash   TEXT PRIMARY KEY,
    duration     INTEGER,
    fingerprint  TEXT,
    recording_id TEXT,
    score        REAL,
    artist       TEXT,
    title        TEXT,
    album        TEXT,
    year         TEXT,
    looked_up_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS operations_run ON operations(run_id);
CREATE INDEX IF NOT EXISTS library_target ON library(target_path);
CREATE INDEX IF NOT EXISTS fingerprints_recording ON fingerprints(recording_id);
"""

# Tag fields cached as JSON alongside the hash.
CACHED_TAG_FIELDS = (
    "tag_artist", "tag_albumartist", "tag_album", "tag_title",
    "genre", "year", "track_number", "disc_number",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class LibraryDatabase:
    """Persistent state for one organized collection."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._transaction() as connection:
            connection.executescript(_SCHEMA)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._connection:
                yield self._connection
        except sqlite3.Error:
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "LibraryDatabase":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Scan cache
    # ------------------------------------------------------------------

    def cached_scan(self, path: Path, mtime: float, size: int) -> Optional[Dict]:
        """Return the cached scan for *path* if it has not changed since."""
        row = self._connection.execute(
            "SELECT * FROM scans WHERE path = ?", (str(path),)
        ).fetchone()

        if row is None or row["size"] != size or abs(row["mtime"] - mtime) > 1e-6:
            return None
        if not row["audio_hash"]:
            return None

        cached = {
            "audio_hash": row["audio_hash"],
            "hash_scope": row["hash_scope"],
            "bitrate": row["bitrate"] or 0,
        }
        try:
            cached.update(json.loads(row["tags"] or "{}"))
        except json.JSONDecodeError:
            return None
        return cached

    def store_scan(self, path: Path, mtime: float, size: int, audio_hash: str,
                   hash_scope: str, bitrate: int, tags: Dict) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO scans "
                "(path, mtime, size, audio_hash, hash_scope, bitrate, tags, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(path), mtime, size, audio_hash, hash_scope, bitrate,
                 json.dumps(tags), _now()),
            )

    def forget_missing_scans(self, existing: Iterable[Path]) -> int:
        """Drop cache rows for files that no longer exist. Returns rows removed."""
        keep = {str(path) for path in existing}
        rows = [row["path"] for row in
                self._connection.execute("SELECT path FROM scans").fetchall()]
        stale = [(path,) for path in rows if path not in keep]
        if stale:
            with self._transaction() as connection:
                connection.executemany("DELETE FROM scans WHERE path = ?", stale)
        return len(stale)

    # ------------------------------------------------------------------
    # Library index
    # ------------------------------------------------------------------

    def known_hashes(self) -> Dict[str, str]:
        """Map of audio hash -> target path for everything already filed."""
        return {
            row["audio_hash"]: row["target_path"]
            for row in self._connection.execute(
                "SELECT audio_hash, target_path FROM library"
            )
        }

    def library_size(self) -> int:
        return self._connection.execute("SELECT COUNT(*) FROM library").fetchone()[0]

    def add_to_library(self, audio_hash: str, target_path: Path,
                       source_path: Optional[Path] = None) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO library "
                "(audio_hash, target_path, source_path, added_at) VALUES (?, ?, ?, ?)",
                (audio_hash, str(target_path),
                 str(source_path) if source_path else None, _now()),
            )

    def remove_from_library(self, audio_hash: str) -> None:
        with self._transaction() as connection:
            connection.execute("DELETE FROM library WHERE audio_hash = ?", (audio_hash,))

    # ------------------------------------------------------------------
    # Runs and the undo log
    # ------------------------------------------------------------------

    def start_run(self, source_root: Path, arguments: Sequence[str],
                  dry_run: bool = False) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO runs (started_at, source_root, arguments, dry_run) "
                "VALUES (?, ?, ?, ?)",
                (_now(), str(source_root), " ".join(arguments), int(dry_run)),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE runs SET finished_at = ? WHERE run_id = ?", (_now(), run_id)
            )

    def record_operation(self, run_id: int, action: str, source_path: Path,
                         target_path: Path, audio_hash: Optional[str] = None) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO operations "
                "(run_id, action, source_path, target_path, audio_hash, performed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, action, str(source_path), str(target_path), audio_hash, _now()),
            )

    def operations_for_run(self, run_id: int) -> List[sqlite3.Row]:
        return list(self._connection.execute(
            "SELECT * FROM operations WHERE run_id = ? ORDER BY id DESC", (run_id,)
        ))

    def delete_operations(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        with self._transaction() as connection:
            connection.executemany(
                "DELETE FROM operations WHERE id = ?", [(i,) for i in ids]
            )

    def runs(self, limit: int = 20) -> List[sqlite3.Row]:
        return list(self._connection.execute(
            "SELECT r.*, "
            "(SELECT COUNT(*) FROM operations o WHERE o.run_id = r.run_id) AS operations "
            "FROM runs r ORDER BY r.run_id DESC LIMIT ?", (limit,)
        ))

    # ------------------------------------------------------------------
    # Acoustic fingerprint cache
    # ------------------------------------------------------------------

    def cached_fingerprint(self, audio_hash: str) -> Optional[Dict]:
        """Return a cached AcoustID lookup, including remembered misses.

        Negative results are cached too -- a track the service does not know is
        not worth asking about again on every run.
        """
        row = self._connection.execute(
            "SELECT * FROM fingerprints WHERE audio_hash = ?", (audio_hash,)
        ).fetchone()
        return dict(row) if row else None

    def store_fingerprint(self, audio_hash: str, duration: Optional[int],
                          fingerprint: Optional[str], match: Optional[Dict]) -> None:
        match = match or {}
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO fingerprints "
                "(audio_hash, duration, fingerprint, recording_id, score, "
                " artist, title, album, year, looked_up_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (audio_hash, duration, fingerprint, match.get("recording_id"),
                 match.get("score"), match.get("artist"), match.get("title"),
                 match.get("album"), match.get("year"), _now()),
            )

    def recording_groups(self) -> Dict[str, List[str]]:
        """Audio hashes grouped by AcoustID recording, for encode-level dupes."""
        groups: Dict[str, List[str]] = {}
        for row in self._connection.execute(
            "SELECT recording_id, audio_hash FROM fingerprints "
            "WHERE recording_id IS NOT NULL"
        ):
            groups.setdefault(row["recording_id"], []).append(row["audio_hash"])
        return groups

    def last_run_id(self) -> Optional[int]:
        row = self._connection.execute(
            "SELECT run_id FROM runs WHERE dry_run = 0 "
            "AND run_id IN (SELECT run_id FROM operations) "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        return int(row["run_id"]) if row else None
