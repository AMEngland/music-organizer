# Music Organizer Project

A comprehensive Python-based music library organization tool that sorts audio files using metadata, directory structure, and filename parsing.

## Features

- **Smart Organization**: Sorts music into Artist/Album/Track structure
- **Metadata Reading**: Uses embedded ID3 tags when available (via mutagen)
- **Duplicate Detection**: MD5 hashing prevents duplicate files
- **Album Art Extraction**: Saves embedded artwork as folder.jpg
- **Playlist Generation**: Creates M3U playlists by genre, year, artist
- **Quality Analysis**: Reports bitrate and format statistics
- **Near-Duplicate Detection**: Finds different versions of same songs
- **Interactive Preview**: Shows organization plan before execution
- **Manual Sort Support**: Handles unparseable files for manual review
- **Incremental Updates**: Add new music to existing organized collection

## Quick Start

```bash
# Install dependencies
pip install mutagen pillow

# Organize your music collection
python music_organizer.py "C:\path\to\your\Music"

# Interactive mode (preview before organizing)
python music_organizer.py "C:\path\to\your\Music" --interactive

# Organize manual sort folder into existing collection
python music_organizer.py "C:\path\to\Music_Organized" --manual-only "C:\path\to\Music_Organized\Manual Sort Needed"
```

## Output Structure

```
Music_Organized/
├── Artist Name/
│   ├── Album Name/
│   │   ├── track1.mp3
│   │   ├── track2.mp3
│   │   └── folder.jpg (album art)
│   └── Singles/
├── Manual Sort Needed/ (files needing manual review)
├── Playlists/
│   ├── genre_Rock.m3u
│   └── year_2023.m3u
└── Reports/
    ├── quality_report.txt
    ├── near_duplicates.txt
    └── organization_summary.txt
```

## Configuration

The tool creates `music_organizer_config.ini` with customizable settings:

- Enable/disable features (playlists, album art, quality analysis)
- Set minimum bitrate thresholds
- Configure playlist creation rules
- Specify folders to skip

## Files

- `music_organizer.py` - Main organizer with all features
- `add_new_music.py` - Add new files to existing organized collection
- `music_organizer_config.ini` - Generated configuration file

## Development Notes

- Originally started as simple folder organization
- Evolved to include metadata reading, duplicate detection, and reporting
- Uses MD5 hashing for reliable duplicate detection
- Preserves original files (copies, doesn't move)
- Conservative filename parsing to avoid false artist assignments

## Manual File Naming Convention

For files in the "Manual Sort Needed" folder, use this naming format:

**`Artist - Track.mp3`**

Examples:
- `Pink Floyd - Comfortably Numb.mp3` → Pink Floyd/Singles/
- `Beatles - Hey Jude.mp3` → Beatles/Singles/
- `Radiohead - Creep.mp3` → Radiohead/Singles/

**Alternative formats:**
- `Artist - Album - Track.mp3` → Artist/Album/
- `Artist_Track.mp3` (underscore separator) → Artist/Singles/
- Remove track numbers: `01 - Song.mp3` → `Song.mp3`
- Files with embedded metadata are parsed automatically

**Avoid:**
- Single words as artists ("The", "A", "I") - these are filtered out
- Numbers only as artist names
- Common words ("and", "or", "but") as artist names

## Usage Examples

**Basic organization:**
```bash
python music_organizer.py "C:\path\to\your\Music"
```

**Add new music later:**
```bash
python add_new_music.py
# Prompts for new music folder path
```

**Organize manual files after manual sorting:**
```bash
python music_organizer.py "C:\path\to\Music_Organized" --manual-only "C:\path\to\Music_Organized\Manual Sort Needed"
```

Perfect for preparing music collections for mobile players like Musicolet that work best with organized Artist/Album structures.
