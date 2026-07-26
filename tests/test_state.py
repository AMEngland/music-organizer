#!/usr/bin/env python3
"""Tests for the SQLite state layer: scan cache, library index, undo log."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import state  # noqa: E402


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.database = state.LibraryDatabase(self.root / state.DB_FILENAME)

    def tearDown(self):
        self.database.close()
        self._tmp.cleanup()


class TestScanCache(StateTestCase):
    def test_roundtrip(self):
        path = self.root / "a.mp3"
        tags = {field: None for field in state.CACHED_TAG_FIELDS}
        tags["tag_artist"] = "Nirvana"
        tags["track_number"] = 3

        self.database.store_scan(path, 123.5, 4096, "deadbeef", "audio", 320000, tags)
        cached = self.database.cached_scan(path, 123.5, 4096)

        self.assertEqual(cached["audio_hash"], "deadbeef")
        self.assertEqual(cached["hash_scope"], "audio")
        self.assertEqual(cached["bitrate"], 320000)
        self.assertEqual(cached["tag_artist"], "Nirvana")
        self.assertEqual(cached["track_number"], 3)

    def test_changed_mtime_invalidates(self):
        path = self.root / "a.mp3"
        self.database.store_scan(path, 1.0, 10, "hash", "audio", 0, {})
        self.assertIsNone(self.database.cached_scan(path, 2.0, 10))

    def test_changed_size_invalidates(self):
        path = self.root / "a.mp3"
        self.database.store_scan(path, 1.0, 10, "hash", "audio", 0, {})
        self.assertIsNone(self.database.cached_scan(path, 1.0, 11))

    def test_unknown_path_returns_none(self):
        self.assertIsNone(self.database.cached_scan(self.root / "missing.mp3", 1.0, 1))

    def test_missing_files_are_forgotten(self):
        kept = self.root / "kept.mp3"
        gone = self.root / "gone.mp3"
        self.database.store_scan(kept, 1.0, 10, "a", "audio", 0, {})
        self.database.store_scan(gone, 1.0, 10, "b", "audio", 0, {})

        removed = self.database.forget_missing_scans([kept])

        self.assertEqual(removed, 1)
        self.assertIsNotNone(self.database.cached_scan(kept, 1.0, 10))
        self.assertIsNone(self.database.cached_scan(gone, 1.0, 10))


class TestLibraryIndex(StateTestCase):
    def test_add_and_lookup(self):
        self.database.add_to_library("hash1", self.root / "Artist" / "song.mp3")
        known = self.database.known_hashes()

        self.assertIn("hash1", known)
        self.assertEqual(self.database.library_size(), 1)

    def test_remove(self):
        self.database.add_to_library("hash1", self.root / "song.mp3")
        self.database.remove_from_library("hash1")
        self.assertEqual(self.database.library_size(), 0)

    def test_reinsert_replaces_target(self):
        self.database.add_to_library("hash1", self.root / "old.mp3")
        self.database.add_to_library("hash1", self.root / "new.mp3")

        self.assertEqual(self.database.library_size(), 1)
        self.assertTrue(self.database.known_hashes()["hash1"].endswith("new.mp3"))


class TestRunsAndOperations(StateTestCase):
    def test_run_lifecycle(self):
        run_id = self.database.start_run(self.root, ["--dry-run"], dry_run=True)
        self.database.finish_run(run_id)

        rows = self.database.runs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], run_id)
        self.assertIsNotNone(rows[0]["finished_at"])
        self.assertEqual(rows[0]["dry_run"], 1)

    def test_operations_are_returned_newest_first(self):
        run_id = self.database.start_run(self.root, [])
        self.database.record_operation(run_id, "copy", self.root / "a", self.root / "A", "h1")
        self.database.record_operation(run_id, "copy", self.root / "b", self.root / "B", "h2")

        operations = self.database.operations_for_run(run_id)
        self.assertEqual(len(operations), 2)
        self.assertEqual(operations[0]["audio_hash"], "h2")

    def test_delete_operations(self):
        run_id = self.database.start_run(self.root, [])
        self.database.record_operation(run_id, "copy", self.root / "a", self.root / "A")
        ids = [row["id"] for row in self.database.operations_for_run(run_id)]

        self.database.delete_operations(ids)
        self.assertEqual(self.database.operations_for_run(run_id), [])

    def test_last_run_id_ignores_dry_runs_and_empty_runs(self):
        applied = self.database.start_run(self.root, [])
        self.database.record_operation(applied, "copy", self.root / "a", self.root / "A")
        dry = self.database.start_run(self.root, [], dry_run=True)
        self.database.record_operation(dry, "copy", self.root / "b", self.root / "B")
        self.database.start_run(self.root, [])  # applied but did nothing

        self.assertEqual(self.database.last_run_id(), applied)

    def test_last_run_id_is_none_on_a_fresh_database(self):
        self.assertIsNone(self.database.last_run_id())


class TestPersistence(StateTestCase):
    def test_state_survives_reopen(self):
        self.database.add_to_library("hash1", self.root / "song.mp3")
        self.database.close()

        reopened = state.LibraryDatabase(self.root / state.DB_FILENAME)
        try:
            self.assertEqual(reopened.library_size(), 1)
        finally:
            reopened.close()
            # tearDown closes self.database again; that is safe.
            self.database = reopened


if __name__ == "__main__":
    unittest.main()
