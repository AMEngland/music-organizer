# Changelog

All notable changes to this project. Versions follow
[Semantic Versioning](https://semver.org/).

## [3.0.0] — 2026-07-26

The first release with the whole [AUDIT.md](AUDIT.md) roadmap delivered and
documented. No behavioural changes from 2.1.0 — this release is the
documentation and the version stamp that make the work usable by someone who was
not there when it was written.

### Added

- **[HOWTO.md](HOWTO.md)** — the user guide. Installing, running the tool on a
  folder of MP3s, what it does to your files (and what it never does), how a
  track gets identified, which tags are read and written, how duplicates are
  detected, the naming rules, clearing the manual pile, fingerprinting setup,
  incremental runs, undo, every command-line option, every configuration
  setting, worked recipes, and a troubleshooting section.
- README links the guide; `CLAUDE.md` records that a new config key must be
  documented in four places, since a key documented in only some of them is
  worse than one documented nowhere.

### Verified

- Both defects found during Tier 3 development remain fixed and covered:
  an acoustic match overwriting embedded tags, and the interactive resolver
  splitting unparseable filenames into one group per file.
- All 36 configuration keys and all 20 command-line flags are checked to be
  present in the guide.
- 200 tests passing, requiring no real audio, no `fpcalc` and no network.

---

## [2.1.0] — 2026-07-26

Tier 3 of the [AUDIT.md](AUDIT.md) roadmap (items 16-21): identification for
files that carry no usable metadata at all, and the operational polish around it.

### Added

- **Acoustic fingerprinting** (`fingerprint.py`, `--fingerprint`). Runs
  Chromaprint's `fpcalc` and resolves the result against AcoustID, filling in
  artist/title/album for tracks that tags, paths and filenames could not place.
  - Needs `fpcalc` on PATH and an AcoustID key (`FINGERPRINT.api_key` or
    `ACOUSTID_API_KEY`). Missing either is a warning, not a failure — the run
    continues without it.
  - Requests are throttled to AcoustID's 3/second guidance, and every lookup is
    cached in the database, **negative results included**, so a second run costs
    nothing.
  - Only unidentified tracks are submitted by default
    (`FINGERPRINT.only_unidentified`); spending a rate-limited lookup on a
    well-tagged file is waste.
  - A match may overwrite a value inferred from a path or filename, but never
    one read from embedded tags. That ordering comes from the same source-rank
    table that drives confidence scoring, so the two cannot drift apart.
- **Encode-level duplicate merging** (`FINGERPRINT.merge_duplicate_encodes`,
  default off). A 128kbps and a 320kbps rip of one performance have different
  audio and so different hashes; only an acoustic match can tell they are the
  same recording. Off by default because it drops files on the strength of a
  network lookup rather than a byte-level fact.
- **Tag write-back** (`--write-tags`, `NAMING.write_tags`). Writes the resolved
  artist/albumartist/album/title/track/disc/genre/date into the organized copy,
  so the library is self-describing on the next pass. Only ever touches the
  copy, never the source.
- **Parallel hashing** (`--workers N`, `PERFORMANCE.workers`). Uncached files are
  hashed in a process pool; cached ones are resolved in-process because a
  database lookup is cheaper than a task round trip. Auto-sizes to the CPU count
  capped at 8, stays serial below 24 files, and falls back to serial if the pool
  cannot start.
- **Sidecar handling** (`[SIDECARS]`). Per-track companions (`.lrc`, `.cue`,
  `.log`, `.nfo`, `.txt`) are copied alongside the audio and **renamed to match**,
  so players keep the association. A loose `cover.jpg` / `folder.jpg` /
  `front.jpg` is copied as the album's `folder.jpg` when nothing was embedded.
  Both are recorded for undo.
- **Interactive resolver** (`--resolve`). Works through the manual sort pile in
  batches grouped by the shape the parser recognized and by parsed artist, so a
  folder of `Artist - Track.mp3` is one question rather than fifty. Answers are
  applied to the whole group, and the whole session is undoable.
- **HTML report** (`report_html.py`, `Reports/report.html`). One self-contained
  page — no scripts, no external assets, light/dark aware — with stat tiles, what
  was added to the library, collapsible duplicate groups, a redundant-source list
  to act on, near-duplicates, the low-confidence queue with confidence bars,
  artist merges, quality, and errors. Disable with `GENERAL.html_report`.
- Database schema v2 adds a `fingerprints` table. Existing databases migrate in
  place.

### Changed

- Routing moved out of `classify()` into `assign_route()` / `collect_routes()`,
  so records rewritten by acoustic identification get their route recomputed
  rather than keeping a stale one.
- Near-duplicate grouping keys on the AcoustID recording when one is known,
  which catches encodes that no amount of string matching would.
- Tag reading and hashing moved to module-level `read_tags()` / `scan_payload()`
  so worker processes can use them.

### Fixed

- The interactive resolver's grouping no longer splits unparseable filenames
  into one group per file.

---

## [2.0.0] — 2026-07-26

A ground-up rework driven by the findings in [AUDIT.md](AUDIT.md). The v1 tool
left large parts of the collection unclassified, renamed nothing, and missed
most duplicates. Every one of those three failures has a named root cause and a
fix below.

**Breaking changes from 1.0.0**

- `add_new_music.py` has been **deleted**. Use `--incremental --output DIR`.
- Output filenames are now normalized by default (`NN - Title.ext`). Pass
  `--no-rename` for the old behaviour.
- Running with no arguments no longer organizes a hardcoded personal path; it
  prints usage and exits 2.
- Duplicate collision suffixes changed from `name_1.mp3` to `name (2).mp3`.
- New files appear inside the collection: `.music_organizer.db`,
  `Reports/plan.csv`, `Reports/review_queue.csv`, `Reports/run_*.log`.

The two tiers below were developed in sequence but ship together as 2.0.0.

### Tier 2 — safety, operability and incremental runs

Covers roadmap items 8-15 in [AUDIT.md](AUDIT.md).

#### Added

- **`--dry-run`** — plans the whole run and writes `Reports/plan.csv`, touching
  nothing else. The CSV lists every file with its source, destination, chosen
  artist/album/title, classification source, confidence, and audio hash, plus a
  `skip-duplicate` row for each copy that would be dropped and why.
- **Real logging** (`state`-aware, `logging`-based). Console output respects
  `--verbose` / `--quiet`; a full DEBUG transcript is written to
  `Reports/run_<timestamp>.log` on every run.
- **`state.py` — SQLite state** stored as `.music_organizer.db` inside the
  collection:
  - a **scan cache** keyed on `(path, mtime, size)`, so unchanged files are not
    re-hashed or re-tagged on later runs;
  - a **library index** of audio hashes already filed;
  - an **operation log** of every copy and move.
- **Idempotent re-runs.** Recordings already in the collection are skipped, so
  running the tool twice over the same source no longer duplicates anything.
- **`--incremental`** (with `--output`) — adds new music to an existing
  collection, skipping what is already present. Replaces the deleted
  `add_new_music.py`.
- **`--reindex`** — rebuilds the library index by hashing the collection on
  disk. Runs automatically the first time an existing collection is used, or
  after the database is lost.
- **`--undo [RUN_ID]`** — reverses a run: copies are deleted, moves are put
  back, and directories left empty are pruned. A destination is only touched if
  its audio content still matches what was recorded, so anything edited or
  replaced since the run is left alone. Requires confirmation or `--yes`.
- **`--list-runs`** — run history with IDs, timestamps, and file counts.
- **Confidence scoring.** Every classification is scored 0-100 from the
  reliability of its artist source, its title source, and how much corroborating
  metadata exists. Scores appear in `plan.csv` and the interactive preview.
- **`Reports/review_queue.csv`** — the low-confidence and unclassified pile as an
  editable spreadsheet.
- **`--apply-csv FILE`** — re-files tracks from a corrected `review_queue.csv`,
  so a whole batch of manual fixes lands in one pass. Rows left unedited are
  no-ops.
- **`--output DIR`** — the destination is no longer hardcoded to
  `../Music_Organized`.
- **Artist casing pass** (roadmap item 15). A name that is entirely upper or
  entirely lower case is re-cased; deliberate mixed-case styling is never
  touched. Vowel-free acronyms (`MGMT`, `TLC`, `BBC`) are detected
  automatically, an exceptions list covers vowel-bearing ones (`ABBA`, `KISS`,
  `R.E.M.`) and lowercase stage names (`deadmau5`, `will.i.am`), small words are
  lowercased mid-title, and `Mc`/`O'` names are capitalized correctly. Extend the
  list with `NAMING.name_exceptions`; disable entirely with
  `NAMING.titlecase_names = false`.
- `[CLASSIFY]` config section: `review_threshold`, `route_review`,
  `review_folder`. With `route_review = true`, low-confidence tracks are filed
  under `Review/` instead of the main tree; the default is `false`, which keeps
  the library whole and flags them in the CSV only.

#### Removed

- **`add_new_music.py` deleted.** It read no tags, inferred the artist from
  `path_parts[-3]` (yielding artist `Users`, album `andys` for a flat download
  folder), and re-hashed the entire library on every run. Running it against an
  organized collection actively misfiled tracks. Use `--incremental`.

#### Fixed

- `--apply-csv` no longer self-moves a correctly filed track into a spurious
  ` (2)` copy when its review row is left unedited.
- The SQLite handle is released at the end of every operation, so a collection
  directory can be moved or deleted immediately afterwards.
- The library index is no longer rebuilt on a freshly created output folder.
- Verified from Tier 1 (roadmap item 13): M3U forward slashes, `target_path`
  guarded against failed copies, `QUALITY.min_bitrate` honoured.

#### Known gaps (Tier 3)

- Deduplication is still exact-audio only. Different encodes of the same
  recording are reported in `near_duplicates.txt` but not merged; that needs
  acoustic fingerprinting (item 16).
- Resolved tags are not written back to files (item 17).
- Hashing is single-threaded (item 18).

### Tier 1 — classification, naming and deduplication overhaul

Addresses the three problems recorded in [AUDIT.md](AUDIT.md): files left
unclassified, files left improperly named, and duplicates left in place.

#### Added

- `audio_hash.py` — format-aware hashing that covers only the **audio payload**.
  Locates and excludes ID3v2 headers, ID3v1 / APEv2 / Lyrics3 trailers, FLAC
  metadata block chains, and everything outside an MP4 `mdat` atom. Two copies
  of a recording that differ only in their tags now produce the same hash.
  Unsupported containers fall back to a whole-file hash.
- `naming.py` — filename parsing, artist canonicalization and safe output naming.
- `tests/` — unit and end-to-end test suite, runnable with
  `python -m unittest discover -s tests`.
- `requirements.txt`.
- New reports: `duplicates.txt`, `unclassified.txt`, `artist_merges.txt`.
- `--no-rename` flag for keeping original filenames.
- `[NAMING]` config section: `rename_files`, `filename_template`,
  `filename_template_no_track`, `max_component_length`.

#### Fixed — classification

- **Directory parsing no longer requires a folder literally named `Music`.**
  Structure is now inferred relative to the scan root, so libraries in any
  location are parsed. The two directories closest to the file are used, which
  handles `Genre/Artist/Album` trees as well as plain `Artist/Album` ones.
- **Filename parsing understands many more shapes**: `Artist - Title`,
  `Artist - Album - Title`, `NN - Artist - Title`, `NN - Artist - Album - Title`,
  `Artist_Title`, `Artist-Title` (unspaced), en/em dashes, `Artist, Title`, and
  `NN Title`. A leading numeric field is read as a track number rather than
  being mistaken for an artist.
- **Scene junk is stripped** before parsing: `[320kbps]`, `(Official Video)`,
  embedded site URLs, and similar, while genuine qualifiers such as `(Live)` and
  `(Acoustic)` are preserved as part of the title.
- **Sources are merged field by field** rather than used all-or-nothing. Tags
  outrank directory structure, which outranks the filename, so a file with a
  known artist but no album is filed under `Artist/Singles` instead of being
  dumped into manual sort.
- **`CD1` / `Disc 2` folders** are recognized as disc numbers instead of being
  treated as album names.
- **Generic container folders** (`Downloads`, `mp3s`, `Amazon MP3`, `misc`, …)
  are skipped when inferring an artist.
- **Skip folders are matched per path component**, not as substrings of the
  whole path, so a library under a directory such as `.Trash-backup` is no
  longer discarded wholesale.

#### Fixed — naming

- **Files are now actually renamed.** Output is `NN - Title.ext` by default,
  driven by a configurable template. Track numbers are zero-padded so players
  sort correctly, and a disc prefix is added only for multi-disc releases.
- **Artist name variants are folded into one canonical folder.**
  `The Beatles` / `beatles` / `BEATLES` / `Beatles, The` / `Béatles` /
  `Simon & Garfunkel` vs `Simon and Garfunkel` all resolve to a single spelling,
  elected by source quality, then casing, then frequency.
- **`albumartist` (ID3 `TPE2`) is read**, so compilations stay in one album
  folder instead of shattering into one folder per track artist.
- **Numeric ID3 genre references** (`(17)`) are expanded to their names, so
  genre playlists no longer fragment.
- **Path components are made safe**: illegal characters removed, Windows
  reserved device names escaped, trailing dots and spaces trimmed, and lengths
  capped on a word boundary.
- **Windows long-path support** — paths past the 260-character `MAX_PATH` limit
  now use the extended-length form instead of failing the copy.
- Track numbers, disc numbers and years are parsed from compound tag values
  (`7/12`, `1973-03-01`).

#### Fixed — duplicates

- **Deduplication is by audio payload**, so tag-only differences no longer
  defeat it.
- **The surviving copy is elected by quality** — lossless, then bitrate, then
  tag completeness, then file size — instead of by arbitrary scan order.
- Dropped copies are recorded in `Reports/duplicates.txt` with the reason.
- Near-duplicate grouping normalizes titles, ignoring `feat.`, `(Live)`,
  `(Remastered)`, punctuation and case.
- Collision suffixes changed from `_1` to ` (2)`, so distinct songs sharing a
  name no longer read as duplicates.

#### Fixed — other

- Playlist generation no longer crashes with `KeyError` when a copy fails.
- M3U entries use forward slashes on every platform.
- `QUALITY.min_bitrate` is honoured instead of a hardcoded 128.
- Errors are written to `Reports/organization_summary.txt` instead of being
  swallowed and pointed at a log file that was never created.
- Bare `except:` clauses replaced with targeted handling.
- Album art extraction reads `APIC` frames via `getall`, so frames carrying a
  description (most real files) are found; FLAC/Ogg picture blocks and MP4
  `covr` atoms are handled too. Directories are no longer created before
  artwork is confirmed, and no empty folders are left behind.
- Missing config sections are backfilled on load, so upgrading does not require
  deleting the existing `.ini`.
- Removed the hardcoded personal path used when the script ran with no
  arguments; it now prints usage and exits.
- Removed unused imports and the dead `get_audio_fingerprint()` function.
- Type hints throughout.

#### Known gaps at the time (all addressed in Tier 2 unless noted)

- `add_new_music.py` was left unchanged and still guessed the artist from
  `path_parts[-3]` with no tag reading. *Deleted in Tier 2.*
- There was no `--dry-run`; the `--interactive` preview was the only check
  before files were written. *Added in Tier 2.*
- Deduplication is exact-audio only. Different encodes of the same recording are
  reported as near-duplicates but not merged; that needs acoustic
  fingerprinting (Tier 3 item 16). *Still open.*

---

## [1.0.0] — 2025-07-22

Initial version: `music_organizer.py` plus `add_new_music.py`. Sorted files into
`Artist/Album/` using ID3 tags, directory structure, or a `Artist - Title`
filename, deduplicated with a whole-file MD5, extracted album art, and wrote M3U
playlists and three text reports.
