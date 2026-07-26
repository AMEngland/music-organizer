# Music Organizer — Code Audit & Improvement Roadmap

**Date:** 2026-07-25
**Scope:** `music_organizer.py`, `add_new_music.py`, `music_organizer_config.ini`, `README.md`
**Baseline commit:** `cf2ddcd` — Fix manual-only feature command line argument handling

---

## 1. Purpose of this document

The tool was built to do three things to an MP3 collection: **organize** it, **prune duplicates**, and
**normalize naming conventions**. In practice it left a large number of files unclassified, many
improperly named, and several duplicates in place.

This document records a full read of the codebase, identifies why each of those three outcomes
happened, and lays out a tiered plan to fix them.

---

## 2. How the software currently works

### 2.1 Entry points

| File | Role |
| --- | --- |
| `music_organizer.py` | Main tool. Class `AdvancedMusicOrganizer` plus a standalone `organize_manual_folder()` helper and an `argparse` CLI. |
| `add_new_music.py` | Separate incremental-add script. Re-implements a weaker version of the same logic. Prompts for a path via `input()`; the destination is hardcoded. |
| `music_organizer_config.ini` | INI config, auto-generated from in-code defaults on first run. |

### 2.2 Main pipeline (`AdvancedMusicOrganizer.organize`)

1. **Discover** — `get_all_music_files()` walks the source tree with `rglob('*')`, keeping
   `.mp3 .m4a .flac .wav .aac .ogg .wma`, and filtering out configured skip folders.
2. **Deduplicate** — a full-file MD5 is computed for every file. First file for a given hash wins;
   later files with the same hash are skipped.
3. **Classify** — `parse_metadata()` tries, in order:
   embedded tags via `mutagen` → directory-structure parsing (`parse_from_path`) →
   filename parsing (`parse_filename_only`) → the literal sentinel `"Manual Sort Needed"`.
4. **Group** — files are bucketed into an `{artist}/{album}` dictionary.
5. **Execute** — directories are created and files are `shutil.copy2`'d in. Name collisions get a
   `_1`, `_2`, … suffix. Originals are never modified.
6. **Report** — M3U playlists by genre/year/artist, plus three plain-text reports
   (`quality_report.txt`, `near_duplicates.txt`, `organization_summary.txt`).

### 2.3 Supporting behaviour

- **Album art** — embedded artwork written as `folder.jpg` next to the album.
- **Quality analysis** — counts files by extension and by bitrate band.
- **Near-duplicate detection** — groups files by `artist_title` and writes them to a report.
- **Interactive preview** — with `--interactive`, prints a sample of the plan and asks y/n.
- **Manual-sort re-run** — `--manual-only` re-parses the manual folder and `shutil.move`s anything
  it can now identify into an existing collection.

---

## 3. Root-cause analysis

### 3.1 Why so many files ended up unclassified

**A. Directory parsing only works if a path component is literally named `Music`.**

`music_organizer.py:282`

```python
music_index = next((i for i, part in enumerate(path_parts) if part == 'Music'), -1)
```

The match is exact and case-sensitive. A library at `D:\Audio\`, `...\My Music\`, or `...\music\`
yields `-1`, so **all** directory-structure inference is skipped and every untagged file drops
through to filename-only parsing. When the component does exist, the *first* occurrence in the
absolute path wins — `C:\Users\andys\Music\...` matches at the user-profile level, so the artist is
inferred from the wrong depth.

**B. Filename parsing understands exactly one separator.**

`music_organizer.py:309` tests `' - '` (space-hyphen-space) and nothing else. There is no handling of
`_`, en/em dashes (`–`, `—`), unspaced `Artist-Title`, `Artist - Album - Track`, `01 Track Name`, or
comma separation. `README.md:90` advertises `Artist_Track.mp3` support that **does not exist in the
code**. Anything without ` - ` is routed to Manual Sort regardless of how parseable it is.

**C. There is no fallback beyond the filename.**

No acoustic fingerprinting, no online lookup, and no folder-level inference (e.g. "15 of the 16 files
in this directory resolved to Artist X, so the 16th probably is too"). Classification is also
all-or-nothing: a file with a confidently-known artist but an unknown album is discarded to Manual
Sort rather than filed under `Artist/Singles`.

**D. Album art extraction silently fails and leaves debris.**

`music_organizer.py:193` checks `'APIC:' in audio_file`. Mutagen's ID3 hash key for a picture frame
is `'APIC:' + description`, so real-world files keyed `APIC:Cover (front)` never match; the correct
access is `tags.getall('APIC')`. FLAC exposes artwork via `.pictures`, not as a key at all. Worse,
`music_organizer.py:202-203` creates `Artist/Album/` *before* confirming any artwork exists, leaving
empty directories behind.

### 3.2 Why so many files are improperly named

**The tool never renames anything.** `track` is computed at `music_organizer.py:269`, stored in the
file record, and then never used. The copy at `music_organizer.py:536` is:

```python
target_file = target_dir / file_info['filename']
```

The original filename is preserved verbatim. So `03_-_track_(www.dodgysite.com).mp3` lands inside a
tidy `Artist/Album/` folder under its original messy name. **Filename normalization — one of the
three stated goals — was never implemented.**

Contributing factors to inconsistent *folder* naming:

- No `The Beatles` / `Beatles` / `beatles` folding — three folders for one artist.
- `albumartist` (ID3 `TPE2`) is never read, so compilations shatter into one folder per track artist.
- ID3 numeric genre references (`(13)`) are not decoded, fragmenting genre playlists.
- No Unicode NFC/NFD normalization, so visually identical artist names produce separate folders.
- `clean_name()` (`music_organizer.py:122-130`) strips illegal characters but does not guard Windows
  reserved device names (`CON`, `NUL`, `AUX`, `PRN`, …) and applies no length cap, so deep
  `Artist/Album/Track` paths exceed the 260-character `MAX_PATH` limit and fail the copy.
- Track numbers are not zero-padded, so players sort 1, 10, 11, 2.

### 3.3 Why duplicates survived

**Deduplication is an exact whole-file MD5** (`music_organizer.py:98-107`). The hash covers the ID3
tag block, so identical audio differing by a single tag byte, a different embedded cover, or
different padding produces a different hash — and both copies are kept. This is the dominant source
of surviving duplicates.

Compounding issues:

- **Near-duplicate detection never acts.** `music_organizer.py:244-252` only appends to a report. It
  also requires both files to carry artist *and* title tags — precisely the tags missing from the
  untagged files most likely to be duplicated. The grouping key is a raw
  `artist.lower()_title.lower()` with no normalization for `feat.`, `(Remastered)`, `(Live)`, or
  punctuation.
- **`get_audio_fingerprint()` is dead code.** Defined at `music_organizer.py:109-120`, never called.
  Its size + first/last-1KB approach would hash the tag block anyway, so it would not have worked.
- **The wrong copy wins.** On a hash collision the first file returned by `rglob` is kept
  (`music_organizer.py:492-496`) — effectively arbitrary order. A 128 kbps rip can beat a 320 kbps one.
- **`duplicates_removed` is a misnomer.** Nothing is deleted; the source tree is untouched because
  the tool copies rather than moves. The statistic overstates what happened.
- **Collision suffixes manufacture look-alike duplicates.** `music_organizer.py:540-543` appends
  `_1`, `_2` when two *distinct* songs share a filename, producing output that reads as duplicated.

---

## 4. Additional defects

| Location | Defect |
| --- | --- |
| `music_organizer.py:422` | Skip-folder test is a substring match against the whole path string. A library under `D:\.Trash-backup\` is skipped entirely. The config value is also split on `', '`, so a comma without a trailing space silently breaks parsing. |
| `music_organizer.py:367` | `file_info['target_path']` uses direct indexing. Any copy exception at `:550` leaves the key unset, so playlist generation raises `KeyError` and the reports are never written. |
| `music_organizer.py:368` | M3U entries concatenate a `../` prefix with a Windows `Path` string, yielding mixed separators (`../Artist\Album\x.mp3`). Most players reject these. |
| `music_organizer.py:237` | The low-quality threshold is hardcoded to `128`; the `QUALITY.min_bitrate` config key is never read. `preferred_format` and `report_low_quality` are also unused. |
| `music_organizer.py:570` | Prints `"see logs"` but no log file is ever written — recorded errors are unreachable. |
| throughout | Bare `except:` / `except Exception: pass` swallow every failure, making the reported symptoms undiagnosable. |
| `add_new_music.py:81-107` | Infers artist as `path_parts[-3]` with no tag reading at all. For a flat `Downloads` folder this yields artist `Users`, album `andys`. Misfiling is guaranteed. |
| `add_new_music.py:23-27` | Recomputes a full MD5 of the entire organized library on every run, with no cache. |
| `music_organizer.py:293` | Hardcoded, undocumented `not d.startswith('Part')` filter. The first directory below `Music` is assumed to be an artist even when it is `Rock`, `Compilations`, or `Albums`. |
| `music_organizer.py:31` | Output directory is hardcoded to `../Music_Organized` and cannot be configured. The config file is written to `music_path.parent`, scattering configs across drives. |
| `music_organizer.py:660-663` | Hardcoded personal path used when the script is run with no arguments. |
| `music_organizer.py:3-26` | Unused imports: `os`, `json`, `Counter`, `ID3NoHeaderError`, `Image`. |
| `music_organizer.py:623` | `--manual-only` performs a destructive `shutil.move` with no dry-run and no undo log. |

Against the project's own standards in `CLAUDE.md`: no type hints, no tests, no `requirements.txt`,
and no use of the `logging` module.

---

## 5. Improvement roadmap

### Tier 1 — directly addresses the three reported failures

| # | Change | Fixes |
| --- | --- | --- |
| 1 | **Hash audio frames, not the whole file.** Locate the tag region with a format-aware parser and hash only the audio payload. | Duplicates |
| 2 | **Actually rename files.** Template-driven output names (`{track:02d} - {title}.ext`), configurable, with safe-path handling. | Naming |
| 3 | **Remove the `== 'Music'` path sentinel.** Infer structure relative to the scan root instead. | Unclassified |
| 4 | **Multi-pattern filename parser.** Ordered regex set covering dash/en-dash/underscore/comma separators, leading track numbers, and three-part `Artist - Album - Title` names, with junk stripping (`[320kbps]`, `(Official Video)`, embedded URLs). | Unclassified, Naming |
| 5 | **Quality-ranked duplicate winner.** Choose the survivor by lossless-ness, then bitrate, then tag completeness, then size — never scan order. | Duplicates |
| 6 | **Fix album-art extraction.** Use `tags.getall('APIC')` and `FLAC.pictures`; only create directories once artwork is confirmed. | Naming/tidiness |
| 7 | **Artist canonicalization.** Fold leading `The`, case, punctuation and Unicode form into a single canonical display name; read `albumartist` for compilations. | Naming |

### Tier 2 — correctness, safety, and operability

| # | Change |
| --- | --- |
| 8 | `--dry-run` producing a full CSV plan (source → target, classification source, confidence) for review before anything touches disk. |
| 9 | Real `logging` with a per-run log file; replace every bare `except`. |
| 10 | SQLite state database (path, mtime, size, audio hash, resolved tags) enabling incremental re-runs and undo. |
| 11 | Confidence scoring instead of a binary manual-sort flag; route high/medium/low into `Organized/`, `Review/`, `Manual Sort/`, and emit the low pile as a CSV for bulk spreadsheet correction and re-ingest. |
| 12 | Fold `add_new_music.py` into the main tool as `--incremental`; it is a strictly weaker duplicate of existing logic. |
| 13 | Fix M3U path separators; use `.get('target_path')`; honour `QUALITY.min_bitrate`. |
| 14 | Windows long-path support (`\\?\` prefix), reserved-name guard, per-component length capping. |
| 15 | **Artist display-name casing pass** with an exceptions list, so a folder is not named `PINK FLOYD` just because the tag shouts. Must preserve deliberate styling: vowel-free acronyms (`MGMT`, `TLC`), listed exceptions (`ABBA`, `KISS`, `R.E.M.`), and lowercase stage names (`deadmau5`). |

### Tier 3 — new capability

| # | Change |
| --- | --- |
| 16 | **AcoustID / Chromaprint fingerprinting** (`pyacoustid` + `fpcalc`). Identifies untagged files against MusicBrainz *and* detects true audio duplicates across formats and bitrates. Opt-in behind `--fingerprint` (needs an API key and network access). |
| 17 | Write resolved tags back to files so the library becomes self-describing. |
| 18 | Parallel hashing via `ProcessPoolExecutor` — hashing dominates wall-clock time. |
| 19 | Sidecar handling: move `.lrc`, `.cue`, `.jpg`, `.log` alongside their audio. |
| 20 | Interactive resolver for ambiguous cases, batched by pattern rather than per file. |
| 21 | HTML report replacing the three text files, with clickable duplicate groups and a proposed-deletion list. |

> Item 15 was added after Tier 1, at the point where an ALL-CAPS tag was
> observed winning an artist folder name. Tier 3 was renumbered from 15–20 to
> 16–21 to make room.

---

## 6. Status

- [x] **Tier 1** — implemented (see `CHANGELOG.md`). Item 14 was pulled forward
      from Tier 2 because normalized filenames built from tags can breach
      `MAX_PATH`, which silently dropped files.
- [x] **Tier 2** — implemented. `add_new_music.py` was deleted rather than
      merely deprecated; its behaviour lives on as `--incremental`.
- [x] **Tier 3** — implemented. Fingerprinting is opt-in and degrades cleanly
      when `fpcalc` or an API key is missing; encode-level duplicate merging is
      opt-in on top of that, because it drops files on the strength of a network
      lookup rather than a byte-level fact.
