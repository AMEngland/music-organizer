#!/usr/bin/env python3
"""Tests for filename parsing, artist folding and safe output naming."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import naming  # noqa: E402


class TestParseFilename(unittest.TestCase):
    def test_spaced_dash(self):
        result = naming.parse_filename("Pink Floyd - Comfortably Numb")
        self.assertEqual(result.artist, "Pink Floyd")
        self.assertEqual(result.title, "Comfortably Numb")

    def test_leading_track_number(self):
        result = naming.parse_filename("01 - Radiohead - Creep")
        self.assertEqual(result.track, 1)
        self.assertEqual(result.artist, "Radiohead")
        self.assertEqual(result.title, "Creep")

    def test_three_part_name(self):
        result = naming.parse_filename("Nirvana - Nevermind - Lithium")
        self.assertEqual(result.artist, "Nirvana")
        self.assertEqual(result.album, "Nevermind")
        self.assertEqual(result.title, "Lithium")

    def test_track_artist_album_title(self):
        result = naming.parse_filename("05 - Nirvana - Nevermind - Lithium")
        self.assertEqual(result.track, 5)
        self.assertEqual(result.artist, "Nirvana")
        self.assertEqual(result.album, "Nevermind")
        self.assertEqual(result.title, "Lithium")

    def test_trailing_qualifier_is_not_an_album(self):
        result = naming.parse_filename("Nirvana - Lithium - Live")
        self.assertEqual(result.artist, "Nirvana")
        self.assertIsNone(result.album)
        self.assertEqual(result.title, "Lithium (Live)")

    def test_en_dash(self):
        result = naming.parse_filename("Björk – Hyperballad")
        self.assertEqual(result.artist, "Björk")
        self.assertEqual(result.title, "Hyperballad")

    def test_underscore_separator(self):
        result = naming.parse_filename("Beatles_HeyJude")
        self.assertEqual(result.artist, "Beatles")
        self.assertEqual(result.title, "HeyJude")

    def test_underscore_padded_dash(self):
        result = naming.parse_filename("Pink_Floyd_-_Comfortably_Numb")
        self.assertEqual(result.artist, "Pink Floyd")
        self.assertEqual(result.title, "Comfortably Numb")

    def test_unspaced_dash(self):
        result = naming.parse_filename("Nirvana-Lithium")
        self.assertEqual(result.artist, "Nirvana")
        self.assertEqual(result.title, "Lithium")

    def test_junk_is_stripped(self):
        result = naming.parse_filename(
            "03_-_Muse_-_Hysteria_(Official Video)_[320kbps]_www.dodgysite.com"
        )
        self.assertEqual(result.track, 3)
        self.assertEqual(result.artist, "Muse")
        self.assertEqual(result.title, "Hysteria")

    def test_live_parenthetical_is_preserved(self):
        result = naming.parse_filename("Queen - Bohemian Rhapsody (Live)")
        self.assertEqual(result.artist, "Queen")
        self.assertEqual(result.title, "Bohemian Rhapsody (Live)")

    def test_stopword_is_not_an_artist(self):
        result = naming.parse_filename("The - Wall")
        self.assertIsNone(result.artist)

    def test_number_is_not_an_artist(self):
        result = naming.parse_filename("12 - 34")
        self.assertIsNone(result.artist)

    def test_track_number_only(self):
        result = naming.parse_filename("07 Sunrise")
        self.assertEqual(result.track, 7)
        self.assertEqual(result.title, "Sunrise")
        self.assertIsNone(result.artist)

    def test_bare_title(self):
        result = naming.parse_filename("Untitled Recording")
        self.assertIsNone(result.artist)
        self.assertEqual(result.title, "Untitled Recording")


class TestArtistCanonicalization(unittest.TestCase):
    def test_leading_article_folded(self):
        self.assertEqual(
            naming.canonical_artist_key("The Beatles"),
            naming.canonical_artist_key("beatles"),
        )

    def test_trailing_article_folded(self):
        self.assertEqual(
            naming.canonical_artist_key("Beatles, The"),
            naming.canonical_artist_key("The Beatles"),
        )

    def test_accent_folded(self):
        self.assertEqual(
            naming.canonical_artist_key("Björk"),
            naming.canonical_artist_key("Bjork"),
        )

    def test_ampersand_folded(self):
        self.assertEqual(
            naming.canonical_artist_key("Simon & Garfunkel"),
            naming.canonical_artist_key("Simon and Garfunkel"),
        )

    def test_distinct_artists_stay_distinct(self):
        self.assertNotEqual(
            naming.canonical_artist_key("Wings"),
            naming.canonical_artist_key("The Who"),
        )

    def test_registry_prefers_tag_spelling(self):
        registry = naming.ArtistRegistry()
        registry.register("BEATLES", "filename")
        registry.register("The Beatles", "tags")
        self.assertEqual(registry.resolve("beatles"), "The Beatles")

    def test_registry_prefers_mixed_case_on_a_tie(self):
        registry = naming.ArtistRegistry()
        registry.register("PORTISHEAD", "tags")
        registry.register("Portishead", "tags")
        self.assertEqual(registry.resolve("portishead"), "Portishead")

    def test_registry_reports_merges(self):
        registry = naming.ArtistRegistry()
        registry.register("The Beatles", "tags")
        registry.register("beatles", "path")
        merged = registry.merged_groups()
        self.assertEqual(len(merged), 1)


class TestSmartTitlecase(unittest.TestCase):
    def test_shouted_name_is_recased(self):
        self.assertEqual(naming.smart_titlecase("PINK FLOYD"), "Pink Floyd")

    def test_whispered_name_is_recased(self):
        self.assertEqual(naming.smart_titlecase("pink floyd"), "Pink Floyd")

    def test_deliberate_mixed_case_is_left_alone(self):
        self.assertEqual(naming.smart_titlecase("iamamiwhoami"), "iamamiwhoami")
        self.assertEqual(naming.smart_titlecase("Sigur Rós"), "Sigur Rós")

    def test_vowelless_acronyms_survive(self):
        for name in ("MGMT", "TLC", "XTC", "BBC"):
            self.assertEqual(naming.smart_titlecase(name), name)

    def test_vowel_bearing_acronyms_come_from_the_exception_list(self):
        self.assertEqual(naming.smart_titlecase("ABBA"), "ABBA")
        self.assertEqual(naming.smart_titlecase("KISS"), "KISS")
        self.assertEqual(naming.smart_titlecase("R.E.M."), "R.E.M.")

    def test_exception_applies_per_word(self):
        self.assertEqual(naming.smart_titlecase("ABBA GOLD"), "ABBA Gold")

    def test_exception_restores_lowercase_styling(self):
        self.assertEqual(naming.smart_titlecase("DEADMAU5"), "deadmau5")

    def test_small_words_lowercase_in_the_middle_only(self):
        self.assertEqual(
            naming.smart_titlecase("THE DARK SIDE OF THE MOON"),
            "The Dark Side of the Moon",
        )

    def test_short_common_words_are_not_treated_as_acronyms(self):
        self.assertEqual(naming.smart_titlecase("SPEAK TO ME"), "Speak to Me")
        self.assertEqual(naming.smart_titlecase("IT IS ME"), "It Is Me")

    def test_mc_and_apostrophe_names(self):
        self.assertEqual(naming.smart_titlecase("PAUL MCCARTNEY"), "Paul McCartney")
        self.assertEqual(naming.smart_titlecase("SINEAD O'CONNOR"), "Sinead O'Connor")

    def test_user_exceptions_win(self):
        self.assertEqual(
            naming.smart_titlecase("MYBAND", exceptions=["MyBaNd"]), "MyBaNd"
        )

    def test_empty_input(self):
        self.assertEqual(naming.smart_titlecase(""), "")
        self.assertEqual(naming.smart_titlecase(None), "")


class TestRegistryTitlecase(unittest.TestCase):
    def test_registry_applies_titlecase_when_enabled(self):
        registry = naming.ArtistRegistry(titlecase=True)
        registry.register("PINK FLOYD", "tags")
        self.assertEqual(registry.resolve("pink floyd"), "Pink Floyd")

    def test_registry_leaves_names_alone_when_disabled(self):
        registry = naming.ArtistRegistry(titlecase=False)
        registry.register("PINK FLOYD", "tags")
        self.assertEqual(registry.resolve("pink floyd"), "PINK FLOYD")

    def test_registry_honours_exceptions(self):
        registry = naming.ArtistRegistry(titlecase=True, exceptions=["ACME"])
        registry.register("ACME", "tags")
        self.assertEqual(registry.resolve("acme"), "ACME")

    def test_titlecase_runs_after_election(self):
        # The properly cased tag variant still wins; casing is only a fallback.
        registry = naming.ArtistRegistry(titlecase=True)
        registry.register("PORTISHEAD", "filename")
        registry.register("Portishead", "tags")
        self.assertEqual(registry.resolve("portishead"), "Portishead")


class TestSafeComponent(unittest.TestCase):
    def test_illegal_characters_removed(self):
        self.assertEqual(naming.safe_component('AC/DC: Live?'), "ACDC Live")

    def test_reserved_name_escaped(self):
        self.assertEqual(naming.safe_component("CON"), "CON_")

    def test_trailing_dot_removed(self):
        self.assertEqual(naming.safe_component("Album."), "Album")

    def test_length_capped_on_word_boundary(self):
        long_name = "word " * 40
        result = naming.safe_component(long_name, max_length=50)
        self.assertLessEqual(len(result), 50)
        self.assertFalse(result.endswith(" "))

    def test_empty_becomes_unknown(self):
        self.assertEqual(naming.safe_component("   "), "Unknown")


class TestBuildFilename(unittest.TestCase):
    def test_track_is_zero_padded(self):
        self.assertEqual(
            naming.build_filename("Creep", ".mp3", track=3),
            "03 - Creep.mp3",
        )

    def test_no_track_uses_title_only(self):
        self.assertEqual(naming.build_filename("Creep", ".mp3"), "Creep.mp3")

    def test_multi_disc_prefix(self):
        self.assertEqual(
            naming.build_filename("Creep", ".mp3", track=3, disc=2),
            "2-03 - Creep.mp3",
        )

    def test_single_disc_has_no_prefix(self):
        self.assertEqual(
            naming.build_filename("Creep", ".mp3", track=3, disc=1),
            "03 - Creep.mp3",
        )

    def test_extension_lowercased(self):
        self.assertEqual(naming.build_filename("Creep", ".MP3"), "Creep.mp3")

    def test_illegal_characters_removed_from_title(self):
        self.assertEqual(naming.build_filename('What/Now?', ".mp3"), "WhatNow.mp3")


class TestDiscDirectory(unittest.TestCase):
    def test_cd_folder_detected(self):
        self.assertEqual(naming.disc_number_from_dirname("CD2"), 2)
        self.assertEqual(naming.disc_number_from_dirname("Disc 3"), 3)

    def test_regular_folder_is_not_a_disc(self):
        self.assertIsNone(naming.disc_number_from_dirname("Nevermind"))


if __name__ == "__main__":
    unittest.main()
