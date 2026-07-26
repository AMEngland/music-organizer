# Music Organizer — How-To Guide

A practical guide to using the tool: what it does to your files, how to run it,
what every option means, and how to fix the things it gets wrong.

**Version 3.0.0**

---

## Contents

1. [Install](#1-install)
2. [The 60-second version](#2-the-60-second-version)
3. [What actually happens to your files](#3-what-actually-happens-to-your-files)
4. [Reading the output](#4-reading-the-output)
5. [How a track gets identified](#5-how-a-track-gets-identified)
6. [Tags: what is read and written](#6-tags-what-is-read-and-written)
7. [How duplicates are detected](#7-how-duplicates-are-detected)
8. [Naming rules](#8-naming-rules)
9. [Cleaning up the leftovers](#9-cleaning-up-the-leftovers)
10. [Acoustic fingerprinting](#10-acoustic-fingerprinting)
11. [Adding music later](#11-adding-music-later)
12. [Undoing a run](#12-undoing-a-run)
13. [Every command-line option](#13-every-command-line-option)
14. [Every configuration setting](#14-every-configuration-setting)
15. [Recipes](#15-recipes)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Install

You need Python 3.9 or newer.

```bash
cd MusicOrganizer
pip install -r requirements.txt
```

That installs `mutagen`, which reads embedded tags. The tool runs without it,
but it will be flying blind — it can then only use folder names and filenames,
so install it.

Two optional extras, only needed for `--fingerprint` (see [section 10](#10-acoustic-fingerprinting)):

- **Chromaprint** (`fpcalc`) — <https://acoustid.org/chromaprint>
- **A free AcoustID API key** — <https://acoustid.org/new-application>

Check the install:

```bash
python music_organizer.py --version
```

---

## 2. The 60-second version

You have a folder of MP3s at `D:\Music`. Do this:

```bash
# 1. See what it plans to do. Writes a CSV, touches nothing else.
python music_organizer.py "D:\Music" --dry-run

# 2. Open D:\Music_Organized\Reports\plan.csv and skim it.

# 3. Do it for real.
python music_organizer.py "D:\Music"

# 4. Open D:\Music_Organized\Reports\report.html to see what happened.
```

Your originals are untouched — the tool **copies**. The result lands in
`Music_Organized` next to your source folder.

Changed your mind?

```bash
python music_organizer.py "D:\Music_Organized" --undo
```

---

## 3. What actually happens to your files

**Nothing is moved or deleted from your source folder.** The organizer reads
your files and writes copies into a new tree. If you hate the result, delete the
output folder — or run `--undo` — and you have lost nothing.

Three modes are the exception, and all three **move** files, always within the
organized collection, never from your source: `--manual-only`, `--apply-csv`,
and `--resolve`. All three are recorded and undoable.

A run goes through these stages:

1. **Scan** — finds every audio file, computes a content hash, reads tags.
   Results are cached, so the second run over the same folder is much faster.
2. **Deduplicate** — groups files by audio content and keeps the best copy of
   each recording, then drops anything the collection already holds.
3. **Classify** — works out artist, album and title from tags, folder structure
   and filename, and scores how confident it is.
4. **Plan** — picks the destination folder and filename for every track, and
   writes `Reports/plan.csv`.
5. **Execute** — copies files, writes tags and sidecars, extracts album art.
6. **Report** — playlists, an HTML report, and several text reports.

---

## 4. Reading the output

```
Music_Organized/
├── Artist Name/
│   ├── Album Name/
│   │   ├── 01 - First Track.mp3
│   │   ├── 02 - Second Track.mp3
│   │   └── folder.jpg              album art
│   └── Singles/                    tracks with no known album
├── Manual Sort Needed/             could not be identified at all
├── Playlists/
│   ├── genre_Rock.m3u
│   └── year_1997.m3u
├── Reports/
│   ├── report.html                 START HERE
│   ├── plan.csv                    every decision, machine-readable
│   ├── review_queue.csv            editable; fix and feed back
│   ├── duplicates.txt              what was skipped and why
│   ├── near_duplicates.txt         probably the same song
│   ├── unclassified.txt
│   ├── artist_merges.txt           name variants folded together
│   ├── quality_report.txt
│   ├── organization_summary.txt
│   └── run_20260726_143000.log     full transcript of the run
└── .music_organizer.db             scan cache, library index, undo log
```

**`report.html`** is the one to open. It is a single self-contained page — no
internet needed — with the run summary, what was added, collapsible duplicate
groups, a list of redundant source files you might want to delete, the
low-confidence queue, and any errors.

**`plan.csv`** has one row per file with the source path, destination, chosen
artist/album/title, where each came from, the confidence score, and the audio
hash. Open it in a spreadsheet and sort by confidence to find the shaky calls.

**Do not delete `.music_organizer.db`.** It holds the scan cache, the index of
what is already filed, and the undo log. Losing it means re-hashing everything
and losing the ability to undo past runs. It can be rebuilt with `--reindex`,
but the undo history is gone for good.

---

## 5. How a track gets identified

Four sources, consulted in order of how much they can be trusted. Fields are
merged individually, so a file can take its artist from one source and its title
from another:

| Rank | Source | Notes |
| --- | --- | --- |
| 1 | **Embedded tags** | `albumartist` preferred over `artist`, so compilations stay in one folder |
| 2 | **Acoustic fingerprint** | Only with `--fingerprint`; see [section 10](#10-acoustic-fingerprinting) |
| 3 | **Folder structure** | The two folders nearest the file |
| 4 | **Filename** | See the patterns below |

Folder structure handles both `Artist/Album/track.mp3` and
`Genre/Artist/Album/track.mp3`. Generic container folders (`Downloads`, `mp3s`,
`Amazon MP3`, `Various Artists`, `misc`, …) are ignored when looking for an
artist, and `CD1` / `Disc 2` folders become disc numbers rather than album names.

Filename patterns understood:

| Filename | Artist | Album | Track | Title |
| --- | --- | --- | --- | --- |
| `Pink Floyd - Comfortably Numb.mp3` | Pink Floyd | *Singles* | | Comfortably Numb |
| `Nirvana - Nevermind - Lithium.mp3` | Nirvana | Nevermind | | Lithium |
| `05 - Nirvana - Lithium.mp3` | Nirvana | *Singles* | 5 | Lithium |
| `03 - Nirvana - Nevermind - Lithium.mp3` | Nirvana | Nevermind | 3 | Lithium |
| `Beatles_HeyJude.mp3` | Beatles | *Singles* | | HeyJude |
| `Nirvana-Lithium.mp3` | Nirvana | *Singles* | | Lithium |
| `Björk – Hyperballad.mp3` | Björk | *Singles* | | Hyperballad |
| `Radiohead, Creep.mp3` | Radiohead | *Singles* | | Creep |
| `07 Sunrise.mp3` | | | 7 | Sunrise |

Junk is stripped automatically. This:

```
03_-_Muse_-_Hysteria_(Official Video)_[320kbps]_www.example.com.mp3
```

becomes `Muse/Singles/03 - Hysteria.mp3`. Genuine qualifiers are kept, so
`Bohemian Rhapsody (Live)` stays `Bohemian Rhapsody (Live)`.

### Confidence

Every result gets a score from 0 to 100 based on where the artist came from,
where the title came from, and how much corroborating data exists.

| Situation | Score | What happens |
| --- | --- | --- |
| Full tags with album and track number | 90+ | Filed |
| Folder structure, album known | ~65 | Filed |
| `Artist - Title` filename | ~40 | Filed **and** listed in `review_queue.csv` |
| Single unspaced dash, e.g. `Artist-Title` | ~30 | Filed **and** listed for review |
| Nothing identifiable | 0 | `Manual Sort Needed/` |

Anything below `review_threshold` (default 55) is listed in
`review_queue.csv` but still goes into the main library. If you would rather
keep uncertain material out of the library entirely, set:

```ini
[CLASSIFY]
route_review = true
```

and it goes to `Review/Artist/Album/` instead.

**Only files where no artist could be determined at all end up in
`Manual Sort Needed`.** A track with a known artist but no album goes to
`Artist/Singles/`, not to the manual pile.

---

## 6. Tags: what is read and written

### Read

Across MP3 (ID3), MP4/M4A, FLAC/Ogg (Vorbis) and WMA (ASF):

| Field | ID3 | MP4 | Vorbis |
| --- | --- | --- | --- |
| Artist | `TPE1` | `©ART` | `ARTIST` |
| Album artist | `TPE2` | `aART` | `ALBUMARTIST` |
| Album | `TALB` | `©alb` | `ALBUM` |
| Title | `TIT2` | `©nam` | `TITLE` |
| Genre | `TCON` | `©gen` | `GENRE` |
| Year | `TDRC` / `TYER` | `©day` | `DATE` |
| Track | `TRCK` | `trkn` | `TRACKNUMBER` |
| Disc | `TPOS` | `disk` | `DISCNUMBER` |
| Artwork | `APIC` | `covr` | picture block |

Compound values are handled: `7/12` gives track 7, `1973-03-01` gives year 1973.
Numeric ID3 genre references like `(17)` are expanded to `Rock`.

**`albumartist` beats `artist`.** This is what keeps a compilation in one folder
instead of scattering it across one folder per track artist.

### Written

Only with `--write-tags`, and **only into the organized copies — never your
source files**. It writes `artist`, `albumartist`, `album`, `title`,
`tracknumber`, `discnumber`, `genre` and `date` using the normalized values, so
the library describes itself correctly on any future pass.

```bash
python music_organizer.py "D:\Music" --write-tags
```

Off by default. It is safe — a bad result is fixed by deleting the output and
re-running — but it is still a modification, so you opt in.

### Album art

Embedded artwork is written to the album folder as `folder.jpg` (or
`folder.png`). If nothing is embedded, a loose `cover.jpg`, `folder.jpg`,
`front.jpg`, `album.jpg` or `artwork.jpg` sitting next to the source audio is
copied instead. Embedded art wins when both exist.

### Sidecar files

Companion files are copied alongside the audio **and renamed to match**, so
players keep the association:

```
Source:  01 track.mp3  +  01 track.lrc
Output:  01 - Lithium.mp3  +  01 - Lithium.lrc
```

Default extensions: `.lrc .cue .log .nfo .txt`. Change with
`SIDECARS.sidecar_extensions`.

---

## 7. How duplicates are detected

**The tool hashes the audio, not the file.** ID3 headers, ID3v1/APEv2/Lyrics3
trailers, FLAC metadata blocks, and everything outside an MP4 `mdat` atom are
excluded from the hash.

This matters. Two copies of the same track that differ only by a tag edit, a
different embedded cover, or different padding produce *different* whole-file
checksums but the *same* audio hash. Whole-file hashing is why duplicates
survived in earlier versions.

When duplicates are found, the survivor is chosen by:

1. Lossless format beats lossy
2. Higher bitrate
3. More complete tags
4. Larger file

Every skipped copy is listed in `duplicates.txt` and in the HTML report, with
the reason. **Nothing is deleted** — the redundant files are still in your
source folder, and the report gives you the list if you want to act on it.

### What this does *not* catch

A 128kbps rip and a 320kbps rip of the same song have genuinely different audio,
so their hashes differ. They appear in `near_duplicates.txt` as suspected
matches. To actually merge them you need fingerprinting:

```ini
[FINGERPRINT]
merge_duplicate_encodes = true
```

with `--fingerprint`. This is off by default because it discards a file based on
a network lookup rather than a byte-level fact.

---

## 8. Naming rules

### Files

Default output is `NN - Title.ext` with a zero-padded track number, so players
sort 1, 2, … 10 correctly instead of 1, 10, 11, 2. Multi-disc releases get a
disc prefix: `2-01 - The Lamia.mp3`.

Change the pattern:

```ini
[NAMING]
filename_template = {track} - {title}
filename_template_no_track = {title}
```

Available fields: `{track}`, `{title}`, `{artist}`, `{disc}`.

To keep your original filenames and only reorganize folders:

```bash
python music_organizer.py "D:\Music" --no-rename
```

### Artist folders

Name variants are folded into a single folder:

```
The Beatles  ┐
beatles      ├─→  The Beatles/
BEATLES      │
Beatles, The ┘

Simon & Garfunkel  ─→  Simon and Garfunkel/   (whichever spelling wins)
Björk / Bjork      ─→  Björk/
```

The winning spelling is chosen by: source quality (a tag beats a folder name),
then proper casing, then frequency. See `artist_merges.txt` for what was folded.

### Casing

A name that is **entirely uppercase or entirely lowercase** gets re-cased:

```
PINK FLOYD                 →  Pink Floyd
THE DARK SIDE OF THE MOON  →  The Dark Side of the Moon
paul mccartney             →  Paul McCartney
```

A name with deliberate mixed case is **never touched** — `Sigur Rós` and
`iamamiwhoami` come through unchanged.

Acronyms without vowels are detected automatically and left alone: `MGMT`,
`TLC`, `XTC`, `BBC`, `AC/DC`. Acronyms *with* vowels can't be told apart from
ordinary words, so they come from a built-in exception list: `ABBA`, `KISS`,
`R.E.M.`, `AFI`, `INXS`, `deadmau5`, `will.i.am`, and others.

Add your own:

```ini
[NAMING]
name_exceptions = THE KLF, dEUS, ACME
```

Or turn the whole thing off with `titlecase_names = false`.

### Safety

Characters illegal on Windows (`< > : " / \ | ? *`) are removed. Reserved device
names (`CON`, `NUL`, `AUX`, `PRN`, `COM1`…) get an underscore appended. Long
names are capped on a word boundary, and paths past the 260-character limit use
the Windows extended-length form so the copy still succeeds.

---

## 9. Cleaning up the leftovers

Two ways, depending on how much there is.

### A. Spreadsheet — good for bulk

1. Open `Music_Organized\Reports\review_queue.csv`.
2. Fill in `artist`, `album`, `title`, `track` for the rows you care about.
   Leave a row's `artist` blank to skip it.
3. Save as CSV and apply:

```bash
python music_organizer.py "D:\Music_Organized" --apply-csv "D:\Music_Organized\Reports\review_queue.csv"
```

Rows you did not edit are no-ops, so you can apply the same file repeatedly as
you work through it over several sittings.

### B. Interactive — good for a quick pass

```bash
python music_organizer.py "D:\Music_Organized" --resolve
```

Files are grouped by the pattern the parser recognized and by any artist it
managed to extract, so a folder of `Artist - Track.mp3` from one artist is a
single question rather than fifty:

```
--- Group 1/3: 12 file(s), pattern 'artist_title'
      Autechre - Amber.mp3
      Autechre - Gantz Graf.mp3
      ... and 10 more
  Artist (Enter=skip, q=quit): Autechre
  Album (Enter=Singles): Amber
```

Both routes are fully undoable.

### C. Rename the files yourself

If you rename files in `Manual Sort Needed` to `Artist - Title.mp3`, re-ingest
them with:

```bash
python music_organizer.py "D:\Music_Organized" --manual-only "D:\Music_Organized\Manual Sort Needed"
```

---

## 10. Acoustic fingerprinting

For files with no usable metadata whatsoever — no tags, meaningless filename,
dumped in a flat folder — the last resort is to identify the audio itself.

### Setup

1. Install Chromaprint and put `fpcalc` on your PATH:
   <https://acoustid.org/chromaprint>
   (or set `FINGERPRINT.fpcalc_path` to its full path)
2. Get a free API key: <https://acoustid.org/new-application>

```bash
set ACOUSTID_API_KEY=your-key-here
python music_organizer.py "D:\Music" --fingerprint
```

Or put the key in the config file under `[FINGERPRINT] api_key`.

### Behaviour

- If `fpcalc` or the key is missing, you get a warning with the install link and
  **the run continues normally** without fingerprinting.
- Only tracks that reached the review queue or manual sort are submitted. There
  is no point spending a rate-limited network lookup on a well-tagged file. Set
  `only_unidentified = false` to submit everything.
- Requests are throttled to AcoustID's 3-per-second guidance.
- **Every lookup is cached in the database, including misses.** A second run
  costs nothing and makes no network calls.
- A match may overwrite a value guessed from a folder name or filename, but
  **never one read from your embedded tags**. Your tags always win.
- Matches scoring below `min_score` (default 0.7) are discarded.

### Merging different encodes

With fingerprinting on, tracks are also grouped by AcoustID recording ID, which
catches a 128kbps and a 320kbps rip of the same performance. To actually collapse
them to the best copy:

```ini
[FINGERPRINT]
merge_duplicate_encodes = true
```

Off by default — see [section 7](#7-how-duplicates-are-detected).

---

## 11. Adding music later

```bash
python music_organizer.py "D:\NewAlbums" --incremental --output "D:\Music_Organized"
```

Anything already in the collection is skipped by audio hash — including copies
that were renamed or retagged since they were filed. Only genuinely new material
is added.

The first time you point this at a collection built by an older version, it
hashes the existing files to build its index. That is a one-off cost; later runs
are fast.

Plain runs are idempotent too. Running the same command twice over the same
source adds nothing the second time.

---

## 12. Undoing a run

Every run gets an ID, and every file placement is logged.

```bash
# What has been run against this collection?
python music_organizer.py "D:\Music_Organized" --list-runs

# Undo the most recent run
python music_organizer.py "D:\Music_Organized" --undo

# Undo a specific run
python music_organizer.py "D:\Music_Organized" --undo 7
```

Copies are deleted, moves are restored to where they came from, and folders left
empty are removed.

**Safety:** you are asked to confirm (use `--yes` to skip the prompt in a
script), and each file's audio is re-hashed before it is touched. Anything you
edited or replaced since the run is skipped with a warning rather than deleted.
Your source files are never affected.

---

## 13. Every command-line option

```
python music_organizer.py <path> [options]
```

For `--undo`, `--list-runs`, `--apply-csv`, `--resolve` and `--manual-only`, the
path is the **organized collection**. Otherwise it is the source folder to read.

### Choosing what to do

| Option | Effect |
| --- | --- |
| *(none)* | Organize the source folder into a collection |
| `--dry-run` | Plan only. Writes `Reports/plan.csv`, touches nothing else |
| `--incremental` | Add new music to an existing collection. Requires `--output` |
| `--manual-only DIR` | Re-ingest a manual sort folder |
| `--apply-csv FILE` | Re-file tracks from a corrected `review_queue.csv` |
| `--resolve` | Work through the manual pile interactively, in batches |
| `--undo [RUN_ID]` | Reverse a run. Defaults to the most recent |
| `--list-runs` | Show the run history |
| `--reindex` | Rebuild the library index from the collection on disk |

### Changing how it works

| Option | Effect |
| --- | --- |
| `--output DIR` | Destination. Default: `<path>/../Music_Organized` |
| `--no-rename` | Keep original filenames; only reorganize folders |
| `--write-tags` | Write resolved metadata into the organized copies |
| `--fingerprint` | Identify unidentified tracks via Chromaprint/AcoustID |
| `--workers N` | Hashing processes. `0` = auto, `1` = serial |
| `--interactive` | Show a sample of the plan and confirm before writing |
| `--config FILE` | Use a specific configuration file |

### Output control

| Option | Effect |
| --- | --- |
| `--verbose` | More console detail |
| `--quiet` | Warnings and errors only |
| `--yes` | Skip the confirmation prompt for `--undo` |
| `--version` | Print the version and exit |
| `--help` | Full option list |

---

## 14. Every configuration setting

`music_organizer_config.ini` is created on first run next to your source folder.
Missing keys are backfilled automatically on later runs, so **you never need to
delete it when upgrading**.

### `[GENERAL]`

| Key | Default | Meaning |
| --- | --- | --- |
| `interactive_mode` | `false` | Always preview and confirm |
| `create_playlists` | `true` | Generate M3U playlists |
| `extract_album_art` | `true` | Save embedded artwork as `folder.jpg` |
| `analyze_quality` | `true` | Collect bitrate and format statistics |
| `find_near_duplicates` | `true` | Look for probable same-song matches |
| `html_report` | `true` | Write `Reports/report.html` |

### `[FOLDERS]`

| Key | Default | Meaning |
| --- | --- | --- |
| `skip_folders` | `System Volume Information, $RECYCLE.BIN, .Trash` | Folder names to ignore. Matched per path component, not as substrings |
| `manual_sort_folder` | `Manual Sort Needed` | Name of the quarantine folder |

### `[NAMING]`

| Key | Default | Meaning |
| --- | --- | --- |
| `rename_files` | `true` | Normalize output filenames |
| `filename_template` | `{track} - {title}` | Used when a track number is known |
| `filename_template_no_track` | `{title}` | Used when it is not |
| `max_component_length` | `100` | Character cap per folder/file name |
| `titlecase_names` | `true` | Re-case ALL-CAPS and all-lowercase names |
| `name_exceptions` | *(empty)* | Extra names whose casing must be preserved |
| `write_tags` | `false` | Write metadata into the organized copies |

### `[CLASSIFY]`

| Key | Default | Meaning |
| --- | --- | --- |
| `review_threshold` | `55` | Confidence below which a track is queued for review |
| `route_review` | `false` | `true` files uncertain tracks under `Review/` |
| `review_folder` | `Review` | Name of that folder |

### `[FINGERPRINT]`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Always fingerprint, without needing `--fingerprint` |
| `api_key` | *(empty)* | AcoustID key. Falls back to `ACOUSTID_API_KEY` |
| `fpcalc_path` | *(empty)* | Full path to `fpcalc` if it is not on PATH |
| `min_score` | `0.7` | Reject matches below this confidence |
| `only_unidentified` | `true` | `false` submits every track |
| `merge_duplicate_encodes` | `false` | Treat matching recordings as duplicates |
| `rate_limit_per_second` | `3` | Request throttle |

### `[PERFORMANCE]`

| Key | Default | Meaning |
| --- | --- | --- |
| `workers` | `0` | Hashing processes. `0` = auto, `1` = serial |

### `[SIDECARS]`

| Key | Default | Meaning |
| --- | --- | --- |
| `copy_sidecars` | `true` | Copy per-track companion files |
| `sidecar_extensions` | `.lrc, .cue, .log, .nfo, .txt` | Which ones |
| `copy_folder_images` | `true` | Copy a loose cover image as `folder.jpg` |

### `[QUALITY]`

| Key | Default | Meaning |
| --- | --- | --- |
| `min_bitrate` | `128` | Threshold for the low-quality report |
| `preferred_format` | `mp3` | Recorded for reporting |
| `report_low_quality` | `true` | Include the low-bitrate section |

### `[PLAYLISTS]`

| Key | Default | Meaning |
| --- | --- | --- |
| `create_by_genre` | `true` | One playlist per genre |
| `create_by_year` | `true` | One playlist per year |
| `create_by_artist` | `false` | One playlist per artist |
| `min_songs_for_playlist` | `5` | Skip playlists smaller than this |

---

## 15. Recipes

**Careful first run on a large, messy library**

```bash
python music_organizer.py "D:\Music" --dry-run
# review Reports\plan.csv, tune the config
python music_organizer.py "D:\Music" --interactive
```

**Maximum-effort pass: identify everything and tag it**

```bash
set ACOUSTID_API_KEY=your-key-here
python music_organizer.py "D:\Music" --fingerprint --write-tags
```

**Prepare a library for a phone player like Musicolet**

```bash
python music_organizer.py "D:\Music" --write-tags --output "E:\Card\Music"
```

Artist/Album folders with zero-padded track numbers, `folder.jpg` art, and
correct embedded tags is exactly what these players want.

**Keep uncertain material out of the main library**

```ini
[CLASSIFY]
route_review = true
```

**Reorganize folders but keep every filename as-is**

```bash
python music_organizer.py "D:\Music" --no-rename
```

**Slow machine, or a network drive**

```bash
python music_organizer.py "D:\Music" --workers 1
```

**Weekly top-up from a downloads folder**

```bash
python music_organizer.py "D:\Downloads\Music" --incremental --output "D:\Music_Organized"
```

---

## 16. Troubleshooting

**"Install 'mutagen' for metadata reading"**
Run `pip install -r requirements.txt`. Without it the tool can only use folder
names and filenames, and most files will end up in `Manual Sort Needed`.

**Everything landed in `Manual Sort Needed`**
Usually means no tags and unhelpful filenames. In order of effort: try
`--fingerprint`, use `--resolve` to answer in batches, or edit
`review_queue.csv` and apply it. Check `plan.csv` to see what it inferred and
from where.

**An artist folder is named `PINK FLOYD` / `pink floyd`**
Check `titlecase_names` is `true`. If the name is a legitimate acronym with a
vowel, add it to `name_exceptions`.

**Two folders for the same artist**
They differ by more than case, accents or a leading `The`. Fix the source tags,
or file both under one name with `--apply-csv`. `artist_merges.txt` shows what
*was* merged.

**Duplicates still in the output**
Check `near_duplicates.txt` — they are probably different encodes, which have
genuinely different audio. Use `--fingerprint` with `merge_duplicate_encodes`.
Note also that two *different* songs sharing a filename get a ` (2)` suffix;
that is a collision, not a duplicate.

**"Cannot copy … The system cannot find the path specified"**
A path length problem. Long paths are handled automatically, but you can lower
`max_component_length` to shorten names further.

**The run was slower than expected**
The first run hashes everything. Later runs use the cache. If files keep getting
re-hashed, something is changing their modification time — a sync client, for
example.

**I want to start completely over**
Delete the output folder, including `.music_organizer.db`, and run again. Your
source files were never modified.

**Where do errors go?**
The end of `Reports/organization_summary.txt`, the Errors section of
`report.html`, and in full detail in `Reports/run_<timestamp>.log`.

---

## See also

- [README.md](README.md) — overview and quick reference
- [AUDIT.md](AUDIT.md) — why the tool works the way it does
- [CHANGELOG.md](CHANGELOG.md) — what changed and when
- [CLAUDE.md](CLAUDE.md) — architecture notes for contributors
