#!/usr/bin/env python3

import os
import shutil
import hashlib
from pathlib import Path
import re
from collections import defaultdict

def add_new_music(source_folder, organized_folder):
    """Add new music files to existing organized collection"""
    source_path = Path(source_folder)
    organized_path = Path(organized_folder)
    
    if not organized_path.exists():
        print(f"Organized folder doesn't exist: {organized_path}")
        return
    
    # Build hash database of existing files
    print("Scanning existing organized collection...")
    existing_hashes = set()
    
    for file_path in organized_path.rglob('*'):
        if file_path.is_file() and file_path.suffix.lower() in {'.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg'}:
            file_hash = get_file_hash(file_path)
            if file_hash:
                existing_hashes.add(file_hash)
    
    print(f"Found {len(existing_hashes)} existing files")
    
    # Process new files
    stats = {'processed': 0, 'added': 0, 'duplicates': 0}
    
    for file_path in source_path.rglob('*'):
        if file_path.is_file() and file_path.suffix.lower() in {'.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg'}:
            stats['processed'] += 1
            
            # Check if already exists
            file_hash = get_file_hash(file_path)
            if file_hash in existing_hashes:
                stats['duplicates'] += 1
                continue
            
            # Parse metadata and organize
            artist, album = parse_metadata(file_path)
            
            # Create target directory
            target_dir = organized_path / artist / album
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy file
            target_file = target_dir / file_path.name
            counter = 1
            while target_file.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                target_file = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            
            try:
                shutil.copy2(file_path, target_file)
                existing_hashes.add(file_hash)
                stats['added'] += 1
                print(f"Added: {artist}/{album}/{file_path.name}")
            except Exception as e:
                print(f"Error copying {file_path}: {e}")
    
    print(f"\nComplete! Processed {stats['processed']}, added {stats['added']}, skipped {stats['duplicates']} duplicates")

def get_file_hash(file_path):
    """Generate MD5 hash for duplicate detection"""
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except:
        return None

def parse_metadata(file_path):
    """Extract artist and album from path"""
    # Simple parsing - can be enhanced
    path_parts = file_path.parts
    
    # Look for artist folder structure
    if len(path_parts) >= 3:
        artist = path_parts[-3]  # Parent directory
        album = path_parts[-2]   # Immediate parent
    elif len(path_parts) >= 2:
        artist = path_parts[-2]
        album = "Singles"
    else:
        # Parse from filename
        filename = file_path.stem
        if " - " in filename:
            artist = filename.split(" - ")[0]
            album = "Singles"
        else:
            artist = "Various Artists"
            album = "Unsorted"
    
    # Clean names
    artist = re.sub(r'[<>:"/\\|?*]', '', artist).strip()
    album = re.sub(r'[<>:"/\\|?*]', '', album).strip()
    
    return artist, album

if __name__ == "__main__":
    # Usage: add_new_music("path/to/new/music", "path/to/Music_Organized")
    source = input("Enter path to new music folder: ").strip('"')
    organized = r"C:\Users\andys\Documents\Claude\Music_Organized"
    
    add_new_music(source, organized)
