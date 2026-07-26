# Music Organizer Project

**Version 3.1.0**

A Python music library organizer that sorts audio files into a clean
`Artist/Album/NN - Title.ext` structure using embedded metadata, directory
structure, and filename parsing — deduplicating by audio content along the way.

Every run is planned before it executes, recorded in a database, and reversible.

See [AUDIT.md](AUDIT.md) for the codebase audit and improvement roadmap, and
[CHANGELOG.md](CHANGELOG.md) for what has changed.

## Features

- **Smart Organization**: Sorts music into `Artist/Album/NN - Title.ext`
- **Metadata Reading**: Embedded tags via mutagen, including `albumartist`,
  track/disc numbers, and numeric genre references
- **Filename Normalization**: Configurable output template, zero-padded track
  numbers, junk stripping, Windows-safe path components
- **Content-Based Deduplication**: Hashes the audio payload only, so copies that
  differ merely in their tags are still detected
- **Quality-Ranked Survivors**: When duplicates are found, the best copy is kept
  (lossless > bitrate > tag completeness > size)
- **Artist Canonicalization**: `The Beatles` / `beatles` / `BEATLES` /
  `Beatles, The` collapse into one folder, with a casing pass that leaves
  `MGMT`, `ABBA` and `deadmau5` alone
- **Confidence Scoring**: Every classification is scored, and the shaky ones are
  queued in an editable CSV instead of disappearing into a folder
- **Dry Run**: `--dry-run` writes the full plan as CSV and touches nothing
- **Undo**: any run can be reversed
- **Incremental**: repeat runs skip what the collection already holds
- **Acoustic Fingerprinting** *(opt-in)*: identifies files with no usable
  metadata at all via Chromaprint + AcoustID, and can spot the same recording
  across different encodes
- **Tag Write-Back** *(opt-in)*: writes the resolved metadata into the organized
  copies so the library becomes self-describing
- **Parallel Hashing**: uses every core when there is enough work to justify it
- **Sidecar Handling**: lyrics, cue sheets and loose cover art follow the audio
- **Interactive Resolver**: works through the manual pile in batches, not
  file by file
- **Album Art Extraction**: Saves embedded artwork as `folder.jpg`
- **Playlist Generation**: M3U playlists by genre, year, or artist
- **Reports**: a self-contained HTML report plus quality, duplicates,
  near-duplicates, unclassified files, artist merges, and a per-run log

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# See what would happen, without writing anything
python music_organizer.py "C:\path\to\your\Music" --dry-run

# Organize for real
python music_organizer.py "C:\path\to\your\Music"

# Changed your mind
python music_organizer.py "C:\path\to\Music_Organized" --undo
```

Originals are never modified — the organizer copies. The modes that **move**
files are `--manual-only` and `--apply-csv`, and both are undoable.

## Command Reference

| Flag | Purpose |
| --- | --- |
| `--dry-run` | Plan only. Writes `__Reports__/plan.csv` and nothing else. |
| `--output DIR` | Destination collection (default `<music_path>/../Music_Organized`). |
| `--interactive` | Show a sample of the plan and confirm before writing. |
| `--no-rename` | Keep original filenames; only reorganize folders. |
| `--incremental` | Add new music to an existing collection (requires `--output`). |
| `--reindex` | Rebuild the library index by hashing the collection on disk. |
| `--fingerprint` | Identify unidentified tracks via Chromaprint/AcoustID. |
| `--write-tags` | Write the resolved metadata into the organized copies. |
| `--workers N` | Hashing processes (0 = auto, 1 = serial). |
| `--manual-only DIR` | Re-ingest a manual sort folder into the collection. |
| `--apply-csv FILE` | Re-file tracks from a corrected `review_queue.csv`. |
| `--resolve` | Work through the manual sort folder interactively, in batches. |
| `--undo [RUN_ID]` | Reverse a run (default: the most recent). Needs `--yes` to skip the prompt. |
| `--list-runs` | Show the run history for a collection. |
| `--verbose` / `--quiet` | Console detail level. The log file is always full detail. |
| `--config FILE` | Configuration file path. |
| `--version` | Print the version and exit. |

For `--undo`, `--list-runs`, `--apply-csv`, `--resolve` and `--manual-only`, the
positional argument is the **collection**, not the source folder.

## Typical Workflows

**First pass over a messy library**

```bash
python music_organizer.py "D:\Music" --dry-run
# read Music_Organized\__Reports__\plan.csv, adjust config if needed
python music_organizer.py "D:\Music"
```

**Fix the files it could not identify**

```bash
# Edit Music_Organized\__Reports__\review_queue.csv in a spreadsheet:
# fill in artist / album / title / track for the rows you care about.
python music_organizer.py "D:\Music_Organized" --apply-csv "D:\Music_Organized\__Reports__\review_queue.csv"
```

Rows left blank are skipped, and rows already filed correctly are no-ops, so the
file can be applied repeatedly as you work through it.

**Add new downloads later**

```bash
python music_organizer.py "D:\NewAlbums" --incremental --output "D:\Music_Organized"
```

Anything already in the collection is skipped by audio hash — including copies
that were renamed or retagged since.

**Back out a run**

```bash
python music_organizer.py "D:\Music_Organized" --list-runs
python music_organizer.py "D:\Music_Organized" --undo 7
```

Copies are deleted, moves are restored, and empty folders are pruned. A file is
only touched if its audio still matches what was recorded, so anything you
edited afterwards is left alone.

## Output Structure

```
Music_Organized/
├── Artist Name/
│   ├── Album Name/
│   │   ├── 01 - First Track.mp3
│   │   ├── 02 - Second Track.mp3
│   │   └── folder.jpg (album art)
│   └── Singles/
├── __Manual Sort Needed__/      (files that could not be identified)
├── __Playlists__/
│   ├── genre_Rock.m3u
│   └── year_2023.m3u
├── __Reports__/
│   ├── report.html           (start here: the whole run, one page)
│   ├── plan.csv              (every decision, with confidence)
│   ├── review_queue.csv      (editable; feed back with --apply-csv)
│   ├── quality_report.txt
│   ├── duplicates.txt        (what was skipped, and why)
│   ├── near_duplicates.txt   (same song, different encode)
│   ├── unclassified.txt
│   ├── artist_merges.txt     (name variants folded together)
│   ├── organization_summary.txt
│   └── run_20260726_010319.log
└── .music_organizer.db       (scan cache, library index, undo log)
```

## How Classification Works

Sources are consulted in descending reliability and merged field by field, so a
partially-identifiable file is still filed correctly rather than being dumped
into manual sort:

1. **Embedded tags** — `albumartist` preferred over `artist`, so compilations
   stay together.
2. **Directory structure** — inferred relative to the scan root. The two
   directories nearest the file are used, so both `Artist/Album/` and
   `Genre/Artist/Album/` trees work. Generic containers (`Downloads`, `mp3s`,
   `Amazon MP3`, …) are ignored, and `CD1` / `Disc 2` folders become disc
   numbers.
3. **Filename** — see the patterns below.
4. **Acoustic fingerprint** — only with `--fingerprint`, and by default only for
   files the first three could not place.

A file with a usable artist but no album goes to `Artist/Singles`. Only a file
that none of the above can identify goes to `__Manual Sort Needed__`.

An acoustic match may overwrite a value guessed from a path or filename, but
never one read from embedded tags — your own tags always win.

### Confidence

Each result is scored 0-100 from the reliability of its artist source, its title
source, and how much corroborating metadata exists. Roughly:

| Situation | Score | Outcome |
| --- | --- | --- |
| Full tags, album and track number | 90+ | filed |
| Artist from folder structure, album known | ~65 | filed |
| Artist guessed from a `Artist - Title` filename | ~40 | filed, queued for review |
| Artist guessed from a single unspaced dash | ~30 | filed, queued for review |
| No artist anywhere | 0 | `__Manual Sort Needed__` |

Anything below `CLASSIFY.review_threshold` (default 55) is written to
`review_queue.csv`. By default it still goes into the main library — set
`route_review = true` to divert it into a `__Review__/` folder instead.

## How Deduplication Works

Duplicates are found by hashing the **audio payload**, not the whole file. ID3v2
headers, ID3v1 / APEv2 / Lyrics3 trailers, FLAC metadata blocks and MP4 atoms
outside `mdat` are excluded, so two copies of the same recording match even when
their tags, embedded artwork, or padding differ. Containers that cannot be
parsed fall back to a whole-file hash.

When a group of duplicates is found, the survivor is chosen by:
lossless format → higher bitrate → more complete tags → larger file. Every
skipped copy is listed in `Reports/duplicates.txt` with the reason.

Different *encodes* of the same recording (a 128kbps and a 320kbps rip) have
different audio, so hashing cannot connect them. They are reported in
`near_duplicates.txt`. With `--fingerprint` they are grouped by AcoustID
recording, and setting `FINGERPRINT.merge_duplicate_encodes = true` will collapse
them to the best copy — off by default, because that discards a file on the
strength of a network lookup rather than a byte-level fact.

## Acoustic Fingerprinting

For files that carry no usable metadata at all, `--fingerprint` identifies the
audio itself. Two prerequisites:

1. **Chromaprint's `fpcalc`** on PATH — <https://acoustid.org/chromaprint> — or
   set `FINGERPRINT.fpcalc_path`.
2. **A free AcoustID API key** — <https://acoustid.org/new-application> — in
   `FINGERPRINT.api_key` or the `ACOUSTID_API_KEY` environment variable.

```bash
set ACOUSTID_API_KEY=your-key-here
python music_organizer.py "D:\Music" --fingerprint
```

If either piece is missing the run prints a warning and continues without it.
Lookups are throttled to AcoustID's 3-per-second guidance and cached in the
database — including misses — so a second run costs nothing. By default only
tracks that reached the review queue or manual sort are submitted; set
`FINGERPRINT.only_unidentified = false` to submit everything.

## Working Through the Leftovers

Two ways to clear `__Manual Sort Needed__`, depending on how many files there are:

```bash
# Spreadsheet: edit Reports\review_queue.csv, then apply it
python music_organizer.py "D:\Music_Organized" --apply-csv "D:\Music_Organized\__Reports__\review_queue.csv"

# Terminal: answer one question per group
python music_organizer.py "D:\Music_Organized" --resolve
```

`--resolve` groups files by the pattern the parser recognized and by the artist
it managed to parse, so a folder of `Artist - Track.mp3` from one artist is a
single prompt. Both routes are undoable.

## Manual File Naming Convention

Files in `__Manual Sort Needed__` can be renamed and re-ingested with
`--manual-only`. All of these are understood:

| Filename | Result |
| --- | --- |
| `Pink Floyd - Comfortably Numb.mp3` | `Pink Floyd/Singles/Comfortably Numb.mp3` |
| `Nirvana - Nevermind - Lithium.mp3` | `Nirvana/Nevermind/Lithium.mp3` |
| `05 - Nirvana - Lithium.mp3` | `Nirvana/Singles/05 - Lithium.mp3` |
| `Beatles_HeyJude.mp3` | `Beatles/Singles/HeyJude.mp3` |
| `Nirvana-Lithium.mp3` | `Nirvana/Singles/Lithium.mp3` |
| `Björk – Hyperballad.mp3` | `Björk/Singles/Hyperballad.mp3` |
| `Radiohead, Creep.mp3` | `Radiohead/Singles/Creep.mp3` |

Scene junk is removed automatically, so
`03_-_Muse_-_Hysteria_(Official Video)_[320kbps]_www.example.com.mp3` becomes
`Muse/Singles/03 - Hysteria.mp3`. Genuine qualifiers such as `(Live)`,
`(Acoustic)` and `(Remix)` are kept as part of the title.

**Still not parseable:**

- Single words or stopwords as artists (`The`, `A`, `I`, `and`)
- Numbers only (`09182734.mp3`)
- Anything with no artist and no usable tags

For those, use `review_queue.csv` and `--apply-csv` instead of renaming by hand.

## Configuration

`music_organizer_config.ini` is created on first run and any missing keys are
backfilled on later runs, so upgrading never requires deleting it.

| Section | Key | Purpose |
| --- | --- | --- |
| `GENERAL` | `interactive_mode`, `create_playlists`, `extract_album_art`, `analyze_quality`, `find_near_duplicates` | Feature toggles |
| `FOLDERS` | `skip_folders` | Comma-separated folder names to skip (matched per path component) |
| `FOLDERS` | `manual_sort_folder` | Name of the quarantine folder |
| `FOLDERS` | `reports_folder`, `playlists_folder` | Names of the tool's own folders |
| `NAMING` | `rename_files` | Set `false` to keep original filenames |
| `NAMING` | `filename_template` | Default `{track} - {title}`; fields: `{track}`, `{title}`, `{artist}`, `{disc}` |
| `NAMING` | `filename_template_no_track` | Used when no track number is known |
| `NAMING` | `max_component_length` | Per-component character cap |
| `NAMING` | `titlecase_names` | Re-case ALL-CAPS and all-lowercase names |
| `NAMING` | `name_exceptions` | Extra names whose casing must be preserved |
| `NAMING` | `write_tags` | Write resolved metadata into the organized copies |
| `CLASSIFY` | `review_threshold` | Confidence below which a track is queued for review |
| `CLASSIFY` | `route_review`, `review_folder` | Physically separate low-confidence tracks |
| `FINGERPRINT` | `enabled`, `api_key`, `fpcalc_path` | Acoustic identification |
| `FINGERPRINT` | `min_score`, `only_unidentified`, `rate_limit_per_second` | Lookup tuning |
| `FINGERPRINT` | `merge_duplicate_encodes` | Treat matching recordings as duplicates |
| `PERFORMANCE` | `workers` | Hashing processes (0 = auto, 1 = serial) |
| `SIDECARS` | `copy_sidecars`, `sidecar_extensions`, `copy_folder_images` | Companion files |
| `GENERAL` | `html_report` | Write `__Reports__/report.html` |
| `QUALITY` | `min_bitrate` | Threshold for the low-quality report |
| `PLAYLISTS` | `create_by_genre`, `create_by_year`, `create_by_artist`, `min_songs_for_playlist` | Playlist rules |

### Name casing

`titlecase_names` only rewrites names that are *entirely* upper or lower case —
deliberate styling such as `Sigur Rós` or `iamamiwhoami` is never touched.
Vowel-free acronyms (`MGMT`, `TLC`, `XTC`) are detected automatically. Names
that need protecting but contain vowels (`ABBA`, `KISS`, `R.E.M.`) come from a
built-in list you can extend:

```ini
[NAMING]
name_exceptions = THE KLF, dEUS, ACME
```

## Files

- `music_organizer.py` — main organizer and CLI
- `naming.py` — filename parsing, artist canonicalization, casing, safe naming
- `audio_hash.py` — audio-payload-scoped hashing
- `state.py` — SQLite scan cache, library index, undo log and fingerprint cache
- `fingerprint.py` — Chromaprint/AcoustID identification
- `report_html.py` — self-contained HTML report
- `music_organizer_config.ini` — generated configuration
- `tests/` — test suite
- `CLAUDE.md` — architecture notes and invariants for contributors

## Upgrading from 1.x

`add_new_music.py` is gone; use `--incremental --output DIR`. Filenames are
normalized by default — pass `--no-rename` to keep the 1.x behaviour. Your
existing `music_organizer_config.ini` is upgraded in place; new keys are
backfilled, so there is no need to delete it.

The first run against an existing collection hashes what is already there to
build the library index, so it takes longer than subsequent runs. After that,
re-running is cheap and idempotent.

## Tests

```bash
python -m unittest discover -s tests
```

The suite synthesizes its own audio files, so no real music is needed, and it
needs neither `fpcalc` nor a network — the external fingerprinting steps are
stubbed. Tests requiring genuine ID3 tags are skipped if mutagen is not
installed.

## Development Notes

- Originally a simple folder organizer; now metadata-driven with content-based
  deduplication, confidence scoring, and a reversible operation log
- Preserves original files (copies rather than moves)
- Conservative filename parsing — an implausible artist guess is discarded
  rather than creating a junk folder

Suitable for preparing collections for mobile players like Musicolet that work
best with organized Artist/Album structures.
