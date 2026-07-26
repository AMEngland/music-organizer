#!/usr/bin/env python3
"""Tests for audio-payload-scoped hashing."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audio_hash  # noqa: E402

AUDIO = b"\xff\xfb\x90\x64" + bytes(range(256)) * 40  # stand-in for MP3 frames


def _syncsafe(value: int) -> bytes:
    return bytes((
        (value >> 21) & 0x7F,
        (value >> 14) & 0x7F,
        (value >> 7) & 0x7F,
        value & 0x7F,
    ))


def id3v2_tag(payload: bytes) -> bytes:
    """Build a minimal ID3v2.3 tag wrapping *payload* as padding."""
    return b"ID3\x03\x00\x00" + _syncsafe(len(payload)) + payload


def id3v1_tag(title: bytes) -> bytes:
    return b"TAG" + title.ljust(125, b"\x00")


def ape_tag() -> bytes:
    """A 32-byte APEv2 footer describing a footer-only tag."""
    return (
        b"APETAGEX"
        + (2000).to_bytes(4, "little")
        + (32).to_bytes(4, "little")   # tag size, footer included
        + (0).to_bytes(4, "little")    # item count
        + (0).to_bytes(4, "little")    # flags: no header
        + bytes(8)
    )


class TestAudioPayloadRange(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_identical_audio_different_id3_tags_hash_the_same(self):
        a = self._write("a.mp3", id3v2_tag(b"ARTIST-ONE" + bytes(500)) + AUDIO)
        b = self._write("b.mp3", id3v2_tag(b"A-COMPLETELY-DIFFERENT-TAG") + AUDIO)

        hash_a = audio_hash.hash_audio_payload(a)
        hash_b = audio_hash.hash_audio_payload(b)

        self.assertEqual(hash_a.digest, hash_b.digest)
        self.assertEqual(hash_a.scope, "audio")
        self.assertEqual(hash_a.length, len(AUDIO))

    def test_trailing_id3v1_is_excluded(self):
        plain = self._write("plain.mp3", AUDIO)
        tagged = self._write("tagged.mp3", AUDIO + id3v1_tag(b"Some Title"))

        self.assertEqual(
            audio_hash.hash_audio_payload(plain).digest,
            audio_hash.hash_audio_payload(tagged).digest,
        )

    def test_trailing_ape_tag_is_excluded(self):
        plain = self._write("plain.mp3", AUDIO)
        tagged = self._write("tagged.mp3", AUDIO + ape_tag())

        self.assertEqual(
            audio_hash.hash_audio_payload(plain).digest,
            audio_hash.hash_audio_payload(tagged).digest,
        )

    def test_stacked_ape_and_id3v1_are_both_excluded(self):
        plain = self._write("plain.mp3", AUDIO)
        tagged = self._write("tagged.mp3", AUDIO + ape_tag() + id3v1_tag(b"Title"))

        self.assertEqual(
            audio_hash.hash_audio_payload(plain).digest,
            audio_hash.hash_audio_payload(tagged).digest,
        )

    def test_different_audio_hashes_differently(self):
        a = self._write("a.mp3", id3v2_tag(bytes(100)) + AUDIO)
        b = self._write("b.mp3", id3v2_tag(bytes(100)) + AUDIO + b"extra frames")

        self.assertNotEqual(
            audio_hash.hash_audio_payload(a).digest,
            audio_hash.hash_audio_payload(b).digest,
        )

    def test_id3v24_footer_is_accounted_for(self):
        payload = bytes(64)
        # Header (10 bytes), body, then the 10-byte v2.4 footer.
        tag = (b"ID3\x04\x00\x10" + _syncsafe(len(payload)) + payload
               + b"3DI\x04\x00\x10" + _syncsafe(len(payload)))
        path = self._write("footer.mp3", tag + AUDIO)

        start, end, scope = audio_hash.audio_payload_range(path)
        self.assertEqual(scope, "audio")
        self.assertEqual(end - start, len(AUDIO))

    def test_flac_metadata_blocks_are_excluded(self):
        # STREAMINFO (type 0, not last) then PADDING (type 1, last).
        streaminfo = b"\x00" + (34).to_bytes(3, "big") + bytes(34)
        padding_a = b"\x81" + (16).to_bytes(3, "big") + bytes(16)
        padding_b = b"\x81" + (64).to_bytes(3, "big") + b"\xaa" * 64

        a = self._write("a.flac", b"fLaC" + streaminfo + padding_a + AUDIO)
        b = self._write("b.flac", b"fLaC" + streaminfo + padding_b + AUDIO)

        hash_a = audio_hash.hash_audio_payload(a)
        hash_b = audio_hash.hash_audio_payload(b)
        self.assertEqual(hash_a.digest, hash_b.digest)
        self.assertEqual(hash_a.length, len(AUDIO))

    def test_mp4_mdat_is_isolated(self):
        def atom(kind: bytes, body: bytes) -> bytes:
            return (len(body) + 8).to_bytes(4, "big") + kind + body

        a = self._write("a.m4a", atom(b"ftyp", b"M4A ") + atom(b"moov", b"X" * 20) + atom(b"mdat", AUDIO))
        b = self._write("b.m4a", atom(b"ftyp", b"M4A ") + atom(b"moov", b"Y" * 60) + atom(b"mdat", AUDIO))

        hash_a = audio_hash.hash_audio_payload(a)
        hash_b = audio_hash.hash_audio_payload(b)
        self.assertEqual(hash_a.digest, hash_b.digest)
        self.assertEqual(hash_a.length, len(AUDIO))

    def test_unsupported_format_falls_back_to_whole_file(self):
        path = self._write("a.wma", b"\x30\x26\xb2\x75" + AUDIO)
        result = audio_hash.hash_audio_payload(path)
        self.assertEqual(result.scope, "file")
        self.assertEqual(result.length, path.stat().st_size)

    def test_empty_file_returns_none(self):
        path = self._write("empty.mp3", b"")
        self.assertIsNone(audio_hash.hash_audio_payload(path))

    def test_tag_only_file_falls_back_rather_than_hashing_nothing(self):
        path = self._write("tagonly.mp3", id3v2_tag(bytes(50)))
        result = audio_hash.hash_audio_payload(path)
        self.assertIsNotNone(result)
        self.assertEqual(result.scope, "file")


if __name__ == "__main__":
    unittest.main()
