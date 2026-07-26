# Music Organizer — Project Context

Python CLI that organizes a messy music collection into
`Artist/Album/NN - Title.ext`, deduplicating by audio content and normalizing
names. Windows-first, but pure `pathlib` and platform-guarded, so it runs
anywhere.

**Current version: 2.0.0.** v1 was the original single-file tool; v2 is the
post-audit rewrite. See [AUDIT.md](AUDIT.md) for why, [CHANGELOG.md](CHANGELOG.md)
for what.

## Architecture

Four modules, deliberately separated so the tricky parts are unit-testable
without real audio files:

| Module | Responsibility |
| --- | --- |
| `music_organizer.py` | Pipeline, classification, CLI. The only module that touches user files. |
| `naming.py` | Filename parsing, artist canonicalization, casing, safe path components. Pure functions plus `ArtistRegistry`. No I/O. |
| `audio_hash.py` | Locating and hashing the audio payload of a container. No I/O beyond reading the file. |
| `state.py` | SQLite: scan cache, library index, run/undo log. No knowledge of music. |

Dependency direction is strictly `music_organizer` → {`naming`, `audio_hash`,
`state`}. The leaf modules never import the pipeline.

## The pipeline

`AdvancedMusicOrganizer.organize()` runs four passes. They are separate passes
on purpose — deduplication needs every file's quality before electing a winner,
and artist canonicalization needs every name variant before electing a spelling.
Merging passes would reintroduce the original tool's "first file seen wins" bug.

1. **Scan** — hash + read tags for each file, reusing the SQLite scan cache when
   `(path, mtime, size)` is unchanged.
2. **Deduplicate** — group by audio hash, elect the best copy, then drop
   anything the collection already holds.
3. **Classify** — merge tags → path → filename field by field; score confidence;
   register artist name variants.
4. **Plan and execute** — resolve canonical names, build destinations, write.

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

## Roadmap

Tiers 1 and 2 are complete. Tier 3 (items 16-21 in [AUDIT.md](AUDIT.md)) covers
acoustic fingerprinting, tag write-back, parallel hashing, sidecar files, an
interactive resolver, and an HTML report.
