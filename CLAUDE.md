# Music Organizer — Project Context

Python CLI that organizes a messy music collection into
`Artist/Album/NN - Title.ext`, deduplicating by audio content and normalizing
names. Windows-first, but pure `pathlib` and platform-guarded, so it runs
anywhere.

**Current version: 3.0.0.** v1 was the original single-file tool; v2 is the
post-audit rewrite. See [AUDIT.md](AUDIT.md) for why, [CHANGELOG.md](CHANGELOG.md)
for what.

## Architecture

Six modules, deliberately separated so the tricky parts are unit-testable
without real audio files:

| Module | Responsibility |
| --- | --- |
| `music_organizer.py` | Pipeline, classification, CLI. The only module that touches user files. |
| `naming.py` | Filename parsing, artist canonicalization, casing, safe path components. Pure functions plus `ArtistRegistry`. No I/O. |
| `audio_hash.py` | Locating and hashing the audio payload of a container. No I/O beyond reading the file. |
| `state.py` | SQLite: scan cache, library index, run/undo log, fingerprint cache. No knowledge of music. |
| `fingerprint.py` | Chromaprint + AcoustID. The only module that touches the network. |
| `report_html.py` | Renders the run as one self-contained HTML page. Pure string building. |

Dependency direction is strictly `music_organizer` → {`naming`, `audio_hash`,
`state`, `fingerprint`, `report_html`}. The leaf modules never import the
pipeline.

## The pipeline

`AdvancedMusicOrganizer.organize()` runs a series of passes. They are separate
on purpose — deduplication needs every file's quality before electing a winner,
and artist canonicalization needs every name variant before electing a spelling.
Merging passes would reintroduce the original tool's "first file seen wins" bug.

1. **Scan** — hash + read tags for each file, reusing the SQLite scan cache when
   `(path, mtime, size)` is unchanged. Uncached files may be hashed in a process
   pool; workers run `scan_payload()` and return plain dicts.
2. **Deduplicate** — group by audio hash, elect the best copy, then drop
   anything the collection already holds.
3. **Classify** — merge tags → path → filename field by field; score confidence;
   register artist name variants.
3b. **Identify** (optional) — ask AcoustID about whatever the metadata could not
   place, then re-route affected records.
4. **Plan and execute** — resolve canonical names, build destinations, write,
   then tags, sidecars and album art.

Routing is deliberately split out of `classify()` into `assign_route()` and
`collect_routes()`, because acoustic identification rewrites records after
classification and their routes have to be recomputed.

## Invariants — do not break these

- **Sources are never modified.** The pipeline copies. The only modes that move
  files are `--manual-only` and `--apply-csv`, and both record undo entries.
- **Every file placement is logged** to `operations` via `_record_placement`, or
  it cannot be undone.
- **Undo verifies content before deleting.** A target is only touched if its
  audio hash still matches what was recorded.
- **Hash the audio payload, not the file.** Whole-file hashing was the root
  cause of the surviving-duplicates bug. Never "simplify" this back.
- **Classification is anchored on the scan root**, never on a path component
  with a particular name. The original `== 'Music'` sentinel is the reason most
  of the library ended up unclassified.
- **An implausible artist guess is discarded**, not written to disk. A junk
  folder is worse than a file in `Manual Sort Needed`.
- **Filesystem access goes through `fs_path()`** so Windows long paths work.
- **Source-ranking is one table, used twice.** `_ARTIST_SOURCE_SCORE` /
  `_TITLE_SOURCE_SCORE` drive both the confidence score and the decision about
  whether an AcoustID match may overwrite a field. Keep them consistent: tags
  outrank acoustid, which outranks path, which outranks filename.
- **Tag write-back only ever touches the organized copy**, never the source.
- **Worker functions must stay at module level** and take/return only picklable
  values, or the process pool breaks on Windows spawn.
- **Release the SQLite handle** (`close_database()`) at the end of every public
  entry point; Windows will not let the directory be deleted otherwise.

## Conventions

- Type hints on everything; `from __future__ import annotations`.
- No bare `except:`. Catch the specific error, log it through `LOG`, and append
  to `stats["errors"]` via `record_error()` so it lands in the summary report.
- User-facing progress goes through `LOG`, not `print()`. `print()` is reserved
  for interactive prompts and `--list-runs` table output.
- Config access always passes `fallback=`, because a user's `.ini` may predate
  the key. `load_config()` backfills missing sections, so never require the file
  to be deleted on upgrade.
- New config keys go in `AdvancedMusicOrganizer.DEFAULTS` **and**
  `music_organizer_config.ini` **and** the README table.

## Testing

```bash
python -m unittest discover -s tests
```

Tests synthesize their own MP3/FLAC/MP4 byte streams — no real audio in the
repo, and the suite runs in ~3 seconds. Tests needing genuine ID3 tags are
guarded with `@unittest.skipUnless(MUTAGEN, ...)`.

Two traps when writing new tests:

- **Give each synthetic file distinct bytes.** Identical payloads deduplicate,
  and the test will fail in a confusing way.
- **Windows filesystems are case-insensitive.** `beatles/` and `BEATLES/`
  are the same directory, so casing tests must account for it.

## Testing external services

`fingerprint.py` isolates its two external steps in `_run_fpcalc()` and
`_call_api()` precisely so tests can subclass and replace them. Never inline
either call — the suite must keep running with no binary and no network.
Pipeline tests replace the whole service by assigning to
`organizer.build_fingerprint_service`.

## Roadmap

Tiers 1, 2 and 3 in [AUDIT.md](AUDIT.md) are complete.
