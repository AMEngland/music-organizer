#!/usr/bin/env python3
"""Tests for the self-contained HTML report."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import report_html  # noqa: E402


def minimal_context(**overrides):
    context = {
        "run_id": 7,
        "version": "2.1.0",
        "dry_run": False,
        "collection": r"D:\Music_Organized",
        "stats": {"total_files": 3, "organized": 2, "duplicates_skipped": 1},
        "duplicates": [],
        "near_duplicates": [],
        "review": [],
        "unclassified": [],
        "artist_merges": {},
        "quality": {},
        "errors": [],
    }
    context.update(overrides)
    return context


class TestRenderReport(unittest.TestCase):
    def test_produces_a_complete_document(self):
        page = report_html.render_report(minimal_context())
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("</html>", page)
        self.assertIn("Music Organizer report", page)

    def test_is_self_contained(self):
        page = report_html.render_report(minimal_context())
        # No external stylesheets, scripts, images or fonts.
        self.assertNotIn("<script", page)
        self.assertNotIn("<link", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)

    def test_stats_are_rendered(self):
        page = report_html.render_report(minimal_context())
        self.assertIn("Files scanned", page)
        self.assertIn("Duplicates skipped", page)

    def test_unknown_stat_keys_are_ignored(self):
        page = report_html.render_report(
            minimal_context(stats={"total_files": 1, "some_internal_counter": 9})
        )
        self.assertNotIn("some_internal_counter", page)

    def test_duplicate_groups_and_deletion_list(self):
        page = report_html.render_report(minimal_context(duplicates=[
            ({"path": r"D:\dupe.mp3", "quality": "mp3 128kbps", "label": "A - B"},
             {"path": r"D:\keep.mp3", "quality": "flac lossless", "label": "A - B"}),
        ]))
        self.assertIn("dupe.mp3", page)
        self.assertIn("keep.mp3", page)
        self.assertIn("Redundant source files", page)

    def test_review_rows_render_a_confidence_bar(self):
        page = report_html.render_report(minimal_context(review=[
            {"confidence": 42, "artist": "Muse", "album": "Singles",
             "title": "Hysteria", "artist_source": "filename", "path": "x.mp3"},
        ]))
        self.assertIn("width:42%", page)
        self.assertIn("Muse", page)

    def test_html_in_metadata_is_escaped(self):
        page = report_html.render_report(minimal_context(review=[
            {"confidence": 10, "artist": "<script>alert(1)</script>",
             "album": "", "title": "", "artist_source": "filename", "path": "x.mp3"},
        ]))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_dry_run_is_flagged(self):
        page = report_html.render_report(minimal_context(dry_run=True))
        self.assertIn("DRY RUN", page)

    def test_empty_sections_say_so(self):
        page = report_html.render_report(minimal_context())
        self.assertIn("No duplicate audio found.", page)
        self.assertIn("No errors.", page)

    def test_artist_merges_are_listed(self):
        page = report_html.render_report(minimal_context(
            artist_merges={"The Beatles": [("The Beatles", 3), ("beatles", 1)]}
        ))
        self.assertIn("The Beatles", page)
        self.assertIn("&times;3", page)

    def test_near_duplicate_groups_show_a_count(self):
        page = report_html.render_report(minimal_context(near_duplicates=[
            {"label": "Creep", "items": [
                {"path": "a.mp3", "quality": "mp3 128kbps"},
                {"path": "b.mp3", "quality": "mp3 320kbps"},
            ]},
        ]))
        self.assertIn("2 copies", page)

    def test_style_is_inlined_once(self):
        page = report_html.render_report(minimal_context())
        self.assertEqual(len(re.findall(r"<style>", page)), 1)


if __name__ == "__main__":
    unittest.main()
