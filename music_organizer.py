#!/usr/bin/env python3

import os
import shutil
import hashlib
import json
import configparser
from pathlib import Path
import re
from collections import defaultdict, Counter
from datetime import datetime
import argparse

# Try to import optional dependencies
try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3NoHeaderError
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

class AdvancedMusicOrganizer:
    def __init__(self, music_path, config_file=None):
        self.music_path = Path(music_path)
        self.organized_path = self.music_path.parent / "Music_Organized"
        self.config_file = config_file or self.music_path.parent / "music_organizer_config.ini"
        
        # Load configuration
        self.config = self.load_config()
        
        # Initialize tracking
        self.duplicates = {}
        self.near_duplicates = defaultdict(list)
        self.quality_stats = defaultdict(int)
        self.album_art = {}
        
        self.stats = {
            'total_files': 0,
            'organized': 0,
            'duplicates_removed': 0,
            'near_duplicates_found': 0,
            'folders_created': 0,
            'manual_sort': 0,
            'metadata_parsed': 0,
            'album_art_extracted': 0,
            'errors': []
        }
    
    def load_config(self):
        """Load or create configuration file"""
        config = configparser.ConfigParser()
        
        # Default configuration
        defaults = {
            'GENERAL': {
                'interactive_mode': 'false',
                'create_playlists': 'true',
                'extract_album_art': 'true',
                'analyze_quality': 'true',
                'find_near_duplicates': 'true'
            },
            'FOLDERS': {
                'skip_folders': 'System Volume Information, $RECYCLE.BIN, .Trash',
                'manual_sort_folder': 'Manual Sort Needed'
            },
            'QUALITY': {
                'min_bitrate': '128',
                'preferred_format': 'mp3',
                'report_low_quality': 'true'
            },
            'PLAYLISTS': {
                'create_by_genre': 'true',
                'create_by_year': 'true', 
                'create_by_artist': 'false',
                'min_songs_for_playlist': '5'
            }
        }
        
        if self.config_file.exists():
            config.read(self.config_file)
        else:
            config.read_dict(defaults)
            self.save_config(config)
        
        return config
    
    def save_config(self, config):
        """Save configuration to file"""
        with open(self.config_file, 'w') as f:
            config.write(f)
    
    def get_file_hash(self, file_path):
        """Generate MD5 hash for duplicate detection"""
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            return None
    
    def get_audio_fingerprint(self, file_path):
        """Create audio fingerprint for near-duplicate detection"""
        try:
            # Simple fingerprint: size + first/last 1KB
            stat = file_path.stat()
            with open(file_path, 'rb') as f:
                start = f.read(1024)
                f.seek(-1024, 2)
                end = f.read(1024)
            return f"{stat.st_size}_{hashlib.md5(start + end).hexdigest()[:8]}"
        except:
            return None
    
    def clean_name(self, name):
        """Clean filename/folder names"""
        if not name:
            return "Unknown"
        name = str(name)
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        name = name.strip('.')
        return name or "Unknown"
    
    def read_metadata(self, file_path):
        """Extract metadata from audio file"""
        if not MUTAGEN_AVAILABLE:
            return None, None, None, None, None, None
        
        try:
            audio_file = MutagenFile(file_path)
            if audio_file is None:
                return None, None, None, None, None, None
            
            # Extract metadata
            artist = album = title = genre = year = None
            bitrate = getattr(audio_file.info, 'bitrate', 0)
            
            # Common tag mappings
            tag_mappings = {
                'artist': ['TPE1', 'ARTIST', 'artist', '\xa9ART'],
                'album': ['TALB', 'ALBUM', 'album', '\xa9alb'],
                'title': ['TIT2', 'TITLE', 'title', '\xa9nam'],
                'genre': ['TCON', 'GENRE', 'genre', '\xa9gen'],
                'year': ['TDRC', 'DATE', 'date', '\xa9day', 'TYER']
            }
            
            for field, tags in tag_mappings.items():
                for tag in tags:
                    if tag in audio_file:
                        value = audio_file[tag]
                        if isinstance(value, list) and value:
                            value = value[0]
                        if value:
                            if field == 'artist':
                                artist = str(value)
                            elif field == 'album':
                                album = str(value)
                            elif field == 'title':
                                title = str(value)
                            elif field == 'genre':
                                genre = str(value)
                            elif field == 'year':
                                year = str(value)[:4]  # Extract year only
                            break
            
            return artist, album, title, genre, year, bitrate
            
        except Exception:
            return None, None, None, None, None, None
    
    def extract_album_art(self, file_path, artist, album):
        """Extract album artwork"""
        if not MUTAGEN_AVAILABLE or not PIL_AVAILABLE:
            return None
        
        if not self.config.getboolean('GENERAL', 'extract_album_art'):
            return None
        
        try:
            audio_file = MutagenFile(file_path)
            if audio_file is None:
                return None
            
            # Look for album art in various tag formats
            art_tags = ['APIC:', 'covr', 'METADATA_BLOCK_PICTURE']
            
            for tag in art_tags:
                if tag in audio_file:
                    art_data = audio_file[tag]
                    if isinstance(art_data, list):
                        art_data = art_data[0]
                    
                    # Save album art
                    art_dir = self.organized_path / artist / album
                    art_dir.mkdir(parents=True, exist_ok=True)
                    art_file = art_dir / "folder.jpg"
                    
                    if not art_file.exists():
                        try:
                            if hasattr(art_data, 'data'):
                                with open(art_file, 'wb') as f:
                                    f.write(art_data.data)
                            else:
                                with open(art_file, 'wb') as f:
                                    f.write(art_data)
                            
                            self.stats['album_art_extracted'] += 1
                            return str(art_file)
                        except:
                            pass
            
        except Exception:
            pass
        
        return None
    
    def analyze_quality(self, file_path, metadata):
        """Analyze audio quality"""
        if not self.config.getboolean('GENERAL', 'analyze_quality'):
            return
        
        _, _, _, _, _, bitrate = metadata
        format_ext = file_path.suffix.lower()
        
        # Track quality statistics
        self.quality_stats[f'format_{format_ext}'] += 1
        
        if bitrate:
            if bitrate < 128:
                self.quality_stats['low_quality'] += 1
            elif bitrate >= 320:
                self.quality_stats['high_quality'] += 1
            else:
                self.quality_stats['medium_quality'] += 1
    
    def check_near_duplicates(self, file_path, metadata):
        """Check for near-duplicate files"""
        if not self.config.getboolean('GENERAL', 'find_near_duplicates'):
            return
        
        artist, album, title, _, _, _ = metadata
        if artist and title:
            key = f"{artist.lower()}_{title.lower()}"
            self.near_duplicates[key].append(file_path)
    
    def parse_metadata(self, file_path):
        """Extract metadata from all sources"""
        # Read file metadata
        metadata = self.read_metadata(file_path)
        meta_artist, meta_album, meta_title, meta_genre, meta_year, bitrate = metadata
        
        # Analyze quality and duplicates
        self.analyze_quality(file_path, metadata)
        self.check_near_duplicates(file_path, metadata)
        
        # Use metadata if available
        if meta_artist and len(meta_artist.strip()) > 1:
            self.stats['metadata_parsed'] += 1
            artist = self.clean_name(meta_artist)
            album = self.clean_name(meta_album) if meta_album else "Singles"
            track = self.clean_name(meta_title) if meta_title else self.clean_name(file_path.stem)
            
            # Extract album art
            self.extract_album_art(file_path, artist, album)
            
            return artist, album, track, meta_genre, meta_year
        
        # Fallback to directory/filename parsing
        return self.parse_from_path(file_path)
    
    def parse_from_path(self, file_path):
        """Parse metadata from path and filename"""
        path_parts = file_path.parts
        music_index = next((i for i, part in enumerate(path_parts) if part == 'Music'), -1)
        
        if music_index == -1:
            return self.parse_filename_only(file_path)
        
        # Skip folders from config
        skip_folders = set(self.config.get('FOLDERS', 'skip_folders').split(', '))
        skip_folders.update({'mp3s', 'new mp3s', 'Amazon MP3', 'Google Music Downloads', 
                            'Converted Music', 'misc', 'Various', 'Unknown Artist'})
        
        dirs_after_music = [d for d in path_parts[music_index + 1:-1] 
                           if d not in skip_folders and not d.startswith('Part')]
        
        if len(dirs_after_music) >= 1:
            artist = dirs_after_music[0]
            album = dirs_after_music[1] if len(dirs_after_music) >= 2 else "Singles"
            track = re.sub(r'^\d+\.?\s*[-.]?\s*', '', file_path.stem)
            
            return self.clean_name(artist), self.clean_name(album), self.clean_name(track), None, None
        
        return self.parse_filename_only(file_path)
    
    def parse_filename_only(self, file_path):
        """Parse from filename only"""
        filename = file_path.stem
        clean_filename = re.sub(r'^\d+\.?\s*[-.]?\s*', '', filename)
        
        if ' - ' in clean_filename:
            parts = clean_filename.split(' - ', 1)
            potential_artist = parts[0].strip()
            potential_track = parts[1].strip()
            
            common_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 
                           'to', 'for', 'of', 'with', 'by', 'they', 'these', 'this', 
                           'that', 'i', 'you', 'we', 'my', 'your', 'his', 'her'}
            
            if (len(potential_artist) >= 2 and 
                len(potential_track) >= 2 and
                potential_artist.lower() not in common_words and
                not potential_artist.isdigit()):
                
                return self.clean_name(potential_artist), "Singles", self.clean_name(potential_track), None, None
        
        return "Manual Sort Needed", "", self.clean_name(filename), None, None
    
    def create_playlists(self, organized_files):
        """Create M3U playlists"""
        if not self.config.getboolean('GENERAL', 'create_playlists'):
            return
        
        playlist_dir = self.organized_path / "Playlists"
        playlist_dir.mkdir(exist_ok=True)
        
        # Organize by metadata
        by_genre = defaultdict(list)
        by_year = defaultdict(list)
        by_artist = defaultdict(list)
        
        min_songs = self.config.getint('PLAYLISTS', 'min_songs_for_playlist')
        
        for files in organized_files.values():
            for file_info in files:
                if file_info.get('genre'):
                    by_genre[file_info['genre']].append(file_info)
                if file_info.get('year'):
                    by_year[file_info['year']].append(file_info)
                by_artist[file_info['artist']].append(file_info)
        
        # Create playlists
        playlist_types = [
            ('genre', by_genre, self.config.getboolean('PLAYLISTS', 'create_by_genre')),
            ('year', by_year, self.config.getboolean('PLAYLISTS', 'create_by_year')),
            ('artist', by_artist, self.config.getboolean('PLAYLISTS', 'create_by_artist'))
        ]
        
        for playlist_type, data, enabled in playlist_types:
            if not enabled:
                continue
                
            for key, files in data.items():
                if len(files) >= min_songs:
                    playlist_file = playlist_dir / f"{playlist_type}_{self.clean_name(key)}.m3u"
                    with open(playlist_file, 'w', encoding='utf-8') as f:
                        f.write("#EXTM3U\n")
                        for file_info in files:
                            rel_path = Path(file_info['target_path']).relative_to(self.organized_path)
                            f.write(f"../{rel_path}\n")
    
    def generate_reports(self):
        """Generate analysis reports"""
        reports_dir = self.organized_path / "Reports"
        reports_dir.mkdir(exist_ok=True)
        
        # Quality report
        if self.config.getboolean('GENERAL', 'analyze_quality'):
            quality_report = reports_dir / "quality_report.txt"
            with open(quality_report, 'w') as f:
                f.write("AUDIO QUALITY ANALYSIS\n")
                f.write("=" * 40 + "\n\n")
                
                for key, count in self.quality_stats.items():
                    f.write(f"{key}: {count}\n")
                
                if self.quality_stats.get('low_quality', 0) > 0:
                    f.write(f"\nRecommendation: {self.quality_stats['low_quality']} files have low bitrate (<128kbps)\n")
        
        # Near-duplicates report
        if self.config.getboolean('GENERAL', 'find_near_duplicates'):
            duplicates_report = reports_dir / "near_duplicates.txt"
            with open(duplicates_report, 'w') as f:
                f.write("NEAR-DUPLICATE ANALYSIS\n")
                f.write("=" * 40 + "\n\n")
                
                for key, files in self.near_duplicates.items():
                    if len(files) > 1:
                        f.write(f"\nPossible duplicates for '{key}':\n")
                        for file_path in files:
                            f.write(f"  - {file_path}\n")
                        self.stats['near_duplicates_found'] += len(files) - 1
        
        # Organization summary
        summary_report = reports_dir / "organization_summary.txt"
        with open(summary_report, 'w') as f:
            f.write("MUSIC ORGANIZATION SUMMARY\n")
            f.write("=" * 40 + "\n")
            f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            for key, value in self.stats.items():
                if key != 'errors':
                    f.write(f"{key.replace('_', ' ').title()}: {value}\n")
    
    def get_all_music_files(self):
        """Find all music files"""
        music_extensions = {'.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.wma'}
        files = []
        
        skip_folders = set(self.config.get('FOLDERS', 'skip_folders').split(', '))
        
        for file_path in self.music_path.rglob('*'):
            # Skip system folders
            if any(skip in str(file_path) for skip in skip_folders):
                continue
                
            if file_path.is_file() and file_path.suffix.lower() in music_extensions:
                files.append(file_path)
        
        return files
    
    def interactive_preview(self, organized_files):
        """Show preview of organization in interactive mode"""
        if not self.config.getboolean('GENERAL', 'interactive_mode'):
            return True
        
        print("\nPREVIEW OF ORGANIZATION:")
        print("=" * 50)
        
        sample_count = 0
        for key, files in list(organized_files.items())[:10]:
            print(f"\n{key}:")
            for file_info in files[:3]:
                print(f"  - {file_info['filename']}")
            if len(files) > 3:
                print(f"  ... and {len(files) - 3} more")
            sample_count += 1
        
        if len(organized_files) > 10:
            print(f"\n... and {len(organized_files) - 10} more folders")
        
        response = input("\nProceed with organization? (y/n): ").lower()
        return response.startswith('y')
    
    def organize(self):
        """Main organization function"""
        print("Advanced Music Organizer")
        print("=" * 40)
        
        # Check dependencies
        if MUTAGEN_AVAILABLE:
            print("✓ Metadata reading enabled")
        else:
            print("⚠ Install 'mutagen' for metadata reading")
        
        if PIL_AVAILABLE:
            print("✓ Album art extraction enabled")
        else:
            print("⚠ Install 'pillow' for album art extraction")
        
        # Create directories
        self.organized_path.mkdir(exist_ok=True)
        manual_sort_dir = self.organized_path / self.config.get('FOLDERS', 'manual_sort_folder')
        manual_sort_dir.mkdir(exist_ok=True)
        
        # Get all files
        all_files = self.get_all_music_files()
        print(f"\nFound {len(all_files)} music files")
        
        # Process files
        organized_files = defaultdict(list)
        
        for i, file_path in enumerate(all_files):
            self.stats['total_files'] += 1
            
            if (i + 1) % 100 == 0:
                print(f"Processing {i + 1}/{len(all_files)}...")
            
            # Check for duplicates
            file_hash = self.get_file_hash(file_path)
            if not file_hash:
                continue
                
            if file_hash in self.duplicates:
                self.stats['duplicates_removed'] += 1
                continue
            
            self.duplicates[file_hash] = file_path
            
            # Parse metadata
            artist, album, track, genre, year = self.parse_metadata(file_path)
            
            if artist == "Manual Sort Needed":
                key = "Manual Sort Needed/"
                self.stats['manual_sort'] += 1
                target_dir = manual_sort_dir
            else:
                key = f"{artist}/{album}"
                target_dir = self.organized_path / artist / album
            
            organized_files[key].append({
                'original_path': file_path,
                'artist': artist,
                'album': album,
                'track': track,
                'genre': genre,
                'year': year,
                'filename': file_path.name,
                'target_dir': target_dir
            })
        
        # Interactive preview
        if not self.interactive_preview(organized_files):
            print("Organization cancelled.")
            return
        
        # Create organized structure
        print("\nOrganizing files...")
        for artist_album, files in organized_files.items():
            target_dir = files[0]['target_dir']
            
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                if artist_album != "Manual Sort Needed/":
                    self.stats['folders_created'] += 1
                
                for file_info in files:
                    target_file = target_dir / file_info['filename']
                    
                    # Handle conflicts
                    counter = 1
                    while target_file.exists():
                        stem = file_info['original_path'].stem
                        suffix = file_info['original_path'].suffix
                        target_file = target_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                    
                    shutil.copy2(file_info['original_path'], target_file)
                    file_info['target_path'] = target_file
                    self.stats['organized'] += 1
                    
            except Exception as e:
                error_msg = f"Error organizing {artist_album}: {str(e)}"
                self.stats['errors'].append(error_msg)
        
        # Create playlists and reports
        print("Creating playlists...")
        self.create_playlists(organized_files)
        
        print("Generating reports...")
        self.generate_reports()
        
        # Print final results
        print("\n" + "="*60)
        print("ORGANIZATION COMPLETE!")
        print("="*60)
        for key, value in self.stats.items():
            if key != 'errors':
                print(f"{key.replace('_', ' ').title()}: {value}")
        
        if self.stats['errors']:
            print(f"\nErrors: {len(self.stats['errors'])} (see logs)")
        
        print(f"\nOutput location: {self.organized_path}")
        print(f"Configuration: {self.config_file}")

def organize_manual_folder(manual_folder_path, organized_collection_path):
    """Organize files from manual sort folder into existing collection"""
    manual_path = Path(manual_folder_path)
    collection_path = Path(organized_collection_path)
    
    if not manual_path.exists():
        print(f"Manual folder not found: {manual_path}")
        return
    
    if not collection_path.exists():
        print(f"Organized collection not found: {collection_path}")
        return
    
    # Create temporary organizer for manual files
    temp_organizer = AdvancedMusicOrganizer(manual_path)
    temp_organizer.organized_path = collection_path
    
    print(f"Organizing manual files from: {manual_path}")
    print(f"Into existing collection: {collection_path}")
    
    # Get files from manual folder
    manual_files = []
    for ext in {'.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.wma'}:
        manual_files.extend(manual_path.glob(f"*{ext}"))
    
    if not manual_files:
        print("No music files found in manual folder.")
        return
    
    print(f"Found {len(manual_files)} files to organize")
    
    organized_count = 0
    for file_path in manual_files:
        artist, album, track, genre, year = temp_organizer.parse_metadata(file_path)
        
        if artist != "Manual Sort Needed":
            target_dir = collection_path / artist / album
            target_dir.mkdir(parents=True, exist_ok=True)
            
            target_file = target_dir / file_path.name
            counter = 1
            while target_file.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                target_file = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            
            try:
                shutil.move(str(file_path), str(target_file))
                print(f"Moved: {artist}/{album}/{file_path.name}")
                organized_count += 1
            except Exception as e:
                print(f"Error moving {file_path.name}: {e}")
        else:
            print(f"Still needs manual sorting: {file_path.name}")
    
    print(f"\nOrganized {organized_count} files from manual folder")

def main():
    parser = argparse.ArgumentParser(description='Advanced Music Organizer')
    parser.add_argument('music_path', help='Path to music folder')
    parser.add_argument('--config', help='Configuration file path')
    parser.add_argument('--interactive', action='store_true', help='Enable interactive mode')
    parser.add_argument('--manual-only', help='Organize only manual sort folder into existing collection')
    
    args = parser.parse_args()
    
    if args.manual_only:
        collection_path = args.music_path
        organize_manual_folder(args.manual_only, collection_path)
        return
    
    organizer = AdvancedMusicOrganizer(args.music_path, args.config)
    
    if args.interactive:
        organizer.config.set('GENERAL', 'interactive_mode', 'true')
    
    organizer.organize()

if __name__ == "__main__":
    # For direct execution without command line args
    music_path = r"C:\Users\andys\Documents\Claude\Music"
    organizer = AdvancedMusicOrganizer(music_path)
    organizer.organize()
