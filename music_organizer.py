#!/usr/bin/env python3
"""Advanced music organizer.

Scans a music tree, deduplicates by audio content, classifies each track from
embedded tags / directory structure / filename, and writes a normalized
``Artist/Album/NN - Title.ext`` library alongside playlists and reports.

Every run is recorded in a SQLite database inside the organized collection, so
scans are cached, repeat runs are incremental, and any run can be undone.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import logging
import os
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import naming
import state
from audio_hash import hash_audio_payload

__version__ = "2.0.0"

# Try to import optional dependencies
try:
    from mutagen import File as MutagenFile
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

try:
    from mutagen.mp4 import MP4Cover
except ImportError:
    MP4Cover = None

try:
    from mutagen.flac import Picture as FlacPicture
except ImportError:
    FlacPicture = None

MUSIC_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".wma", ".opus", ".aiff"}
LOSSLESS_EXTENSIONS = {".flac", ".wav", ".aiff", ".ape", ".wv"}

MANUAL_SORT = "Manual Sort Needed"

LOG = logging.getLogger("music_organizer")

# Directory names that describe a container, not an artist.
_GENERIC_DIRS = {
    naming.canonical_artist_key(name)
    for name in (
        "music", "my music", "mp3", "mp3s", "new mp3s", "amazon mp3", "itunes",
        "google music downloads", "converted music", "misc", "various",
        "various artists", "unknown artist", "unknown", "downloads", "download",
        "media", "audio", "albums", "songs", "tracks", "new", "sorted",
        "unsorted", "temp", "untitled", "compilations", "library",
    )
}

# Tag keys per logical field, covering ID3, MP4, Vorbis and ASF containers.
_TAG_KEYS = {
    "artist": ["TPE1", "\xa9ART", "ARTIST", "artist", "Author"],
    "albumartist": ["TPE2", "aART", "ALBUMARTIST", "albumartist", "album artist",
                    "WM/AlbumArtist"],
    "album": ["TALB", "\xa9alb", "ALBUM", "album", "WM/AlbumTitle"],
    "title": ["TIT2", "\xa9nam", "TITLE", "title"],
    "genre": ["TCON", "\xa9gen", "GENRE", "genre", "WM/Genre"],
    "year": ["TDRC", "TYER", "\xa9day", "DATE", "date", "year", "WM/Year"],
    "track": ["TRCK", "trkn", "TRACKNUMBER", "tracknumber", "WM/TrackNumber"],
    "disc": ["TPOS", "disk", "DISCNUMBER", "discnumber", "WM/PartOfSet"],
}

# Confidence weights. Tags are trusted most, then directory structure, then the
# filename; a filename pattern that hinges on a single unspaced separator is
# discounted further because it is the easiest one to get wrong.
_ARTIST_SOURCE_SCORE = {"tags": 55, "path": 38, "filename": 26, "none": 0}
_TITLE_SOURCE_SCORE = {"tags": 25, "filename": 12, "none": 0}
_WEAK_PATTERNS = {"artist_dash_title", "artist_underscore_title", "artist_comma_title"}

ROUTE_LIBRARY = "library"
ROUTE_REVIEW = "review"
ROUTE_MANUAL = "manual"


def fs_path(path: Path) -> str:
    """Return a path string safe to hand to the filesystem.

    Normalized ``Artist/Album/NN - Title.ext`` names are built from tags, which
    can be long; on Windows that pushes paths past the 260-character MAX_PATH
    limit and the copy fails. Switching to the extended-length ``\\\\?\\`` form
    lifts the limit.
    """
    text = os.path.abspath(str(path))
    if sys.platform != "win32" or len(text) < 240 or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):  # UNC share
        return "\\\\?\\UNC" + text[1:]
    return "\\\\?\\" + text


@dataclass
class TrackRecord:
    """Everything known about one source file."""

    path: Path
    size: int = 0
    mtime: float = 0.0
    extension: str = ""
    bitrate: int = 0
    audio_hash: Optional[str] = None
    hash_scope: str = "file"
    from_cache: bool = False

    # Raw values as found, before canonicalization.
    tag_artist: Optional[str] = None
    tag_albumartist: Optional[str] = None
    tag_album: Optional[str] = None
    tag_title: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None

    # Resolved classification.
    artist: Optional[str] = None
    album: Optional[str] = None
    title: Optional[str] = None
    artist_source: str = "none"
    title_source: str = "none"
    filename_pattern: str = "none"
    confidence: int = 0
    route: str = ROUTE_MANUAL

    # Filled in during planning and execution.
    target_path: Optional[Path] = None
    action: str = "copy"

    @property
    def needs_manual_sort(self) -> bool:
        return self.route == ROUTE_MANUAL

    @property
    def is_lossless(self) -> bool:
        return self.extension in LOSSLESS_EXTENSIONS

    @property
    def tag_score(self) -> int:
        """How completely tagged this file is; used to break dedupe ties."""
        return sum(
            1 for value in (
                self.tag_artist, self.tag_albumartist, self.tag_album,
                self.tag_title, self.genre, self.year, self.track_number,
            ) if value
        )

    @property
    def quality_key(self) -> Tuple:
        """Sort key electing the best copy among identical recordings."""
        return (
            1 if self.is_lossless else 0,
            self.bitrate or 0,
            self.tag_score,
            self.size,
            -len(str(self.path)),
        )

    def cacheable_tags(self) -> Dict:
        return {field: getattr(self, field) for field in state.CACHED_TAG_FIELDS}


class AdvancedMusicOrganizer:
    """Organizes a music tree into ``Artist/Album/NN - Title.ext``."""

    DEFAULTS = {
        "GENERAL": {
            "interactive_mode": "false",
            "create_playlists": "true",
            "extract_album_art": "true",
            "analyze_quality": "true",
            "find_near_duplicates": "true",
        },
        "FOLDERS": {
            "skip_folders": "System Volume Information, $RECYCLE.BIN, .Trash",
            "manual_sort_folder": MANUAL_SORT,
        },
        "NAMING": {
            "rename_files": "true",
            "filename_template": "{track} - {title}",
            "filename_template_no_track": "{title}",
            "max_component_length": "100",
            "titlecase_names": "true",
            "name_exceptions": "",
        },
        "CLASSIFY": {
            "review_threshold": "55",
            "route_review": "false",
            "review_folder": "Review",
        },
        "QUALITY": {
            "min_bitrate": "128",
            "preferred_format": "mp3",
            "report_low_quality": "true",
        },
        "PLAYLISTS": {
            "create_by_genre": "true",
            "create_by_year": "true",
            "create_by_artist": "false",
            "min_songs_for_playlist": "5",
        },
    }

    def __init__(self, music_path: str | Path, config_file: str | Path | None = None,
                 output_path: str | Path | None = None, dry_run: bool = False) -> None:
        self.music_path = Path(music_path)
        self.organized_path = Path(output_path) if output_path else \
            self.music_path.parent / "Music_Organized"
        self.config_file = Path(config_file) if config_file else \
            self.music_path.parent / "music_organizer_config.ini"
        self.dry_run = dry_run

        self.config = self.load_config()
        self.artists = naming.ArtistRegistry(
            titlecase=self.config.getboolean("NAMING", "titlecase_names", fallback=True),
            exceptions=self._name_exceptions(),
        )

        self.database: Optional[state.LibraryDatabase] = None
        self.run_id: Optional[int] = None

        self.duplicate_groups: Dict[str, List[TrackRecord]] = {}
        self.dropped_duplicates: List[Tuple[TrackRecord, TrackRecord]] = []
        self.near_duplicates: Dict[str, List[TrackRecord]] = defaultdict(list)
        self.quality_stats: Dict[str, int] = defaultdict(int)
        self.unclassified: List[TrackRecord] = []
        self.review_queue: List[TrackRecord] = []
        self.planned: List[TrackRecord] = []

        self.stats = {
            "total_files": 0,
            "organized": 0,
            "duplicates_skipped": 0,
            "already_in_library": 0,
            "near_duplicates_found": 0,
            "folders_created": 0,
            "manual_sort": 0,
            "needs_review": 0,
            "metadata_parsed": 0,
            "classified_from_path": 0,
            "classified_from_filename": 0,
            "files_renamed": 0,
            "artists_merged": 0,
            "album_art_extracted": 0,
            "scans_from_cache": 0,
            "errors": [],
        }

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def load_config(self) -> configparser.ConfigParser:
        """Load configuration, backfilling any keys the file predates."""
        config = configparser.ConfigParser()
        config.read_dict(self.DEFAULTS)

        if self.config_file.exists():
            try:
                config.read(self.config_file)
            except configparser.Error as exc:
                LOG.warning("Could not read config (%s); using defaults", exc)

        # Re-add any section or key the file dropped entirely, so upgrading
        # never requires deleting an existing .ini.
        for section, values in self.DEFAULTS.items():
            if not config.has_section(section):
                config.add_section(section)
            for key, value in values.items():
                if not config.has_option(section, key):
                    config.set(section, key, value)

        try:
            self.save_config(config)
        except OSError as exc:
            LOG.warning("Could not write config: %s", exc)

        return config

    def save_config(self, config: configparser.ConfigParser) -> None:
        """Save configuration to file"""
        with open(self.config_file, "w", encoding="utf-8") as handle:
            config.write(handle)

    def _name_exceptions(self) -> Tuple[str, ...]:
        """User-supplied casing exceptions, on top of the built-in list."""
        raw = self.config.get("NAMING", "name_exceptions", fallback="")
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    @property
    def max_component_length(self) -> int:
        return self.config.getint("NAMING", "max_component_length", fallback=100)

    @property
    def reports_path(self) -> Path:
        return self.organized_path / "Reports"

    # ------------------------------------------------------------------
    # Persistent state
    # ------------------------------------------------------------------

    def open_database(self) -> state.LibraryDatabase:
        if self.database is None:
            self.organized_path.mkdir(parents=True, exist_ok=True)
            self.database = state.LibraryDatabase(self.organized_path / state.DB_FILENAME)
        return self.database

    def close_database(self) -> None:
        if self.database is not None:
            self.database.close()
            self.database = None

    def reindex_collection(self) -> int:
        """Rebuild the library index by hashing what is already organized.

        Needed the first time an existing collection is used with this version,
        or after files have been moved around by hand.
        """
        database = self.open_database()
        indexed = 0

        for path in sorted(self.organized_path.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in MUSIC_EXTENSIONS:
                continue
            digest = hash_audio_payload(path)
            if digest is None:
                continue
            database.add_to_library(digest.digest, path)
            indexed += 1

        LOG.info("Indexed %d files already in the collection", indexed)
        return indexed

    def _collection_has_music(self) -> bool:
        """True if the destination already holds audio files."""
        for path in self.organized_path.rglob("*"):
            if path.is_file() and path.suffix.lower() in MUSIC_EXTENSIONS:
                return True
        return False

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _skip_folder_names(self) -> set:
        raw = self.config.get("FOLDERS", "skip_folders", fallback="")
        return {part.strip().lower() for part in raw.split(",") if part.strip()}

    def get_all_music_files(self) -> List[Path]:
        """Find all music files below the scan root.

        Skip folders are matched per path *component*, so a legitimate library
        that happens to sit under a directory containing a skip word is no
        longer discarded wholesale.
        """
        skip_names = self._skip_folder_names()
        files: List[Path] = []

        for file_path in self.music_path.rglob("*"):
            try:
                if not file_path.is_file():
                    continue
            except OSError:
                continue

            if file_path.suffix.lower() not in MUSIC_EXTENSIONS:
                continue

            try:
                relative_parts = file_path.relative_to(self.music_path).parts[:-1]
            except ValueError:
                relative_parts = file_path.parts[:-1]

            if any(part.lower() in skip_names for part in relative_parts):
                continue

            files.append(file_path)

        return sorted(files)

    @staticmethod
    def _first_tag(audio_file, keys: Sequence[str]):
        """Return the first present tag value among *keys*, or None."""
        for key in keys:
            try:
                if key in audio_file:
                    value = audio_file[key]
                else:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            if value is None:
                continue
            return value
        return None

    @staticmethod
    def _as_text(value) -> Optional[str]:
        if value is None:
            return None
        # Vorbis/MP4 return lists; ID3 returns frames that stringify cleanly.
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        if isinstance(value, tuple) and value:  # MP4 trkn/disk pairs
            value = value[0]
        text = str(value).strip()
        return text or None

    @staticmethod
    def _as_number(value) -> Optional[int]:
        text = AdvancedMusicOrganizer._as_text(value)
        if not text:
            return None
        match = re.match(r"\s*(\d{1,4})", text)
        if not match:
            return None
        number = int(match.group(1))
        return number if 0 < number < 1000 else None

    def read_metadata(self, record: TrackRecord) -> None:
        """Populate *record* with embedded tag data and stream info."""
        if not MUTAGEN_AVAILABLE:
            return

        try:
            audio_file = MutagenFile(record.path)
        except Exception as exc:
            LOG.debug("Tag read failed for %s: %s", record.path, exc)
            audio_file = None

        if audio_file is None:
            return

        record.bitrate = int(getattr(getattr(audio_file, "info", None), "bitrate", 0) or 0)

        record.tag_artist = self._as_text(self._first_tag(audio_file, _TAG_KEYS["artist"]))
        record.tag_albumartist = self._as_text(
            self._first_tag(audio_file, _TAG_KEYS["albumartist"])
        )
        record.tag_album = self._as_text(self._first_tag(audio_file, _TAG_KEYS["album"]))
        record.tag_title = self._as_text(self._first_tag(audio_file, _TAG_KEYS["title"]))
        record.track_number = self._as_number(self._first_tag(audio_file, _TAG_KEYS["track"]))
        record.disc_number = self._as_number(self._first_tag(audio_file, _TAG_KEYS["disc"]))

        genre_value = self._first_tag(audio_file, _TAG_KEYS["genre"])
        # ID3 TCON may hold numeric references like "(13)"; mutagen expands
        # those through .genres, so prefer it over the raw text.
        expanded = getattr(genre_value, "genres", None)
        record.genre = self._as_text(expanded[0] if expanded else genre_value)

        year_text = self._as_text(self._first_tag(audio_file, _TAG_KEYS["year"]))
        if year_text:
            match = re.search(r"(\d{4})", year_text)
            record.year = match.group(1) if match else None

    def scan_file(self, path: Path) -> Optional[TrackRecord]:
        """Hash and read one file, using the scan cache when it is still valid."""
        try:
            stat_result = path.stat()
        except OSError as exc:
            self.record_error(f"Cannot stat {path}: {exc}")
            return None

        record = TrackRecord(
            path=path,
            size=stat_result.st_size,
            mtime=stat_result.st_mtime,
            extension=path.suffix.lower(),
        )

        cached = None
        if self.database is not None:
            cached = self.database.cached_scan(path, record.mtime, record.size)

        if cached:
            record.audio_hash = cached["audio_hash"]
            record.hash_scope = cached["hash_scope"]
            record.bitrate = cached["bitrate"]
            for field_name in state.CACHED_TAG_FIELDS:
                setattr(record, field_name, cached.get(field_name))
            record.from_cache = True
            self.stats["scans_from_cache"] += 1
        else:
            digest = hash_audio_payload(path)
            if digest is None:
                self.record_error(f"Cannot hash {path}")
                return None
            record.audio_hash = digest.digest
            record.hash_scope = digest.scope
            self.read_metadata(record)

            if self.database is not None:
                try:
                    self.database.store_scan(
                        path, record.mtime, record.size, record.audio_hash,
                        record.hash_scope, record.bitrate, record.cacheable_tags(),
                    )
                except Exception as exc:  # pragma: no cover - cache is best effort
                    LOG.debug("Could not cache scan for %s: %s", path, exc)

        self.analyze_quality(record)
        return record

    def record_error(self, message: str) -> None:
        """Log an error and keep it for the summary report."""
        LOG.error("%s", message)
        self.stats["errors"].append(message)

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def parse_from_path(self, path: Path) -> Tuple[Optional[str], Optional[str], Optional[int]]:
        """Infer ``(artist, album, disc)`` from directories below the scan root.

        Unlike the original implementation this is anchored on the scan root
        rather than a path component literally named ``Music``, so libraries in
        arbitrary locations are parsed correctly.
        """
        try:
            relative = path.relative_to(self.music_path)
        except ValueError:
            return None, None, None

        skip_names = self._skip_folder_names()
        disc: Optional[int] = None
        directories: List[str] = []

        for part in relative.parts[:-1]:
            if part.lower() in skip_names:
                continue
            disc_number = naming.disc_number_from_dirname(part)
            if disc_number is not None:
                disc = disc_number
                continue
            if naming.canonical_artist_key(part) in _GENERIC_DIRS:
                continue
            directories.append(part)

        if not directories:
            return None, None, disc

        # The two directories closest to the file are the most reliable, which
        # handles Genre/Artist/Album trees as well as plain Artist/Album ones.
        if len(directories) >= 2:
            artist, album = directories[-2], directories[-1]
        else:
            artist, album = directories[-1], None

        if not naming.looks_like_artist(artist):
            return None, None, disc

        return naming.normalize_title(artist), naming.normalize_title(album or "") or None, disc

    def score_confidence(self, record: TrackRecord) -> int:
        """Score how much to trust a classification, 0-100."""
        if record.artist_source == "none":
            return 0

        score = _ARTIST_SOURCE_SCORE.get(record.artist_source, 0)
        score += _TITLE_SOURCE_SCORE.get(record.title_source, 0)

        if record.artist_source == "filename" and record.filename_pattern in _WEAK_PATTERNS:
            score -= 8
        if record.album and record.album != "Singles":
            score += 10
        if record.track_number:
            score += 5
        if record.genre or record.year:
            score += 3

        return max(0, min(100, score))

    def classify(self, record: TrackRecord) -> None:
        """Resolve artist / album / title by merging every available source.

        Sources are consulted in descending reliability -- embedded tags, then
        directory structure, then the filename -- and merged field by field, so
        a file with a usable artist but no album is filed under ``Singles``
        instead of being dumped into manual sort.
        """
        path_artist, path_album, path_disc = self.parse_from_path(record.path)
        parsed = naming.parse_filename(record.path.stem)
        record.filename_pattern = parsed.pattern

        tag_artist = record.tag_albumartist or record.tag_artist

        if tag_artist:
            artist, artist_source = tag_artist, "tags"
        elif path_artist:
            artist, artist_source = path_artist, "path"
        elif parsed.artist:
            artist, artist_source = parsed.artist, "filename"
        else:
            artist, artist_source = None, "none"

        if record.tag_title:
            title, title_source = record.tag_title, "tags"
        elif parsed.title:
            title, title_source = parsed.title, "filename"
        else:
            title, title_source = record.path.stem, "filename"

        album = record.tag_album or path_album or parsed.album

        if record.track_number is None:
            record.track_number = parsed.track
        if record.disc_number is None:
            record.disc_number = path_disc

        record.title = naming.normalize_title(title) or "Unknown"
        record.title_source = title_source
        record.artist_source = artist_source

        if artist:
            record.artist = naming.normalize_title(artist)
            record.album = naming.normalize_title(album or "") or "Singles"
            self.artists.register(record.artist, artist_source)
            if artist_source == "tags":
                self.stats["metadata_parsed"] += 1
            elif artist_source == "path":
                self.stats["classified_from_path"] += 1
            else:
                self.stats["classified_from_filename"] += 1
        else:
            record.artist = None
            record.album = None

        record.confidence = self.score_confidence(record)
        threshold = self.config.getint("CLASSIFY", "review_threshold", fallback=55)

        if not artist:
            record.route = ROUTE_MANUAL
            self.unclassified.append(record)
            self.stats["manual_sort"] += 1
        elif record.confidence < threshold:
            record.route = ROUTE_REVIEW
            self.review_queue.append(record)
            self.stats["needs_review"] += 1
        else:
            record.route = ROUTE_LIBRARY

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_quality(self, record: TrackRecord) -> None:
        """Record format and bitrate statistics for the quality report."""
        if not self.config.getboolean("GENERAL", "analyze_quality", fallback=True):
            return

        self.quality_stats[f"format_{record.extension}"] += 1
        min_bitrate = self.config.getint("QUALITY", "min_bitrate", fallback=128) * 1000

        if record.is_lossless:
            self.quality_stats["lossless"] += 1
        elif record.bitrate:
            if record.bitrate < min_bitrate:
                self.quality_stats["low_quality"] += 1
            elif record.bitrate >= 320000:
                self.quality_stats["high_quality"] += 1
            else:
                self.quality_stats["medium_quality"] += 1

    def check_near_duplicates(self, records: Iterable[TrackRecord]) -> None:
        """Group surviving tracks that look like the same song."""
        if not self.config.getboolean("GENERAL", "find_near_duplicates", fallback=True):
            return

        for record in records:
            if not (record.artist and record.title):
                continue
            key = f"{naming.canonical_artist_key(record.artist)}::{_title_key(record.title)}"
            self.near_duplicates[key].append(record)

    def deduplicate(self, records: Sequence[TrackRecord]) -> List[TrackRecord]:
        """Keep the best copy of each distinct recording.

        Grouping is by audio-payload hash, so copies that differ only in their
        tags collapse together; the survivor is elected by quality rather than
        by scan order.
        """
        groups: Dict[str, List[TrackRecord]] = defaultdict(list)
        for record in records:
            groups[record.audio_hash].append(record)

        kept: List[TrackRecord] = []
        for digest, members in groups.items():
            members.sort(key=lambda item: item.quality_key, reverse=True)
            winner = members[0]
            kept.append(winner)
            if len(members) > 1:
                self.duplicate_groups[digest] = members
                for loser in members[1:]:
                    self.dropped_duplicates.append((loser, winner))
                    self.stats["duplicates_skipped"] += 1

        return kept

    def drop_already_organized(self, records: Sequence[TrackRecord]) -> List[TrackRecord]:
        """Filter out recordings the collection already holds.

        This is what makes repeat runs idempotent and ``--incremental`` cheap.
        """
        if self.database is None:
            return list(records)

        known = self.database.known_hashes()
        if not known:
            return list(records)

        remaining: List[TrackRecord] = []
        for record in records:
            existing = known.get(record.audio_hash)
            if existing and os.path.exists(fs_path(Path(existing))):
                self.stats["already_in_library"] += 1
                LOG.debug("Already in library: %s (as %s)", record.path, existing)
            else:
                remaining.append(record)
        return remaining

    # ------------------------------------------------------------------
    # Album art
    # ------------------------------------------------------------------

    @staticmethod
    def _embedded_artwork(audio_file) -> Optional[Tuple[bytes, str]]:
        """Return ``(data, mime)`` for embedded artwork, or None.

        Handles the three real-world shapes: FLAC/Ogg picture blocks, ID3
        ``APIC`` frames (whose hash keys carry a description suffix, which is
        why a plain ``'APIC:' in tags`` test misses most files), and MP4
        ``covr`` atoms.
        """
        pictures = getattr(audio_file, "pictures", None)
        if pictures:
            return pictures[0].data, getattr(pictures[0], "mime", "image/jpeg")

        tags = getattr(audio_file, "tags", None)
        if tags is None:
            return None

        getall = getattr(tags, "getall", None)
        if callable(getall):
            frames = getall("APIC")
            if frames:
                return frames[0].data, getattr(frames[0], "mime", "image/jpeg")

        try:
            if "covr" in tags and tags["covr"]:
                cover = tags["covr"][0]
                mime = "image/jpeg"
                if MP4Cover is not None and getattr(cover, "imageformat", None) == MP4Cover.FORMAT_PNG:
                    mime = "image/png"
                return bytes(cover), mime
        except (KeyError, TypeError, ValueError):
            pass

        try:
            if FlacPicture is not None and "metadata_block_picture" in tags:
                import base64

                picture = FlacPicture(base64.b64decode(tags["metadata_block_picture"][0]))
                return picture.data, picture.mime
        except Exception as exc:
            LOG.debug("Vorbis picture decode failed: %s", exc)

        return None

    def extract_album_art(self, source: Path, target_dir: Path) -> None:
        """Write embedded artwork into *target_dir* as ``folder.jpg``/``.png``.

        Only called once the destination is known and populated, so no empty
        directories are left behind when a file carries no artwork.
        """
        if not MUTAGEN_AVAILABLE or self.dry_run:
            return
        if not self.config.getboolean("GENERAL", "extract_album_art", fallback=True):
            return
        if any(os.path.exists(fs_path(target_dir / name))
               for name in ("folder.jpg", "folder.png")):
            return

        try:
            audio_file = MutagenFile(source)
            if audio_file is None:
                return
            artwork = self._embedded_artwork(audio_file)
            if not artwork:
                return

            data, mime = artwork
            if not data:
                return
            extension = ".png" if "png" in (mime or "").lower() else ".jpg"
            with open(fs_path(target_dir / f"folder{extension}"), "wb") as handle:
                handle.write(data)
            self.stats["album_art_extracted"] += 1
        except Exception as exc:
            self.record_error(f"Album art failed for {source}: {exc}")

    # ------------------------------------------------------------------
    # Planning and execution
    # ------------------------------------------------------------------

    def _present(self, text: Optional[str]) -> str:
        """Apply the optional title-case pass to an album or track title."""
        if not text:
            return ""
        if not self.config.getboolean("NAMING", "titlecase_names", fallback=True):
            return text
        return naming.smart_titlecase(text, self._name_exceptions())

    def plan_targets(self, records: Sequence[TrackRecord]) -> Dict[str, List[TrackRecord]]:
        """Assign a destination directory and filename to every record."""
        manual_dir = self.organized_path / self.config.get(
            "FOLDERS", "manual_sort_folder", fallback=MANUAL_SORT
        )
        review_dir = self.organized_path / self.config.get(
            "CLASSIFY", "review_folder", fallback="Review"
        )
        route_review = self.config.getboolean("CLASSIFY", "route_review", fallback=False)

        rename = self.config.getboolean("NAMING", "rename_files", fallback=True)
        template = self.config.get("NAMING", "filename_template", fallback="{track} - {title}")
        template_no_track = self.config.get(
            "NAMING", "filename_template_no_track", fallback="{title}"
        )
        max_length = self.max_component_length

        grouped: Dict[str, List[TrackRecord]] = defaultdict(list)

        for record in records:
            if record.route == ROUTE_MANUAL:
                record.target_path = manual_dir / record.path.name
                record.action = "manual"
                grouped[MANUAL_SORT].append(record)
                continue

            # Elect the canonical spelling so name variants share one folder.
            artist = self.artists.resolve(record.artist) or record.artist
            record.artist = artist
            record.album = self._present(record.album) or "Singles"
            record.title = self._present(record.title) or record.title

            artist_dir = naming.safe_component(artist, max_length)
            album_dir = naming.safe_component(record.album, max_length)

            base = review_dir if (route_review and record.route == ROUTE_REVIEW) \
                else self.organized_path
            target_dir = base / artist_dir / album_dir

            if rename:
                filename = naming.build_filename(
                    title=record.title or record.path.stem,
                    extension=record.extension,
                    track=record.track_number,
                    disc=record.disc_number,
                    artist=artist,
                    template=template,
                    template_without_track=template_no_track,
                    max_length=max_length,
                )
                if filename != record.path.name:
                    self.stats["files_renamed"] += 1
            else:
                filename = naming.safe_component(record.path.stem, max_length) + record.extension

            record.target_path = target_dir / filename
            record.action = "copy"
            key = str(target_dir.relative_to(self.organized_path)).replace("\\", "/")
            grouped[key].append(record)

        self.stats["artists_merged"] = sum(
            len(variants) - 1 for variants in self.artists.merged_groups().values()
        )
        self.planned = list(records)
        return grouped

    def execute(self, grouped: Dict[str, List[TrackRecord]]) -> None:
        """Create the destination tree and copy files into place."""
        if self.dry_run:
            LOG.info("Dry run: no files will be written")
            return

        for group_name, members in grouped.items():
            target_dir = members[0].target_path.parent
            try:
                created = not os.path.exists(fs_path(target_dir))
                os.makedirs(fs_path(target_dir), exist_ok=True)
                if created and group_name != MANUAL_SORT:
                    self.stats["folders_created"] += 1
            except OSError as exc:
                self.record_error(f"Cannot create {target_dir}: {exc}")
                for record in members:
                    record.target_path = None
                continue

            for record in members:
                destination = self._resolve_collision(record.target_path)
                try:
                    shutil.copy2(fs_path(record.path), fs_path(destination))
                except OSError as exc:
                    self.record_error(f"Cannot copy {record.path}: {exc}")
                    record.target_path = None
                    continue

                record.target_path = destination
                self.stats["organized"] += 1
                self._record_placement("copy", record.path, destination, record.audio_hash)

            if group_name != MANUAL_SORT:
                for record in members:
                    if record.target_path is not None:
                        self.extract_album_art(record.path, target_dir)
                        break

    def _record_placement(self, action: str, source: Path, target: Path,
                          audio_hash: Optional[str]) -> None:
        """Log a file placement to the undo log and the library index."""
        if self.database is None:
            return
        try:
            if self.run_id is not None:
                self.database.record_operation(self.run_id, action, source, target, audio_hash)
            if audio_hash:
                self.database.add_to_library(audio_hash, target, source)
        except Exception as exc:  # pragma: no cover - bookkeeping is best effort
            LOG.debug("Could not record placement for %s: %s", target, exc)

    @staticmethod
    def _resolve_collision(target: Path) -> Path:
        """Return a free filename, suffixing ``Track (2).mp3`` style if needed."""
        if not os.path.exists(fs_path(target)):
            return target
        stem, suffix = target.stem, target.suffix
        counter = 2
        while True:
            candidate = target.with_name(f"{stem} ({counter}){suffix}")
            if not os.path.exists(fs_path(candidate)):
                return candidate
            counter += 1

    # ------------------------------------------------------------------
    # Output artefacts
    # ------------------------------------------------------------------

    PLAN_COLUMNS = [
        "action", "route", "confidence", "source_path", "target_path",
        "artist", "album", "title", "track", "disc",
        "artist_source", "title_source", "filename_pattern",
        "audio_hash", "hash_scope", "bitrate", "size", "reason",
    ]

    def write_plan_csv(self, records: Sequence[TrackRecord]) -> Path:
        """Write the full plan -- including skipped duplicates -- as CSV."""
        self.reports_path.mkdir(parents=True, exist_ok=True)
        plan_file = self.reports_path / "plan.csv"

        with open(plan_file, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.PLAN_COLUMNS)
            writer.writeheader()

            for record in records:
                writer.writerow({
                    "action": record.action,
                    "route": record.route,
                    "confidence": record.confidence,
                    "source_path": str(record.path),
                    "target_path": str(record.target_path) if record.target_path else "",
                    "artist": record.artist or "",
                    "album": record.album or "",
                    "title": record.title or "",
                    "track": record.track_number or "",
                    "disc": record.disc_number or "",
                    "artist_source": record.artist_source,
                    "title_source": record.title_source,
                    "filename_pattern": record.filename_pattern,
                    "audio_hash": record.audio_hash or "",
                    "hash_scope": record.hash_scope,
                    "bitrate": record.bitrate,
                    "size": record.size,
                    "reason": "",
                })

            for loser, winner in self.dropped_duplicates:
                writer.writerow({
                    "action": "skip-duplicate",
                    "route": "",
                    "confidence": "",
                    "source_path": str(loser.path),
                    "target_path": "",
                    "artist": loser.tag_artist or "",
                    "album": loser.tag_album or "",
                    "title": loser.tag_title or "",
                    "track": loser.track_number or "",
                    "disc": loser.disc_number or "",
                    "artist_source": "",
                    "title_source": "",
                    "filename_pattern": "",
                    "audio_hash": loser.audio_hash or "",
                    "hash_scope": loser.hash_scope,
                    "bitrate": loser.bitrate,
                    "size": loser.size,
                    "reason": f"duplicate of {winner.path}",
                })

        LOG.info("Plan written to %s", plan_file)
        return plan_file

    REVIEW_COLUMNS = ["current_path", "confidence", "artist", "album", "title", "track", "notes"]

    def write_review_csv(self) -> Optional[Path]:
        """Write the low-confidence and unclassified pile as an editable CSV.

        Correct the artist/album/title columns in a spreadsheet and feed the
        file back with ``--apply-csv`` to file everything in one pass.
        """
        rows = self.review_queue + self.unclassified
        if not rows:
            return None

        self.reports_path.mkdir(parents=True, exist_ok=True)
        review_file = self.reports_path / "review_queue.csv"

        with open(review_file, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.REVIEW_COLUMNS)
            writer.writeheader()
            for record in sorted(rows, key=lambda item: item.confidence):
                location = record.target_path or record.path
                writer.writerow({
                    "current_path": str(location),
                    "confidence": record.confidence,
                    "artist": record.artist or "",
                    "album": record.album or "",
                    "title": record.title or "",
                    "track": record.track_number or "",
                    "notes": f"artist from {record.artist_source}, "
                             f"title from {record.title_source}",
                })

        LOG.info("Review queue written to %s", review_file)
        return review_file

    def create_playlists(self, records: Sequence[TrackRecord]) -> None:
        """Create M3U playlists"""
        if self.dry_run:
            return
        if not self.config.getboolean("GENERAL", "create_playlists", fallback=True):
            return

        playlist_dir = self.organized_path / "Playlists"

        by_genre: Dict[str, List[TrackRecord]] = defaultdict(list)
        by_year: Dict[str, List[TrackRecord]] = defaultdict(list)
        by_artist: Dict[str, List[TrackRecord]] = defaultdict(list)

        for record in records:
            if record.target_path is None or record.route == ROUTE_MANUAL:
                continue
            if record.genre:
                by_genre[record.genre].append(record)
            if record.year:
                by_year[record.year].append(record)
            if record.artist:
                by_artist[record.artist].append(record)

        min_songs = self.config.getint("PLAYLISTS", "min_songs_for_playlist", fallback=5)
        playlist_types = [
            ("genre", by_genre, self.config.getboolean("PLAYLISTS", "create_by_genre", fallback=True)),
            ("year", by_year, self.config.getboolean("PLAYLISTS", "create_by_year", fallback=True)),
            ("artist", by_artist, self.config.getboolean("PLAYLISTS", "create_by_artist", fallback=False)),
        ]

        for playlist_type, data, enabled in playlist_types:
            if not enabled:
                continue
            for key, members in data.items():
                if len(members) < min_songs:
                    continue
                playlist_file = playlist_dir / f"{playlist_type}_{naming.safe_component(key)}.m3u"
                try:
                    # Created lazily so an empty Playlists folder is never left behind.
                    playlist_dir.mkdir(parents=True, exist_ok=True)
                    with open(playlist_file, "w", encoding="utf-8") as handle:
                        handle.write("#EXTM3U\n")
                        for record in members:
                            relative = record.target_path.relative_to(self.organized_path)
                            # M3U wants forward slashes on every platform.
                            handle.write("../" + relative.as_posix() + "\n")
                except (OSError, ValueError) as exc:
                    self.record_error(f"Cannot write {playlist_file}: {exc}")

    def generate_reports(self, records: Sequence[TrackRecord]) -> None:
        """Generate analysis reports"""
        reports_dir = self.reports_path
        reports_dir.mkdir(parents=True, exist_ok=True)

        if self.config.getboolean("GENERAL", "analyze_quality", fallback=True):
            with open(reports_dir / "quality_report.txt", "w", encoding="utf-8") as handle:
                handle.write("AUDIO QUALITY ANALYSIS\n")
                handle.write("=" * 40 + "\n\n")
                for key, count in sorted(self.quality_stats.items()):
                    handle.write(f"{key}: {count}\n")
                low = self.quality_stats.get("low_quality", 0)
                if low:
                    threshold = self.config.getint("QUALITY", "min_bitrate", fallback=128)
                    handle.write(f"\nRecommendation: {low} files below {threshold}kbps\n")

        with open(reports_dir / "duplicates.txt", "w", encoding="utf-8") as handle:
            handle.write("EXACT AUDIO DUPLICATES (tag differences ignored)\n")
            handle.write("=" * 60 + "\n\n")
            if not self.dropped_duplicates:
                handle.write("None found.\n")
            for loser, winner in self.dropped_duplicates:
                handle.write(f"Skipped: {loser.path}\n")
                handle.write(f"  kept:  {winner.path}\n")
                handle.write(
                    f"  reason: {_quality_summary(winner)} beats {_quality_summary(loser)}\n\n"
                )

        if self.config.getboolean("GENERAL", "find_near_duplicates", fallback=True):
            with open(reports_dir / "near_duplicates.txt", "w", encoding="utf-8") as handle:
                handle.write("NEAR-DUPLICATE ANALYSIS\n")
                handle.write("=" * 40 + "\n\n")
                for key, members in sorted(self.near_duplicates.items()):
                    if len(members) < 2:
                        continue
                    handle.write(f"\nPossible duplicates for '{key}':\n")
                    for record in members:
                        handle.write(f"  - {record.path} ({_quality_summary(record)})\n")
                    self.stats["near_duplicates_found"] += len(members) - 1

        with open(reports_dir / "unclassified.txt", "w", encoding="utf-8") as handle:
            handle.write("FILES REQUIRING MANUAL SORTING\n")
            handle.write("=" * 40 + "\n\n")
            if not self.unclassified:
                handle.write("None - everything was classified.\n")
            for record in self.unclassified:
                handle.write(f"{record.path}\n")

        merged = self.artists.merged_groups()
        if merged:
            with open(reports_dir / "artist_merges.txt", "w", encoding="utf-8") as handle:
                handle.write("ARTIST NAME VARIANTS MERGED\n")
                handle.write("=" * 40 + "\n\n")
                for key, variants in sorted(merged.items()):
                    canonical = self.artists.resolve(next(iter(variants)))
                    handle.write(f"{canonical}:\n")
                    for variant, count in variants.most_common():
                        handle.write(f"  - {variant} ({count} file(s))\n")
                    handle.write("\n")

        with open(reports_dir / "organization_summary.txt", "w", encoding="utf-8") as handle:
            handle.write("MUSIC ORGANIZATION SUMMARY\n")
            handle.write("=" * 40 + "\n")
            handle.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            handle.write(f"Version: {__version__}\n")
            handle.write(f"Run ID: {self.run_id}\n")
            handle.write(f"Dry run: {self.dry_run}\n\n")
            for key, value in self.stats.items():
                if key == "errors":
                    continue
                handle.write(f"{key.replace('_', ' ').title()}: {value}\n")
            if self.stats["errors"]:
                handle.write("\nERRORS\n")
                handle.write("-" * 40 + "\n")
                for message in self.stats["errors"]:
                    handle.write(f"{message}\n")

    # ------------------------------------------------------------------
    # Interactive preview
    # ------------------------------------------------------------------

    def interactive_preview(self, grouped: Dict[str, List[TrackRecord]]) -> bool:
        """Show preview of organization in interactive mode"""
        if self.dry_run:
            return True
        if not self.config.getboolean("GENERAL", "interactive_mode", fallback=False):
            return True

        print("\nPREVIEW OF ORGANIZATION:")
        print("=" * 50)

        for key, members in list(sorted(grouped.items()))[:10]:
            print(f"\n{key}:")
            for record in members[:3]:
                print(f"  {record.path.name}")
                print(f"    -> {record.target_path.name}  (confidence {record.confidence})")
            if len(members) > 3:
                print(f"  ... and {len(members) - 3} more")

        if len(grouped) > 10:
            print(f"\n... and {len(grouped) - 10} more folders")

        print(f"\nDuplicates to skip: {self.stats['duplicates_skipped']}")
        print(f"Already in library: {self.stats['already_in_library']}")
        print(f"Low confidence:     {self.stats['needs_review']}")
        print(f"Needing manual sort:{self.stats['manual_sort']}")

        return input("\nProceed with organization? (y/n): ").lower().startswith("y")

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------

    def organize(self, reindex: bool = False) -> None:
        """Main organization function"""
        try:
            self._organize(reindex)
        finally:
            # Release the SQLite handle; callers may delete the collection.
            self.close_database()

    def _organize(self, reindex: bool = False) -> None:
        LOG.info("Advanced Music Organizer %s", __version__)

        if not MUTAGEN_AVAILABLE:
            LOG.warning("Install 'mutagen' for metadata reading")

        if not self.music_path.exists():
            LOG.error("Source folder not found: %s", self.music_path)
            return

        self.organized_path.mkdir(parents=True, exist_ok=True)
        database = self.open_database()
        self.run_id = database.start_run(self.music_path, sys.argv[1:], self.dry_run)

        if reindex or (database.library_size() == 0 and self._collection_has_music()):
            self.reindex_collection()

        all_files = self.get_all_music_files()
        LOG.info("Found %d music files", len(all_files))

        # Pass 1 - read and hash, reusing cached scans where possible.
        records: List[TrackRecord] = []
        for index, path in enumerate(all_files, start=1):
            self.stats["total_files"] += 1
            if index % 100 == 0:
                LOG.info("Scanning %d/%d...", index, len(all_files))
            record = self.scan_file(path)
            if record is not None:
                records.append(record)

        database.forget_missing_scans(all_files)

        # Pass 2 - collapse duplicate recordings, then drop anything the
        # collection already holds so repeat runs are idempotent.
        LOG.info("Deduplicating...")
        kept = self.deduplicate(records)
        kept = self.drop_already_organized(kept)
        LOG.info("%d to organize (%d duplicates, %d already present)",
                 len(kept), self.stats["duplicates_skipped"],
                 self.stats["already_in_library"])

        # Pass 3 - classify, which also populates the artist name registry.
        LOG.info("Classifying...")
        for record in kept:
            self.classify(record)
        self.check_near_duplicates(kept)

        # Pass 4 - resolve canonical names and destinations.
        grouped = self.plan_targets(kept)
        self.write_plan_csv(kept)

        if not self.interactive_preview(grouped):
            LOG.info("Organization cancelled.")
            database.finish_run(self.run_id)
            return

        if self.dry_run:
            LOG.info("Dry run - reviewing plan only")
        else:
            LOG.info("Organizing files...")
        self.execute(grouped)

        self.create_playlists(kept)
        self.generate_reports(kept)
        self.write_review_csv()

        database.finish_run(self.run_id)

        LOG.info("=" * 60)
        LOG.info("ORGANIZATION COMPLETE (run %d)%s",
                 self.run_id, " - DRY RUN, nothing written" if self.dry_run else "")
        for key, value in self.stats.items():
            if key != "errors":
                LOG.info("%s: %s", key.replace("_", " ").title(), value)

        if self.stats["errors"]:
            LOG.warning("Errors: %d (see Reports/organization_summary.txt)",
                        len(self.stats["errors"]))

        LOG.info("Output location: %s", self.organized_path)
        LOG.info("Configuration: %s", self.config_file)
        if not self.dry_run:
            LOG.info("Undo this run with: --undo %d", self.run_id)

    # ------------------------------------------------------------------
    # Corrections and undo
    # ------------------------------------------------------------------

    def apply_csv(self, csv_file: Path) -> int:
        """Re-file tracks using a corrected ``review_queue.csv``.

        Rows whose ``artist`` column is filled in are moved to the destination
        implied by their corrected fields; rows left blank are skipped.
        """
        try:
            return self._apply_csv(csv_file)
        finally:
            self.close_database()

    def _apply_csv(self, csv_file: Path) -> int:
        csv_path = Path(csv_file)
        if not csv_path.exists():
            LOG.error("CSV not found: %s", csv_path)
            return 0

        database = self.open_database()
        self.run_id = database.start_run(csv_path, sys.argv[1:], self.dry_run)

        max_length = self.max_component_length
        template = self.config.get("NAMING", "filename_template", fallback="{track} - {title}")
        template_no_track = self.config.get(
            "NAMING", "filename_template_no_track", fallback="{title}"
        )

        moved = 0
        with open(csv_path, "r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                source = Path((row.get("current_path") or "").strip())
                artist = (row.get("artist") or "").strip()

                if not artist or not source.name:
                    continue
                if not os.path.exists(fs_path(source)):
                    LOG.warning("Skipping missing file: %s", source)
                    continue

                album = (row.get("album") or "").strip() or "Singles"
                title = (row.get("title") or "").strip() or source.stem
                track_text = (row.get("track") or "").strip()
                track = int(track_text) if track_text.isdigit() else None

                target_dir = (self.organized_path
                              / naming.safe_component(self._present(artist), max_length)
                              / naming.safe_component(self._present(album), max_length))
                filename = naming.build_filename(
                    title=self._present(title), extension=source.suffix.lower(),
                    track=track, artist=artist, template=template,
                    template_without_track=template_no_track, max_length=max_length,
                )
                target = target_dir / filename
                if _same_path(source, target):
                    # Row was left unedited and the file is already correctly
                    # filed; moving it would only mint a " (2)" copy.
                    LOG.debug("Already correctly filed: %s", source)
                    continue

                destination = self._resolve_collision(target)

                if self.dry_run:
                    LOG.info("Would move %s -> %s", source, destination)
                    moved += 1
                    continue

                try:
                    os.makedirs(fs_path(target_dir), exist_ok=True)
                    shutil.move(fs_path(source), fs_path(destination))
                except OSError as exc:
                    self.record_error(f"Cannot move {source}: {exc}")
                    continue

                digest = hash_audio_payload(destination)
                self._record_placement("move", source, destination,
                                       digest.digest if digest else None)
                LOG.info("Moved %s -> %s", source.name, destination.relative_to(self.organized_path))
                moved += 1

        database.finish_run(self.run_id)
        LOG.info("Applied %d correction(s) from %s", moved, csv_path)
        return moved

    def undo_run(self, run_id: Optional[int] = None, assume_yes: bool = False) -> int:
        """Reverse the file placements recorded for a run.

        Copies are deleted and moves are put back. A destination is only touched
        when its audio content still matches what was recorded, so files edited
        or replaced since the run are left alone.
        """
        try:
            return self._undo_run(run_id, assume_yes)
        finally:
            self.close_database()

    def _undo_run(self, run_id: Optional[int], assume_yes: bool) -> int:
        database = self.open_database()
        target_run = run_id if run_id is not None else database.last_run_id()

        if target_run is None:
            LOG.error("No undoable run found in %s", self.organized_path)
            return 0

        operations = database.operations_for_run(target_run)
        if not operations:
            LOG.error("Run %s has no recorded file operations", target_run)
            return 0

        copies = sum(1 for row in operations if row["action"] == "copy")
        moves = len(operations) - copies
        print(f"\nUndoing run {target_run} in {self.organized_path}")
        print(f"  {copies} copied file(s) will be DELETED")
        print(f"  {moves} moved file(s) will be RESTORED to their original location")

        if not assume_yes:
            if not sys.stdin.isatty():
                LOG.error("Refusing to undo without confirmation; pass --yes")
                return 0
            if not input("Proceed? (y/n): ").lower().startswith("y"):
                LOG.info("Undo cancelled.")
                return 0

        reverted, applied_ids = 0, []

        for row in operations:
            target = Path(row["target_path"])
            source = Path(row["source_path"])

            if not os.path.exists(fs_path(target)):
                applied_ids.append(row["id"])  # already gone; forget it
                continue

            if row["audio_hash"]:
                digest = hash_audio_payload(target)
                if digest is None or digest.digest != row["audio_hash"]:
                    LOG.warning("Skipping %s - content changed since the run", target)
                    continue

            try:
                if row["action"] == "move":
                    os.makedirs(fs_path(source.parent), exist_ok=True)
                    shutil.move(fs_path(target), fs_path(source))
                else:
                    os.remove(fs_path(target))
            except OSError as exc:
                self.record_error(f"Cannot undo {target}: {exc}")
                continue

            if row["audio_hash"]:
                database.remove_from_library(row["audio_hash"])
            applied_ids.append(row["id"])
            reverted += 1

        database.delete_operations(applied_ids)
        self._prune_empty_directories()

        LOG.info("Reverted %d file(s) from run %s", reverted, target_run)
        return reverted

    def _prune_empty_directories(self) -> None:
        """Remove directories left empty by an undo."""
        protected = {self.organized_path, self.reports_path,
                     self.organized_path / "Playlists"}
        for directory in sorted(self.organized_path.rglob("*"), reverse=True):
            if not directory.is_dir() or directory in protected:
                continue
            try:
                next(directory.iterdir())
            except StopIteration:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            except OSError:
                pass

    def list_runs(self) -> None:
        """Print the run history for this collection."""
        try:
            rows = self.open_database().runs()
            if not rows:
                print(f"No runs recorded in {self.organized_path}")
                return

            print(f"Runs recorded in {self.organized_path}:\n")
            print(f"{'Run':>5}  {'Started':<20} {'Files':>6}  {'Kind':<8} Source")
            for row in rows:
                kind = "dry-run" if row["dry_run"] else "applied"
                print(f"{row['run_id']:>5}  {row['started_at']:<20} "
                      f"{row['operations']:>6}  {kind:<8} {row['source_root']}")
        finally:
            self.close_database()


def _same_path(first: Path, second: Path) -> bool:
    """True if both paths name the same location, ignoring case on Windows."""
    return (os.path.normcase(os.path.abspath(str(first)))
            == os.path.normcase(os.path.abspath(str(second))))


def _title_key(title: str) -> str:
    """Normalize a title for near-duplicate grouping."""
    text = naming.normalize_title(title).casefold()
    text = re.sub(r"\((?:live|remaster(?:ed)?[^)]*|remix|explicit|clean|\d{4})\)", " ", text)
    text = re.sub(r"\b(feat|ft)\.?\s.*$", " ", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _quality_summary(record: TrackRecord) -> str:
    kind = "lossless" if record.is_lossless else f"{(record.bitrate or 0) // 1000}kbps"
    return f"{record.extension.lstrip('.')} {kind}, {record.tag_score} tags, {record.size} bytes"


def setup_logging(log_directory: Optional[Path], verbose: bool = False,
                  quiet: bool = False) -> Optional[Path]:
    """Configure console and file logging. Returns the log file path, if any."""
    LOG.setLevel(logging.DEBUG)
    for handler in list(LOG.handlers):
        LOG.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO))
    console.setFormatter(logging.Formatter("%(message)s"))
    LOG.addHandler(console)

    if log_directory is None:
        return None

    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        log_file = log_directory / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
        )
        LOG.addHandler(file_handler)
        return log_file
    except OSError as exc:
        LOG.warning("Could not open log file: %s", exc)
        return None


def organize_manual_folder(manual_folder_path: str | Path,
                           organized_collection_path: str | Path,
                           dry_run: bool = False) -> int:
    """Organize files from manual sort folder into existing collection"""
    manual_path = Path(manual_folder_path)
    collection_path = Path(organized_collection_path)

    if not manual_path.exists():
        LOG.error("Manual folder not found: %s", manual_path)
        return 0

    if not collection_path.exists():
        LOG.error("Organized collection not found: %s", collection_path)
        return 0

    organizer = AdvancedMusicOrganizer(manual_path, output_path=collection_path,
                                       dry_run=dry_run)
    database = organizer.open_database()
    organizer.run_id = database.start_run(manual_path, sys.argv[1:], dry_run)

    LOG.info("Organizing manual files from: %s", manual_path)
    LOG.info("Into existing collection: %s", collection_path)

    manual_files = [
        path for path in sorted(manual_path.glob("*"))
        if path.is_file() and path.suffix.lower() in MUSIC_EXTENSIONS
    ]

    if not manual_files:
        LOG.info("No music files found in manual folder.")
        database.finish_run(organizer.run_id)
        return 0

    LOG.info("Found %d files to organize", len(manual_files))

    records: List[TrackRecord] = []
    for path in manual_files:
        record = organizer.scan_file(path)
        if record is not None:
            records.append(record)
            organizer.classify(record)

    organized_count = 0
    for record in records:
        if record.route == ROUTE_MANUAL:
            LOG.info("Still needs manual sorting: %s", record.path.name)
            continue

        artist = organizer.artists.resolve(record.artist) or record.artist
        max_length = organizer.max_component_length
        target_dir = (collection_path
                      / naming.safe_component(artist, max_length)
                      / naming.safe_component(organizer._present(record.album) or "Singles",
                                              max_length))

        filename = naming.build_filename(
            title=organizer._present(record.title) or record.path.stem,
            extension=record.extension,
            track=record.track_number,
            disc=record.disc_number,
            artist=artist,
            template=organizer.config.get("NAMING", "filename_template",
                                          fallback="{track} - {title}"),
            template_without_track=organizer.config.get("NAMING", "filename_template_no_track",
                                                        fallback="{title}"),
            max_length=max_length,
        )
        destination = AdvancedMusicOrganizer._resolve_collision(target_dir / filename)

        if dry_run:
            LOG.info("Would move %s -> %s", record.path.name,
                     destination.relative_to(collection_path))
            organized_count += 1
            continue

        try:
            os.makedirs(fs_path(target_dir), exist_ok=True)
            shutil.move(fs_path(record.path), fs_path(destination))
        except OSError as exc:
            organizer.record_error(f"Error moving {record.path.name}: {exc}")
            continue

        organizer._record_placement("move", record.path, destination, record.audio_hash)
        LOG.info("Moved: %s", destination.relative_to(collection_path))
        organized_count += 1

    database.finish_run(organizer.run_id)
    LOG.info("Organized %d files from manual folder", organized_count)
    if not dry_run and organized_count:
        LOG.info("Undo this run with: --undo %d", organizer.run_id)
    organizer.close_database()
    return organized_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Advanced Music Organizer",
        epilog="Examples:\n"
               "  music_organizer.py D:\\Music\n"
               "  music_organizer.py D:\\Music --dry-run\n"
               "  music_organizer.py D:\\NewAlbums --incremental --output D:\\Music_Organized\n"
               "  music_organizer.py D:\\Music_Organized --apply-csv Reports\\review_queue.csv\n"
               "  music_organizer.py D:\\Music_Organized --undo 7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("music_path", nargs="?",
                        help="Music folder to organize (or the collection, for "
                             "--undo / --apply-csv / --list-runs)")
    parser.add_argument("--output", help="Destination collection "
                                         "(default: <music_path>/../Music_Organized)")
    parser.add_argument("--config", help="Configuration file path")

    parser.add_argument("--dry-run", action="store_true",
                        help="Plan only; write Reports/plan.csv and touch nothing else")
    parser.add_argument("--interactive", action="store_true", help="Enable interactive mode")
    parser.add_argument("--no-rename", action="store_true",
                        help="Keep original filenames instead of normalizing them")
    parser.add_argument("--incremental", action="store_true",
                        help="Add new music to an existing collection, skipping "
                             "anything already present (requires --output)")
    parser.add_argument("--reindex", action="store_true",
                        help="Rebuild the library index from the collection on disk")

    parser.add_argument("--manual-only",
                        help="Organize only this manual sort folder into the collection")
    parser.add_argument("--apply-csv",
                        help="Re-file tracks using a corrected review_queue.csv")
    parser.add_argument("--undo", nargs="?", const=-1, type=int, metavar="RUN_ID",
                        help="Undo a run (default: the most recent one)")
    parser.add_argument("--list-runs", action="store_true", help="Show the run history")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt for --undo")

    parser.add_argument("--verbose", action="store_true", help="Verbose console output")
    parser.add_argument("--quiet", action="store_true", help="Warnings and errors only")
    parser.add_argument("--version", action="version",
                        version=f"Music Organizer {__version__}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.music_path:
        parser.error("music_path is required")

    if args.incremental and not args.output:
        parser.error("--incremental requires --output (the existing collection)")

    # Collection-scoped modes treat the positional argument as the collection.
    collection_modes = (args.undo is not None or args.list_runs
                        or args.apply_csv or args.manual_only)
    output = args.output or (args.music_path if collection_modes else None)

    log_directory = Path(output).joinpath("Reports") if output else None
    if log_directory is None:
        log_directory = Path(args.music_path).parent / "Music_Organized" / "Reports"
    setup_logging(log_directory, verbose=args.verbose, quiet=args.quiet)

    if args.manual_only:
        organize_manual_folder(args.manual_only, args.music_path, dry_run=args.dry_run)
        return 0

    organizer = AdvancedMusicOrganizer(
        args.music_path, args.config, output_path=output, dry_run=args.dry_run
    )

    try:
        if args.list_runs:
            organizer.list_runs()
            return 0

        if args.undo is not None:
            run_id = None if args.undo == -1 else args.undo
            organizer.undo_run(run_id, assume_yes=args.yes)
            return 0

        if args.apply_csv:
            organizer.apply_csv(Path(args.apply_csv))
            return 0

        if args.interactive:
            organizer.config.set("GENERAL", "interactive_mode", "true")
        if args.no_rename:
            organizer.config.set("NAMING", "rename_files", "false")

        organizer.organize(reindex=args.reindex or args.incremental)
        return 0
    finally:
        organizer.close_database()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main())
    else:
        build_parser().print_help()
        sys.exit(2)
