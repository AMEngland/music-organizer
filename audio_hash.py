#!/usr/bin/env python3
"""Format-aware hashing of the audio payload of a media file.

Whole-file hashing is useless for duplicate detection in a real music library:
two copies of the same recording routinely differ by a single ID3 byte, a
different embedded cover, or a different amount of tag padding.  This module
locates the *audio* byte range of a file and hashes only that, so tag-only
differences no longer defeat deduplication.

Supported precisely: MP3 / AAC-ADTS (ID3v2 header, ID3v1 / APEv2 / Lyrics3
trailers), FLAC (metadata block chain), MP4 / M4A (``mdat`` atom).
Everything else falls back to a whole-file hash, which is still correct --
just less tolerant of tag differences.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, NamedTuple, Optional, Tuple

__all__ = ["AudioHash", "audio_payload_range", "hash_audio_payload"]

_CHUNK = 1 << 20  # 1 MiB

# Containers whose audio payload lives inside an ``mdat`` atom.
_MP4_EXTENSIONS = {".m4a", ".m4b", ".mp4", ".m4p", ".m4v"}


class AudioHash(NamedTuple):
    """Result of hashing a file."""

    digest: str
    #: ``"audio"`` when tag regions were excluded, ``"file"`` for the fallback.
    scope: str
    #: Number of bytes actually hashed.
    length: int


def _id3v2_length(handle: BinaryIO) -> int:
    """Return the byte length of a leading ID3v2 tag, or 0 if there is none."""
    handle.seek(0)
    header = handle.read(10)
    if len(header) < 10 or header[:3] != b"ID3":
        return 0

    flags = header[5]
    size = 0
    for byte in header[6:10]:
        # ID3v2 sizes are "syncsafe": 7 significant bits per byte.
        size = (size << 7) | (byte & 0x7F)

    total = 10 + size
    if flags & 0x10:  # ID3v2.4 footer
        total += 10
    return total


def _strip_trailers(handle: BinaryIO, end: int) -> int:
    """Peel ID3v1 / APEv2 / Lyrics3 trailers off the end of the file."""
    while end > 0:
        if end >= 128:
            handle.seek(end - 128)
            if handle.read(3) == b"TAG":
                end -= 128
                continue

        if end >= 32:
            handle.seek(end - 32)
            footer = handle.read(32)
            if footer[:8] == b"APETAGEX":
                total = int.from_bytes(footer[12:16], "little")
                flags = int.from_bytes(footer[20:24], "little")
                if flags & 0x80000000:  # tag also carries a 32-byte header
                    total += 32
                if 0 < total <= end:
                    end -= total
                    continue

        if end >= 15:
            handle.seek(end - 9)
            if handle.read(9) == b"LYRICS200":
                handle.seek(end - 15)
                digits = handle.read(6)
                if digits.isdigit():
                    total = int(digits) + 15
                    if 0 < total <= end:
                        end -= total
                        continue

        break
    return end


def _flac_audio_start(handle: BinaryIO, offset: int) -> Optional[int]:
    """Return the offset of the first FLAC audio frame, or None."""
    handle.seek(offset)
    if handle.read(4) != b"fLaC":
        return None

    position = offset + 4
    while True:
        handle.seek(position)
        header = handle.read(4)
        if len(header) < 4:
            return None
        is_last = header[0] & 0x80
        position += 4 + int.from_bytes(header[1:4], "big")
        if is_last:
            return position


def _mp4_mdat_range(handle: BinaryIO, size: int) -> Optional[Tuple[int, int]]:
    """Return the byte range of the ``mdat`` atom payload, or None."""
    position = 0
    while position + 8 <= size:
        handle.seek(position)
        header = handle.read(8)
        if len(header) < 8:
            return None

        atom_size = int.from_bytes(header[0:4], "big")
        atom_type = header[4:8]
        header_length = 8

        if atom_size == 1:  # 64-bit extended size follows the type
            extended = handle.read(8)
            if len(extended) < 8:
                return None
            atom_size = int.from_bytes(extended, "big")
            header_length = 16
        elif atom_size == 0:  # atom runs to end of file
            atom_size = size - position

        if atom_size < header_length:
            return None

        if atom_type == b"mdat":
            return position + header_length, min(position + atom_size, size)

        position += atom_size

    return None


def audio_payload_range(path: Path) -> Tuple[int, int, str]:
    """Return ``(start, end, scope)`` for the audio payload of *path*.

    ``scope`` is ``"audio"`` when metadata was successfully excluded and
    ``"file"`` when the caller is getting the whole file as a fallback.
    """
    size = path.stat().st_size
    if size == 0:
        return 0, 0, "file"

    extension = path.suffix.lower()
    start, end = 0, size
    scope = "file"

    try:
        with open(path, "rb") as handle:
            start = _id3v2_length(handle)
            end = _strip_trailers(handle, size)
            if start or end != size:
                scope = "audio"

            if extension == ".flac":
                flac_start = _flac_audio_start(handle, start)
                if flac_start is not None:
                    start, scope = flac_start, "audio"
            elif extension in _MP4_EXTENSIONS:
                mdat = _mp4_mdat_range(handle, size)
                if mdat is not None:
                    start, end = mdat
                    scope = "audio"
    except OSError:
        return 0, size, "file"

    if not 0 <= start < end <= size:
        return 0, size, "file"
    return start, end, scope


def hash_audio_payload(path: Path) -> Optional[AudioHash]:
    """Hash the audio payload of *path*. Returns None if the file is unreadable."""
    try:
        start, end, scope = audio_payload_range(path)
        digest = hashlib.md5()
        remaining = end - start

        with open(path, "rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)

        hashed = (end - start) - max(remaining, 0)
        if hashed <= 0:
            return None
        return AudioHash(digest.hexdigest(), scope, hashed)
    except OSError:
        return None
