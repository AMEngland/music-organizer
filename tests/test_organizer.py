#!/usr/bin/env python3
"""End-to-end tests for the organizer pipeline.

Files are synthesized rather than sampled, so the suite runs without any real
audio on disk. Where a test needs genuine ID3 tags it uses mutagen and is
skipped if mutagen is unavailable.
"""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import music_organizer  # noqa: E402
import state  # noqa: E402
from music_organizer import AdvancedMusicOrganizer  # noqa: E402

try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
    MUTAGEN = True
except ImportError:  # pragma: no cover
    MUTAGEN = False

# A single silent MPEG-1 Layer III frame, repeated to make a decodable stream.
_FRAME = b"\xff\xfb\x90\x64" + bytes(413)
SILENT_MP3 = _FRAME * 40


class OrganizerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "Source"
        self.source.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, relative: str, data: bytes = SILENT_MP3) -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def build(self) -> AdvancedMusicOrganizer:
        organizer = AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini"
        )
        organizer.config.set("GENERAL", "interactive_mode", "false")
        return organizer

    def outputs(self, organizer: AdvancedMusicOrganizer):
        """Relative paths of every audio file written to the output tree."""
        if not organizer.organized_path.exists():
            return []
        return sorted(
            str(p.relative_to(organizer.organized_path)).replace("\\", "/")
            for p in organizer.organized_path.rglob("*")
            if p.is_file() and p.suffix.lower() in music_organizer.MUSIC_EXTENSIONS
        )

    @staticmethod
    def read_csv(path: Path):
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def write_csv(path: Path, rows):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


class TestClassification(OrganizerTestCase):
    def test_directory_structure_is_used_without_a_music_folder(self):
        # The old implementation required a path component literally named
        # "Music" and dumped everything else into manual sort.
        self.write("Pink Floyd/The Wall/01 Comfortably Numb.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertIn(
            "Pink Floyd/The Wall/01 - Comfortably Numb.mp3", self.outputs(organizer)
        )
        self.assertEqual(organizer.stats["manual_sort"], 0)

    def test_filename_patterns_are_classified(self):
        # Distinct payloads, otherwise deduplication would collapse them.
        self.write("Nirvana-Lithium.mp3", SILENT_MP3 + b"\x01")
        self.write("Beatles_HeyJude.mp3", SILENT_MP3 + b"\x02")
        self.write("Muse – Hysteria.mp3", SILENT_MP3 + b"\x03")
        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertIn("Nirvana/Singles/Lithium.mp3", outputs)
        self.assertIn("Beatles/Singles/HeyJude.mp3", outputs)
        self.assertIn("Muse/Singles/Hysteria.mp3", outputs)
        self.assertEqual(organizer.stats["manual_sort"], 0)

    def test_genre_artist_album_tree_picks_the_right_two_levels(self):
        self.write("Rock/Radiohead/OK Computer/03 Karma Police.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertIn(
            "Radiohead/OK Computer/03 - Karma Police.mp3", self.outputs(organizer)
        )

    def test_disc_folders_become_a_filename_prefix(self):
        self.write("Genesis/The Lamb/CD2/01 The Lamia.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertIn("Genesis/The Lamb/2-01 - The Lamia.mp3", self.outputs(organizer))

    def test_generic_container_folders_are_ignored(self):
        self.write("Downloads/mp3s/Portishead/Dummy/02 Sour Times.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertIn("Portishead/Dummy/02 - Sour Times.mp3", self.outputs(organizer))

    def test_unparseable_file_goes_to_manual_sort(self):
        self.write("audio_2049.mp3")
        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertTrue(any(o.startswith(music_organizer.MANUAL_SORT + "/") for o in outputs))
        self.assertEqual(organizer.stats["manual_sort"], 1)

    def test_unclassified_report_is_written(self):
        self.write("audio_2049.mp3")
        organizer = self.build()
        organizer.organize()

        report = (organizer.reports_path / "unclassified.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("audio_2049.mp3", report)


class TestRenaming(OrganizerTestCase):
    def test_messy_filename_is_normalized(self):
        self.write("Muse/Absolution/03_-_Muse_-_Hysteria_[320kbps]_www.dodgy.com.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertIn("Muse/Absolution/03 - Hysteria.mp3", self.outputs(organizer))
        self.assertGreater(organizer.stats["files_renamed"], 0)

    def test_track_numbers_are_zero_padded(self):
        self.write("Artist X/Album Y/1 - First.mp3", SILENT_MP3 + b"\x01")
        self.write("Artist X/Album Y/10 - Tenth.mp3", SILENT_MP3 + b"\x0a")
        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertIn("Artist X/Album Y/01 - First.mp3", outputs)
        self.assertIn("Artist X/Album Y/10 - Tenth.mp3", outputs)

    def test_no_rename_preserves_original_names(self):
        self.write("Artist X/Album Y/03 - track.mp3")
        organizer = self.build()
        organizer.config.set("NAMING", "rename_files", "false")
        organizer.organize()

        self.assertIn("Artist X/Album Y/03 - track.mp3", self.outputs(organizer))

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_illegal_characters_are_stripped_from_folders(self):
        # The offending characters can only arrive via tags -- Windows will not
        # let a source directory be named "AC/DC" in the first place.
        path = self.write("unsorted/track.mp3")
        tags = ID3()
        tags.add(TPE1(encoding=3, text="AC/DC"))
        tags.add(TALB(encoding=3, text="Back In Black?"))
        tags.add(TIT2(encoding=3, text="Hells Bells"))
        tags.add(TRCK(encoding=3, text="1"))
        tags.save(path)

        organizer = self.build()
        organizer.organize()

        self.assertIn("ACDC/Back In Black/01 - Hells Bells.mp3", self.outputs(organizer))

    def test_distinct_songs_sharing_a_name_do_not_overwrite(self):
        self.write("Artist A/Album/01 Intro.mp3", SILENT_MP3)
        self.write("Artist A/Album/CD1/01 Intro.mp3", SILENT_MP3 + b"\x00" * 32)
        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertEqual(len([o for o in outputs if "Intro" in o]), 2)


class TestDeduplication(OrganizerTestCase):
    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_same_audio_with_different_tags_is_deduplicated(self):
        first = self.write("Copy A/Artist/Album/01 Song.mp3")
        second = self.write("Copy B/Artist/Album/01 Song.mp3")

        tags = ID3()
        tags.add(TIT2(encoding=3, text="Song"))
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.save(first)

        other = ID3()
        other.add(TIT2(encoding=3, text="Song"))
        other.add(TPE1(encoding=3, text="Artist"))
        other.add(TALB(encoding=3, text="A Totally Different Album Tag Value"))
        other.save(second)

        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.stats["duplicates_skipped"], 1)
        self.assertEqual(organizer.stats["organized"], 1)

    def test_byte_identical_copies_are_deduplicated(self):
        self.write("Artist/Album/01 Song.mp3")
        self.write("Backup/Artist/Album/01 Song.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.stats["duplicates_skipped"], 1)
        self.assertEqual(organizer.stats["organized"], 1)

    def test_higher_quality_copy_wins(self):
        low = self.write("Low/Artist/Album/01 Song.mp3")
        high = self.write("High/Artist/Album/01 Song.flac")

        organizer = self.build()
        # Force both records into one duplicate group with known qualities.
        records = [organizer.scan_file(low), organizer.scan_file(high)]
        for record in records:
            record.audio_hash = "same"
        records[0].bitrate = 128000
        records[1].bitrate = 0

        kept = organizer.deduplicate(records)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].path, high)  # lossless beats a 128kbps mp3

    def test_bitrate_breaks_ties_between_same_format(self):
        low = self.write("Low/Artist/Album/01 Song.mp3")
        high = self.write("High/Artist/Album/01 Song.mp3")

        organizer = self.build()
        records = [organizer.scan_file(low), organizer.scan_file(high)]
        for record in records:
            record.audio_hash = "same"
        records[0].bitrate = 128000
        records[1].bitrate = 320000

        kept = organizer.deduplicate(records)
        self.assertEqual(kept[0].path, high)

    def test_duplicate_report_names_both_files(self):
        self.write("Artist/Album/01 Song.mp3")
        self.write("Backup/Artist/Album/01 Song.mp3")
        organizer = self.build()
        organizer.organize()

        report = (organizer.reports_path / "duplicates.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("Skipped:", report)
        self.assertIn("kept:", report)


class TestArtistFolding(OrganizerTestCase):
    def test_name_variants_share_one_folder(self):
        self.write("The Beatles/Abbey Road/01 Come Together.mp3")
        self.write("beatles/Revolver/01 Taxman.mp3", SILENT_MP3 + b"\x01")
        self.write("BEATLES/Help/01 Help.mp3", SILENT_MP3 + b"\x02")

        organizer = self.build()
        organizer.organize()

        artist_dirs = sorted(
            p.name for p in organizer.organized_path.iterdir()
            if p.is_dir() and p.name not in {music_organizer.PLAYLISTS_FOLDER, music_organizer.REPORTS_FOLDER,
                                   music_organizer.MANUAL_SORT}
        )
        self.assertEqual(artist_dirs, ["The Beatles"])

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_album_artist_keeps_compilations_together(self):
        paths = []
        for index, (artist, title) in enumerate(
            [("Artist One", "Track One"), ("Artist Two", "Track Two")], start=1
        ):
            path = self.write(f"Various/{index:02d} {title}.mp3", SILENT_MP3 + bytes([index]))
            tags = ID3()
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist))
            tags.add(TPE2(encoding=3, text="Now Thats What I Call Music"))
            tags.add(TALB(encoding=3, text="Now 42"))
            tags.add(TRCK(encoding=3, text=str(index)))
            tags.save(path)
            paths.append(path)

        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertEqual(
            outputs,
            [
                "Now Thats What I Call Music/Now 42/01 - Track One.mp3",
                "Now Thats What I Call Music/Now 42/02 - Track Two.mp3",
            ],
        )


class TestSkipFolders(OrganizerTestCase):
    def test_skip_matches_components_not_substrings(self):
        # A path that merely contains a skip word must not be discarded.
        self.write(".Trash-backup/Artist/Album/01 Song.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.stats["total_files"], 1)

    def test_named_skip_folder_is_excluded(self):
        self.write(".Trash/Artist/Album/01 Song.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.stats["total_files"], 0)


class TestPlaylists(OrganizerTestCase):
    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_playlist_uses_forward_slashes(self):
        for index in range(1, 6):
            path = self.write(f"Artist/Album/{index:02d} Song {index}.mp3",
                              SILENT_MP3 + bytes([index]))
            tags = ID3()
            tags.add(TIT2(encoding=3, text=f"Song {index}"))
            tags.add(TPE1(encoding=3, text="Artist"))
            tags.add(TALB(encoding=3, text="Album"))
            tags.add(TRCK(encoding=3, text=str(index)))
            tags.save(path)

        organizer = self.build()
        organizer.config.set("PLAYLISTS", "create_by_artist", "true")
        organizer.organize()

        playlists = list(organizer.playlists_path.glob("*.m3u"))
        self.assertTrue(playlists)
        content = playlists[0].read_text(encoding="utf-8")
        self.assertNotIn("\\", content)
        self.assertIn("../Artist/Album/", content)


class TestAlbumArt(OrganizerTestCase):
    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_apic_with_a_description_is_extracted(self):
        # The old code tested `'APIC:' in tags`, which misses every frame that
        # carries a description -- i.e. most real files.
        path = self.write("Artist/Album/01 Song.mp3")
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Song"))
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.add(TALB(encoding=3, text="Album"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3,
                      desc="Cover (front)", data=b"\xff\xd8\xff\xe0FAKEJPEG"))
        tags.save(path)

        organizer = self.build()
        organizer.organize()

        art = organizer.organized_path / "Artist" / "Album" / "folder.jpg"
        self.assertTrue(art.exists())
        self.assertEqual(art.read_bytes(), b"\xff\xd8\xff\xe0FAKEJPEG")
        self.assertEqual(organizer.stats["album_art_extracted"], 1)

    def test_no_artwork_leaves_no_stray_image(self):
        self.write("Artist/Album/01 Song.mp3")
        organizer = self.build()
        organizer.organize()

        album_dir = organizer.organized_path / "Artist" / "Album"
        self.assertFalse((album_dir / "folder.jpg").exists())
        self.assertFalse((album_dir / "folder.png").exists())
        self.assertEqual(organizer.stats["album_art_extracted"], 0)

    def test_no_empty_directories_are_created(self):
        self.write("Artist/Album/01 Song.mp3")
        organizer = self.build()
        organizer.organize()

        empty = [
            p for p in organizer.organized_path.rglob("*")
            if p.is_dir() and not any(p.iterdir())
        ]
        self.assertEqual(empty, [])


class TestTagReading(OrganizerTestCase):
    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_numeric_genre_reference_is_expanded(self):
        path = self.write("Artist/Album/01 Song.mp3")
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Song"))
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.add(TCON(encoding=3, text="(17)"))  # ID3v1 genre 17 == Rock
        tags.save(path)

        organizer = self.build()
        record = organizer.scan_file(path)
        self.assertEqual(record.genre, "Rock")

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_track_and_year_are_parsed_from_compound_values(self):
        path = self.write("Artist/Album/song.mp3")
        tags = ID3()
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.add(TRCK(encoding=3, text="7/12"))
        tags.add(TDRC(encoding=3, text="1973-03-01"))
        tags.save(path)

        organizer = self.build()
        record = organizer.scan_file(path)
        self.assertEqual(record.track_number, 7)
        self.assertEqual(record.year, "1973")


class TestLongPaths(OrganizerTestCase):
    def test_fs_path_uses_extended_form_only_when_needed(self):
        short = music_organizer.fs_path(Path("C:/music/a.mp3"))
        self.assertFalse(short.startswith("\\\\?\\"))

        long_path = Path("C:/music") / ("x" * 300) / "a.mp3"
        rendered = music_organizer.fs_path(long_path)
        if sys.platform == "win32":
            self.assertTrue(rendered.startswith("\\\\?\\"))
        else:
            self.assertFalse(rendered.startswith("\\\\?\\"))

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_very_long_title_still_gets_copied(self):
        path = self.write("Artist/Album/01 t.mp3")
        tags = ID3()
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.add(TALB(encoding=3, text="Album"))
        tags.add(TIT2(encoding=3, text="verbose title segment " * 12))
        tags.add(TRCK(encoding=3, text="1"))
        tags.save(path)

        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.stats["organized"], 1)
        self.assertEqual(organizer.stats["errors"], [])


class TestConfig(OrganizerTestCase):
    def test_missing_sections_are_backfilled(self):
        config_path = self.root / "config.ini"
        config_path.write_text("[GENERAL]\ncreate_playlists = false\n", encoding="utf-8")

        organizer = AdvancedMusicOrganizer(self.source, config_file=config_path)

        # Pre-existing value preserved, new section supplied.
        self.assertFalse(organizer.config.getboolean("GENERAL", "create_playlists"))
        self.assertTrue(organizer.config.has_section("NAMING"))
        self.assertTrue(organizer.config.getboolean("NAMING", "rename_files"))


class TestDryRun(OrganizerTestCase):
    def test_nothing_is_written(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini", dry_run=True
        )
        organizer.organize()

        self.assertEqual(self.outputs(organizer), [])
        self.assertEqual(organizer.stats["organized"], 0)
        self.assertFalse((organizer.organized_path / "Nirvana").exists())

    def test_plan_csv_describes_the_intended_result(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini", dry_run=True
        )
        organizer.organize()

        rows = self.read_csv(organizer.reports_path / "plan.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "copy")
        self.assertEqual(rows[0]["artist"], "Nirvana")
        self.assertEqual(rows[0]["album"], "Nevermind")
        self.assertTrue(rows[0]["target_path"].endswith("01 - Lithium.mp3"))

    def test_plan_csv_lists_skipped_duplicates(self):
        self.write("Artist/Album/01 Song.mp3")
        self.write("Backup/Artist/Album/01 Song.mp3")
        organizer = AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini", dry_run=True
        )
        organizer.organize()

        rows = self.read_csv(organizer.reports_path / "plan.csv")
        skipped = [row for row in rows if row["action"] == "skip-duplicate"]
        self.assertEqual(len(skipped), 1)
        self.assertIn("duplicate of", skipped[0]["reason"])

    def test_dry_run_leaves_the_library_index_empty(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini", dry_run=True
        )
        organizer.organize()

        database = state.LibraryDatabase(organizer.organized_path / state.DB_FILENAME)
        try:
            self.assertEqual(database.library_size(), 0)
        finally:
            database.close()


class TestIdempotency(OrganizerTestCase):
    def test_rerunning_does_not_duplicate(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")

        first = self.build()
        first.organize()
        second = self.build()
        second.organize()

        self.assertEqual(len(self.outputs(second)), 1)
        self.assertEqual(second.stats["organized"], 0)
        self.assertEqual(second.stats["already_in_library"], 1)

    def test_incremental_adds_only_new_material(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3", SILENT_MP3 + b"\x01")
        organizer = self.build()
        organizer.organize()

        self.write("Nirvana/Nevermind/02 In Bloom.mp3", SILENT_MP3 + b"\x02")
        second = self.build()
        second.organize(reindex=True)

        outputs = self.outputs(second)
        self.assertEqual(len(outputs), 2)
        self.assertEqual(second.stats["organized"], 1)
        self.assertEqual(second.stats["already_in_library"], 1)

    def test_reindex_recovers_from_a_deleted_database(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()
        (organizer.organized_path / state.DB_FILENAME).unlink()

        second = self.build()
        second.organize()

        self.assertEqual(second.stats["already_in_library"], 1)
        self.assertEqual(second.stats["organized"], 0)


class TestScanCache(OrganizerTestCase):
    def test_second_run_reuses_cached_scans(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")

        first = self.build()
        first.organize()
        self.assertEqual(first.stats["scans_from_cache"], 0)

        second = self.build()
        second.organize()
        self.assertEqual(second.stats["scans_from_cache"], 1)

    def test_modified_file_is_rescanned(self):
        path = self.write("Nirvana/Nevermind/01 Lithium.mp3")
        self.build().organize()

        path.write_bytes(SILENT_MP3 + b"\x99")
        second = self.build()
        second.organize()

        self.assertEqual(second.stats["scans_from_cache"], 0)


class TestConfidenceRouting(OrganizerTestCase):
    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_tagged_files_score_higher_than_filename_guesses(self):
        tagged = self.write("a/tagged.mp3", SILENT_MP3 + b"\x01")
        tags = ID3()
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.add(TALB(encoding=3, text="Album"))
        tags.add(TIT2(encoding=3, text="Song"))
        tags.add(TRCK(encoding=3, text="1"))
        tags.save(tagged)
        self.write("Guessed-Title.mp3", SILENT_MP3 + b"\x02")

        organizer = self.build()
        organizer.organize()

        scores = {r.path.name: r.confidence for r in organizer.planned}
        self.assertGreater(scores["tagged.mp3"], scores["Guessed-Title.mp3"])

    def test_unclassified_file_scores_zero(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.unclassified[0].confidence, 0)
        self.assertEqual(organizer.unclassified[0].route, music_organizer.ROUTE_MANUAL)

    def test_low_confidence_stays_in_the_library_by_default(self):
        self.write("Nirvana-Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.stats["needs_review"], 1)
        self.assertIn("Nirvana/Singles/Lithium.mp3", self.outputs(organizer))

    def test_route_review_separates_low_confidence_files(self):
        self.write("Nirvana-Lithium.mp3")
        organizer = self.build()
        organizer.config.set("CLASSIFY", "route_review", "true")
        organizer.organize()

        self.assertIn(music_organizer.REVIEW_FOLDER + "/Nirvana/Singles/Lithium.mp3", self.outputs(organizer))

    def test_review_queue_csv_is_written(self):
        self.write("Nirvana-Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        rows = self.read_csv(organizer.reports_path / "review_queue.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["artist"], "Nirvana")


class TestApplyCsv(OrganizerTestCase):
    def test_corrections_are_applied(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.organize()

        review_csv = organizer.reports_path / "review_queue.csv"
        rows = self.read_csv(review_csv)
        self.assertEqual(len(rows), 1)

        rows[0].update({"artist": "Boards of Canada", "album": "Music Has the Right",
                        "title": "Roygbiv", "track": "4"})
        self.write_csv(review_csv, rows)

        applied = self.build()
        self.assertEqual(applied.apply_csv(review_csv), 1)

        self.assertTrue((organizer.organized_path / "Boards of Canada"
                         / "Music Has the Right" / "04 - Roygbiv.mp3").exists())
        self.assertFalse((organizer.manual_sort_path
                          / "09182734.mp3").exists())

    def test_rows_without_an_artist_are_skipped(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.organize()

        review_csv = organizer.reports_path / "review_queue.csv"
        applied = self.build()
        self.assertEqual(applied.apply_csv(review_csv), 0)
        self.assertTrue((organizer.manual_sort_path
                         / "09182734.mp3").exists())

    def test_unedited_rows_do_not_re_file_correctly_placed_tracks(self):
        # A review row that the user left alone must be a no-op, not a self-move
        # that mints "Lithium (2).mp3".
        self.write("Nirvana-Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        review_csv = organizer.reports_path / "review_queue.csv"
        applied = self.build()
        self.assertEqual(applied.apply_csv(review_csv), 0)

        self.assertEqual(self.outputs(organizer), ["Nirvana/Singles/Lithium.mp3"])

    def test_missing_csv_is_reported_not_raised(self):
        organizer = self.build()
        self.assertEqual(organizer.apply_csv(self.root / "nope.csv"), 0)


class TestUndo(OrganizerTestCase):
    def test_undo_removes_copied_files_and_empty_folders(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()
        self.assertEqual(len(self.outputs(organizer)), 1)

        undone = self.build()
        undone.organized_path = organizer.organized_path
        self.assertEqual(undone.undo_run(assume_yes=True), 1)

        self.assertEqual(self.outputs(undone), [])
        self.assertFalse((organizer.organized_path / "Nirvana").exists())

    def test_undo_leaves_the_source_untouched(self):
        source = self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        self.build().undo_run(assume_yes=True)
        self.assertTrue(source.exists())

    def test_undo_skips_files_modified_since_the_run(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        target = organizer.organized_path / "Nirvana" / "Nevermind" / "01 - Lithium.mp3"
        target.write_bytes(SILENT_MP3 + b"\xee")

        self.assertEqual(self.build().undo_run(assume_yes=True), 0)
        self.assertTrue(target.exists())

    def test_undo_restores_moved_files(self):
        collection = self.root / "Music_Organized"
        manual = collection / music_organizer.MANUAL_SORT
        manual.mkdir(parents=True)
        source = manual / "Nirvana - Lithium.mp3"
        source.write_bytes(SILENT_MP3)

        music_organizer.organize_manual_folder(manual, collection)
        self.assertFalse(source.exists())

        organizer = AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini", output_path=collection
        )
        self.assertEqual(organizer.undo_run(assume_yes=True), 1)
        self.assertTrue(source.exists())

    def test_undo_of_a_specific_run(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3", SILENT_MP3 + b"\x01")
        first = self.build()
        first.organize()
        first_run = first.run_id

        self.write("Muse/Absolution/01 Apocalypse.mp3", SILENT_MP3 + b"\x02")
        self.build().organize()

        self.assertEqual(self.build().undo_run(first_run, assume_yes=True), 1)

        outputs = self.outputs(self.build())
        self.assertEqual(outputs, ["Muse/Absolution/01 - Apocalypse.mp3"])

    def test_undo_with_no_runs_reports_cleanly(self):
        organizer = self.build()
        self.assertEqual(organizer.undo_run(assume_yes=True), 0)


class FakeMatch:
    """Stand-in for fingerprint.RecordingMatch."""

    def __init__(self, recording_id="rec-1", score=0.95, artist="Boards of Canada",
                 title="Roygbiv", album="Music Has the Right to Children", year=None):
        self.recording_id = recording_id
        self.score = score
        self.artist = artist
        self.title = title
        self.album = album
        self.year = year


class FakeFingerprintService:
    """Identifies every file it is handed, or a chosen subset."""

    def __init__(self, matches=None, default=None):
        self.matches = matches or {}
        self.default = default
        self.available = True
        self.calls = []

    def unavailable_reason(self):
        return ""

    def identify(self, path, audio_hash=None):
        self.calls.append(Path(path).name)
        return self.matches.get(Path(path).name, self.default)

    def summary(self):
        return f"stub, {len(self.calls)} call(s)"


class TestFingerprinting(OrganizerTestCase):
    def test_unidentified_file_is_named_by_fingerprint(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.build_fingerprint_service = lambda: FakeFingerprintService(
            default=FakeMatch()
        )
        organizer.organize(fingerprint_lookup=True)

        self.assertIn(
            "Boards of Canada/Music Has the Right to Children/Roygbiv.mp3",
            self.outputs(organizer),
        )
        self.assertEqual(organizer.stats["manual_sort"], 0)
        self.assertEqual(organizer.stats["identified_by_fingerprint"], 1)

    def test_only_unidentified_files_are_submitted(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3", SILENT_MP3 + b"\x01")
        self.write("09182734.mp3", SILENT_MP3 + b"\x02")

        service = FakeFingerprintService(default=FakeMatch())
        organizer = self.build()
        organizer.build_fingerprint_service = lambda: service
        organizer.organize(fingerprint_lookup=True)

        self.assertEqual(service.calls, ["09182734.mp3"])

    def test_every_file_is_submitted_when_configured(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3", SILENT_MP3 + b"\x01")
        self.write("09182734.mp3", SILENT_MP3 + b"\x02")

        service = FakeFingerprintService(default=None)
        organizer = self.build()
        organizer.config.set("FINGERPRINT", "only_unidentified", "false")
        organizer.build_fingerprint_service = lambda: service
        organizer.organize(fingerprint_lookup=True)

        self.assertEqual(len(service.calls), 2)

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_embedded_tags_outrank_an_acoustic_match(self):
        path = self.write("unsorted/track.mp3")
        tags = ID3()
        tags.add(TPE1(encoding=3, text="Nirvana"))
        tags.add(TALB(encoding=3, text="Nevermind"))
        tags.add(TIT2(encoding=3, text="Lithium"))
        tags.add(TRCK(encoding=3, text="1"))
        tags.save(path)

        organizer = self.build()
        organizer.config.set("FINGERPRINT", "only_unidentified", "false")
        organizer.build_fingerprint_service = lambda: FakeFingerprintService(
            default=FakeMatch(artist="Wrong Artist", title="Wrong Title")
        )
        organizer.organize(fingerprint_lookup=True)

        self.assertIn("Nirvana/Nevermind/01 - Lithium.mp3", self.outputs(organizer))

    def test_an_acoustic_match_outranks_a_folder_name_guess(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.config.set("FINGERPRINT", "only_unidentified", "false")
        organizer.build_fingerprint_service = lambda: FakeFingerprintService(
            default=FakeMatch(artist="Real Artist", title="Real Title",
                              album="Real Album")
        )
        organizer.organize(fingerprint_lookup=True)

        self.assertIn("Real Artist/Real Album/01 - Real Title.mp3",
                      self.outputs(organizer))

    def test_confidence_rises_after_identification(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.build_fingerprint_service = lambda: FakeFingerprintService(
            default=FakeMatch()
        )
        organizer.organize(fingerprint_lookup=True)

        record = organizer.planned[0]
        self.assertEqual(record.artist_source, "acoustid")
        self.assertGreater(record.confidence, 55)

    def test_unavailable_service_is_a_warning_not_a_failure(self):
        self.write("09182734.mp3")

        class Unavailable(FakeFingerprintService):
            available = False

            def unavailable_reason(self):
                return "fpcalc not found"

        organizer = self.build()
        organizer.build_fingerprint_service = lambda: Unavailable()
        organizer.organize(fingerprint_lookup=True)

        self.assertEqual(organizer.stats["manual_sort"], 1)
        self.assertEqual(organizer.stats["errors"], [])

    def test_fingerprinting_is_off_unless_requested(self):
        self.write("09182734.mp3")
        service = FakeFingerprintService(default=FakeMatch())
        organizer = self.build()
        organizer.build_fingerprint_service = lambda: service
        organizer.organize()

        self.assertEqual(service.calls, [])

    def test_encode_duplicates_merge_when_enabled(self):
        # Different audio, same recording: only fingerprinting can tell.
        self.write("rip-128/09182734.mp3", SILENT_MP3 + b"\x01")
        self.write("rip-320/09182735.mp3", SILENT_MP3 + b"\x02" * 200)

        organizer = self.build()
        organizer.config.set("FINGERPRINT", "merge_duplicate_encodes", "true")
        organizer.build_fingerprint_service = lambda: FakeFingerprintService(
            default=FakeMatch()
        )
        organizer.organize(fingerprint_lookup=True)

        self.assertEqual(organizer.stats["encode_duplicates_merged"], 1)
        self.assertEqual(len(self.outputs(organizer)), 1)

    def test_encode_duplicates_are_kept_by_default(self):
        self.write("rip-128/09182734.mp3", SILENT_MP3 + b"\x01")
        self.write("rip-320/09182735.mp3", SILENT_MP3 + b"\x02" * 200)

        organizer = self.build()
        organizer.build_fingerprint_service = lambda: FakeFingerprintService(
            default=FakeMatch()
        )
        organizer.organize(fingerprint_lookup=True)

        self.assertEqual(organizer.stats["encode_duplicates_merged"], 0)
        self.assertEqual(len(self.outputs(organizer)), 2)


class TestTagWriteBack(OrganizerTestCase):
    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_tags_are_written_to_the_copy(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.config.set("NAMING", "write_tags", "true")
        organizer.organize()

        target = organizer.organized_path / "Nirvana" / "Nevermind" / "01 - Lithium.mp3"
        tags = MutagenFile(target, easy=True)
        self.assertEqual(tags["artist"], ["Nirvana"])
        self.assertEqual(tags["album"], ["Nevermind"])
        self.assertEqual(tags["title"], ["Lithium"])
        self.assertEqual(tags["tracknumber"], ["1"])
        self.assertEqual(organizer.stats["tags_written"], 1)

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_source_file_is_not_modified(self):
        source = self.write("Nirvana/Nevermind/01 Lithium.mp3")
        before = source.read_bytes()

        organizer = self.build()
        organizer.config.set("NAMING", "write_tags", "true")
        organizer.organize()

        self.assertEqual(source.read_bytes(), before)

    def test_disabled_by_default(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()
        self.assertEqual(organizer.stats["tags_written"], 0)

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_manual_sort_files_are_left_untagged(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.config.set("NAMING", "write_tags", "true")
        organizer.organize()

        self.assertEqual(organizer.stats["tags_written"], 0)


class TestSidecars(OrganizerTestCase):
    def test_lyrics_file_follows_the_rename(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        (self.source / "Nirvana" / "Nevermind" / "01 Lithium.lrc").write_text(
            "[00:01.00]words", encoding="utf-8"
        )

        organizer = self.build()
        organizer.organize()

        sidecar = organizer.organized_path / "Nirvana" / "Nevermind" / "01 - Lithium.lrc"
        self.assertTrue(sidecar.exists())
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "[00:01.00]words")
        self.assertEqual(organizer.stats["sidecars_copied"], 1)

    def test_unlisted_extensions_are_not_copied(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        (self.source / "Nirvana" / "Nevermind" / "01 Lithium.bak").write_text(
            "junk", encoding="utf-8"
        )

        organizer = self.build()
        organizer.organize()

        album_dir = organizer.organized_path / "Nirvana" / "Nevermind"
        self.assertEqual(list(album_dir.glob("*.bak")), [])

    def test_folder_image_is_copied_as_folder_jpg(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        (self.source / "Nirvana" / "Nevermind" / "cover.jpg").write_bytes(b"\xff\xd8JPEG")

        organizer = self.build()
        organizer.organize()

        art = organizer.organized_path / "Nirvana" / "Nevermind" / "folder.jpg"
        self.assertTrue(art.exists())
        self.assertEqual(art.read_bytes(), b"\xff\xd8JPEG")

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_embedded_art_wins_over_a_loose_file(self):
        path = self.write("Nirvana/Nevermind/01 Lithium.mp3")
        tags = ID3()
        tags.add(TPE1(encoding=3, text="Nirvana"))
        tags.add(TALB(encoding=3, text="Nevermind"))
        tags.add(TIT2(encoding=3, text="Lithium"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover (front)",
                      data=b"EMBEDDED"))
        tags.save(path)
        (self.source / "Nirvana" / "Nevermind" / "cover.jpg").write_bytes(b"LOOSE")

        organizer = self.build()
        organizer.organize()

        art = organizer.organized_path / "Nirvana" / "Nevermind" / "folder.jpg"
        self.assertEqual(art.read_bytes(), b"EMBEDDED")

    def test_sidecars_are_undone_with_the_run(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        (self.source / "Nirvana" / "Nevermind" / "01 Lithium.lrc").write_text(
            "words", encoding="utf-8"
        )

        organizer = self.build()
        organizer.organize()
        self.build().undo_run(assume_yes=True)

        self.assertFalse((organizer.organized_path / "Nirvana").exists())

    def test_disabled_by_config(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        (self.source / "Nirvana" / "Nevermind" / "01 Lithium.lrc").write_text(
            "words", encoding="utf-8"
        )

        organizer = self.build()
        organizer.config.set("SIDECARS", "copy_sidecars", "false")
        organizer.organize()

        self.assertEqual(organizer.stats["sidecars_copied"], 0)


class TestParallelScanning(OrganizerTestCase):
    def test_serial_and_parallel_agree(self):
        for index in range(6):
            self.write(f"Artist/Album/{index:02d} Track {index}.mp3",
                       SILENT_MP3 + bytes([index]))

        serial = self.build()
        serial.config.set("PERFORMANCE", "workers", "1")
        serial.organize()

        hashes = sorted(r.audio_hash for r in serial.planned)
        self.assertEqual(len(set(hashes)), 6)
        self.assertEqual(serial.stats["organized"], 6)

    def test_worker_count_respects_config(self):
        organizer = self.build()
        organizer.config.set("PERFORMANCE", "workers", "1")
        self.assertEqual(organizer._worker_count(1000), 1)

        organizer.config.set("PERFORMANCE", "workers", "4")
        self.assertEqual(organizer._worker_count(1000), 4)

    def test_small_batches_stay_in_one_process(self):
        organizer = self.build()
        organizer.config.set("PERFORMANCE", "workers", "8")
        self.assertEqual(organizer._worker_count(3), 1)

    def test_auto_worker_count_is_bounded(self):
        organizer = self.build()
        organizer.config.set("PERFORMANCE", "workers", "0")
        self.assertLessEqual(organizer._worker_count(1000), 8)
        self.assertGreaterEqual(organizer._worker_count(1000), 1)

    def test_scan_payload_returns_plain_data(self):
        path = self.write("Artist/Album/01 Track.mp3")
        scanned = music_organizer.scan_payload(str(path))

        self.assertIn("audio_hash", scanned)
        self.assertIn("hash_scope", scanned)
        self.assertIsInstance(scanned["audio_hash"], str)

    def test_scan_payload_on_an_unreadable_file(self):
        path = self.source / "empty.mp3"
        path.write_bytes(b"")
        self.assertIsNone(music_organizer.scan_payload(str(path)))


class TestHtmlReport(OrganizerTestCase):
    def test_report_is_written(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        report = organizer.reports_path / "report.html"
        self.assertTrue(report.exists())
        page = report.read_text(encoding="utf-8")
        self.assertIn("Music Organizer report", page)
        self.assertIn("Nirvana", page)

    def test_report_lists_duplicates(self):
        self.write("Artist/Album/01 Song.mp3")
        self.write("Backup/Artist/Album/01 Song.mp3")
        organizer = self.build()
        organizer.organize()

        page = (organizer.reports_path / "report.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Redundant source files", page)
        self.assertIn("Backup", page)

    def test_report_can_be_disabled(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.config.set("GENERAL", "html_report", "false")
        organizer.organize()

        self.assertFalse((organizer.reports_path / "report.html").exists())


class TestInteractiveResolver(OrganizerTestCase):
    def _answers(self, *responses):
        queue = list(responses)
        return lambda prompt: queue.pop(0) if queue else "q"

    def test_a_group_is_filed_from_one_answer(self):
        self.write("09182734.mp3", SILENT_MP3 + b"\x01")
        self.write("09182735.mp3", SILENT_MP3 + b"\x02")
        organizer = self.build()
        organizer.organize()
        self.assertEqual(organizer.stats["manual_sort"], 2)

        resolver = self.build()
        moved = resolver.resolve_interactive(self._answers("Autechre", "Amber"))

        self.assertEqual(moved, 2)
        outputs = self.outputs(resolver)
        self.assertEqual(len(outputs), 2)
        self.assertTrue(all(o.startswith("Autechre/Amber/") for o in outputs))

    def test_blank_answer_skips_the_group(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.organize()

        resolver = self.build()
        self.assertEqual(resolver.resolve_interactive(self._answers("", "q")), 0)
        self.assertTrue((organizer.manual_sort_path
                         / "09182734.mp3").exists())

    def test_quit_stops_immediately(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.organize()

        resolver = self.build()
        self.assertEqual(resolver.resolve_interactive(self._answers("q")), 0)

    def test_blank_album_defaults_to_singles(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.organize()

        resolver = self.build()
        resolver.resolve_interactive(self._answers("Autechre", ""))

        self.assertIn("Autechre/Singles/09182734.mp3", self.outputs(resolver))

    def test_resolution_is_undoable(self):
        self.write("09182734.mp3")
        organizer = self.build()
        organizer.organize()

        resolver = self.build()
        resolver.resolve_interactive(self._answers("Autechre", "Amber"))

        self.build().undo_run(assume_yes=True)
        self.assertTrue((organizer.manual_sort_path
                         / "09182734.mp3").exists())

    def test_empty_manual_folder_is_handled(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        resolver = self.build()
        self.assertEqual(resolver.resolve_interactive(self._answers()), 0)


class TestContainerFolders(OrganizerTestCase):
    """A folder wrapping the whole library must not become an artist.

    ``All Music/Nirvana/x.mp3`` and ``Nirvana/Nevermind/x.mp3`` are the same
    shape, so the parser used to read the wrapper as the artist and the real
    artist as an album.
    """

    def _tagged(self, relative, artist, album, title, track, payload):
        path = self.write(relative, payload)
        if MUTAGEN:
            tags = ID3()
            tags.add(TPE1(encoding=3, text=artist))
            tags.add(TALB(encoding=3, text=album))
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TRCK(encoding=3, text=str(track)))
            tags.save(path)
        return path

    def test_named_container_is_not_an_artist(self):
        for index, artist in enumerate(["Nirvana", "Portishead"], start=1):
            self.write(f"All Music/{artist}/01 track.mp3", SILENT_MP3 + bytes([index]))

        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertFalse(any(o.startswith("All Music/") for o in outputs))
        self.assertIn("Nirvana/Singles/01 - Track.mp3", outputs)
        self.assertIn("Portishead/Singles/01 - Track.mp3", outputs)

    def test_extra_container_names_can_be_configured(self):
        self.write("Steves Rips/Nirvana/01 track.mp3")

        organizer = self.build()
        organizer.config.set("FOLDERS", "container_folders", "Steves Rips")
        organizer.organize()

        self.assertIn("Nirvana/Singles/01 - Track.mp3", self.outputs(organizer))

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_unlisted_container_detected_from_tag_evidence(self):
        # Untagged files under a wrapper whose children are artists the tags
        # elsewhere in the collection know about.
        for index, artist in enumerate(["Nirvana", "Portishead"], start=1):
            self.write(f"Andys Rips 2019/{artist}/01 track.mp3",
                       SILENT_MP3 + bytes([index]))
            self._tagged(f"Sorted/{artist}/Real Album/01 song.mp3", artist,
                         "Real Album", "Song", 1, SILENT_MP3 + bytes([index + 50]))

        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertFalse(any(o.startswith("Andys Rips 2019/") for o in outputs))
        self.assertIn("Nirvana/Singles/01 - Track.mp3", outputs)
        self.assertIn("Nirvana/Real Album/01 - Song.mp3", outputs)

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_a_tagged_artist_is_never_called_a_container(self):
        # A prolific artist has the same shape as a container. The tags settle it.
        for index in range(1, 10):
            self._tagged(f"Frank Zappa/Album {index:02d}/01 song.mp3", "Frank Zappa",
                         f"Album {index:02d}", f"Song {index}", 1,
                         SILENT_MP3 + bytes([index]))

        organizer = self.build()
        organizer.organize()

        artists = {o.split("/")[0] for o in self.outputs(organizer)}
        self.assertEqual(artists, {"Frank Zappa"})

    def test_untagged_prolific_artist_is_left_alone(self):
        # No tags anywhere, so there is no sound way to tell this from a
        # container. The safe answer is to leave it as one artist rather than
        # shatter it into one "artist" per album.
        for index in range(1, 13):
            self.write(f"Frank Zappa/Album {index:02d}/01 song.mp3",
                       SILENT_MP3 + bytes([index]))

        organizer = self.build()
        organizer.organize()

        artists = {o.split("/")[0] for o in self.outputs(organizer)}
        self.assertEqual(artists, {"Frank Zappa"})

    @unittest.skipUnless(MUTAGEN, "mutagen required")
    def test_self_titled_album_survives(self):
        self._tagged("Weezer/Weezer/01 song.mp3", "Weezer", "Weezer", "Undone", 1,
                     SILENT_MP3 + b"\x01")
        self._tagged("Weezer/Pinkerton/01 song.mp3", "Weezer", "Pinkerton",
                     "El Scorcho", 1, SILENT_MP3 + b"\x02")

        organizer = self.build()
        organizer.organize()

        outputs = self.outputs(organizer)
        self.assertIn("Weezer/Weezer/01 - Undone.mp3", outputs)
        self.assertIn("Weezer/Pinkerton/01 - El Scorcho.mp3", outputs)

    def test_detection_can_be_disabled(self):
        self.write("Andys Rips 2019/Nirvana/01 track.mp3")
        organizer = self.build()
        organizer.config.set("CLASSIFY", "detect_containers", "false")
        organizer.organize()

        self.assertEqual(organizer.detected_containers, set())


class TestDuplicateCollectionGuard(OrganizerTestCase):
    def _make_collection(self, name="Music_Organized"):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        collection = self.root / name
        organizer = AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini", output_path=collection
        )
        organizer.organize()
        return collection

    def test_refuses_to_copy_a_collection_into_a_new_one(self):
        collection = self._make_collection("My Organized Music")
        second = self.root / "Second_Copy"

        organizer = AdvancedMusicOrganizer(
            collection, config_file=self.root / "config2.ini", output_path=second
        )
        organizer.organize()

        self.assertFalse(second.exists())
        self.assertEqual(organizer.stats["organized"], 0)

    def test_in_place_rerun_is_allowed(self):
        collection = self._make_collection()

        organizer = AdvancedMusicOrganizer(
            collection, config_file=self.root / "config2.ini", output_path=collection
        )
        organizer.organize()

        # Allowed, and a no-op because everything is already in the index.
        self.assertEqual(organizer.stats["organized"], 0)
        self.assertEqual(organizer.stats["already_in_library"], 1)

    def test_override_with_assume_yes(self):
        collection = self._make_collection("My Organized Music")
        second = self.root / "Second_Copy"

        organizer = AdvancedMusicOrganizer(
            collection, config_file=self.root / "config2.ini", output_path=second
        )
        organizer.organize(assume_yes=True)

        self.assertEqual(organizer.stats["organized"], 1)

    def test_a_plain_music_folder_is_not_mistaken_for_a_collection(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()

        self.assertFalse(organizer.looks_like_a_collection(self.source))
        organizer.organize()
        self.assertEqual(organizer.stats["organized"], 1)


class TestSpecialFolderNames(OrganizerTestCase):
    def test_tool_folders_sort_above_the_artists(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3", SILENT_MP3 + b"\x01")
        self.write("09182734.mp3", SILENT_MP3 + b"\x02")

        organizer = self.build()
        organizer.organize()

        names = sorted(p.name for p in organizer.organized_path.iterdir() if p.is_dir())
        tool = [n for n in names if n.startswith("__")]
        artists = [n for n in names if not n.startswith("__")]

        self.assertEqual(tool, [music_organizer.MANUAL_SORT,
                                music_organizer.REPORTS_FOLDER])
        self.assertEqual(artists, ["Nirvana"])

        # The affix is what does the work. Note that "_" sorts *after* A-Z in
        # raw codepoint order, so a byte-wise sort is the wrong thing to assert:
        # file managers use a natural-language comparison (StrCmpLogicalW on
        # Windows) that places punctuation before alphanumerics, which is what
        # actually floats these folders to the top of the listing.
        for name in tool:
            self.assertTrue(name.startswith("__") and name.endswith("__"))

    def test_names_are_configurable(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.config.set("FOLDERS", "reports_folder", "ZZ_Diagnostics")
        organizer.organize()

        self.assertTrue((organizer.organized_path / "ZZ_Diagnostics"
                         / "report.html").exists())


class TestLegacyFolderMigration(OrganizerTestCase):
    def _collection_with_legacy(self):
        collection = self.root / "Music_Organized"
        (collection / "Reports").mkdir(parents=True)
        (collection / "Reports" / "organization_summary.txt").write_text(
            "old", encoding="utf-8"
        )
        (collection / "Playlists").mkdir()
        (collection / "Playlists" / "genre_Rock.m3u").write_text("#EXTM3U", encoding="utf-8")
        (collection / "Manual Sort Needed").mkdir()
        (collection / "Manual Sort Needed" / "mystery.mp3").write_bytes(SILENT_MP3)
        return collection

    def _organizer_for(self, collection):
        return AdvancedMusicOrganizer(
            self.source, config_file=self.root / "config.ini", output_path=collection
        )

    def test_legacy_folders_are_renamed(self):
        collection = self._collection_with_legacy()
        organizer = self._organizer_for(collection)

        self.assertEqual(organizer.migrate_legacy_folders(), 3)

        self.assertTrue((collection / music_organizer.REPORTS_FOLDER
                         / "organization_summary.txt").exists())
        self.assertTrue((collection / music_organizer.PLAYLISTS_FOLDER
                         / "genre_Rock.m3u").exists())
        self.assertTrue((collection / music_organizer.MANUAL_SORT
                         / "mystery.mp3").exists())
        self.assertFalse((collection / "Reports").exists())

    def test_migration_runs_during_organize(self):
        collection = self._collection_with_legacy()
        self.write("Nirvana/Nevermind/01 Lithium.mp3")

        organizer = self._organizer_for(collection)
        organizer.organize()

        self.assertFalse((collection / "Reports").exists())
        self.assertTrue((collection / music_organizer.REPORTS_FOLDER).exists())

    def test_a_band_called_reports_is_not_touched(self):
        collection = self.root / "Music_Organized"
        (collection / "Reports").mkdir(parents=True)
        (collection / "Reports" / "Album").mkdir()
        (collection / "Reports" / "Album" / "01 Song.mp3").write_bytes(SILENT_MP3)

        organizer = self._organizer_for(collection)

        self.assertEqual(organizer.migrate_legacy_folders(), 0)
        self.assertTrue((collection / "Reports" / "Album" / "01 Song.mp3").exists())

    def test_migration_does_not_clobber_an_existing_new_folder(self):
        collection = self._collection_with_legacy()
        (collection / music_organizer.REPORTS_FOLDER).mkdir()
        (collection / music_organizer.REPORTS_FOLDER / "keep.txt").write_text(
            "new", encoding="utf-8"
        )

        organizer = self._organizer_for(collection)
        organizer.migrate_legacy_folders()

        self.assertTrue((collection / "Reports").exists())
        self.assertEqual(
            (collection / music_organizer.REPORTS_FOLDER / "keep.txt").read_text(
                encoding="utf-8"),
            "new",
        )

    def test_cli_migrates_before_it_creates_the_log_folder(self):
        # Regression: attaching the log file creates the reports folder, and a
        # pre-existing destination is treated as "do not clobber" -- so if the
        # log is attached first, the old folder is never renamed and the
        # collection ends up with both.
        collection = self._collection_with_legacy()
        self.write("Nirvana/Nevermind/01 Lithium.mp3")

        try:
            music_organizer.main([
                str(self.source), "--output", str(collection),
                "--config", str(self.root / "config.ini"), "--quiet",
            ])
        finally:
            for handler in list(music_organizer.LOG.handlers):
                handler.close()
                music_organizer.LOG.removeHandler(handler)

        self.assertFalse((collection / "Reports").exists())
        self.assertFalse((collection / "Playlists").exists())
        self.assertFalse((collection / "Manual Sort Needed").exists())
        self.assertTrue((collection / music_organizer.REPORTS_FOLDER
                         / "report.html").exists())

    def test_migration_is_a_no_op_on_a_fresh_collection(self):
        self.write("Nirvana/Nevermind/01 Lithium.mp3")
        organizer = self.build()
        organizer.organize()

        self.assertEqual(organizer.migrate_legacy_folders(), 0)


class TestManualFolder(OrganizerTestCase):
    def test_manual_folder_files_are_moved_and_renamed(self):
        collection = self.root / "Music_Organized"
        manual = collection / music_organizer.MANUAL_SORT
        manual.mkdir(parents=True)
        (manual / "Nirvana - Lithium.mp3").write_bytes(SILENT_MP3)
        (manual / "09182734.mp3").write_bytes(SILENT_MP3 + b"\x07")

        music_organizer.organize_manual_folder(manual, collection)

        self.assertTrue((collection / "Nirvana" / "Singles" / "Lithium.mp3").exists())
        self.assertTrue((manual / "09182734.mp3").exists())


if __name__ == "__main__":
    unittest.main()
