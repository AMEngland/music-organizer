#!/usr/bin/env python3
"""Acoustic fingerprinting via Chromaprint and the AcoustID web service.

This is the answer to the two problems that tags, paths and filenames cannot
solve: identifying a file that carries no usable metadata at all, and
recognizing that a 128kbps rip and a 320kbps rip are the same recording.

Requirements are deliberately light: the ``fpcalc`` binary from Chromaprint
must be on PATH (or configured), and an AcoustID API key is needed for lookups.
No third-party Python packages -- the web call is plain ``urllib``. If either
piece is missing the service reports itself unavailable and the organizer
carries on without it.

Every lookup is cached in the organizer's database, negative results included,
so a second run costs nothing.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

__all__ = ["FingerprintService", "RecordingMatch", "ACOUSTID_ENDPOINT"]

LOG = logging.getLogger("music_organizer")

ACOUSTID_ENDPOINT = "https://api.acoustid.org/v2/lookup"

# AcoustID asks clients to stay at or below 3 requests per second.
DEFAULT_RATE_LIMIT = 3.0
FPCALC_TIMEOUT = 60


@dataclass
class RecordingMatch:
    """One identified recording."""

    recording_id: str
    score: float
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[str] = None

    def as_dict(self) -> Dict:
        return {
            "recording_id": self.recording_id,
            "score": self.score,
            "artist": self.artist,
            "title": self.title,
            "album": self.album,
            "year": self.year,
        }

    @classmethod
    def from_row(cls, row: Dict) -> Optional["RecordingMatch"]:
        if not row.get("recording_id"):
            return None
        return cls(
            recording_id=row["recording_id"],
            score=row.get("score") or 0.0,
            artist=row.get("artist"),
            title=row.get("title"),
            album=row.get("album"),
            year=row.get("year"),
        )


class FingerprintService:
    """Fingerprints files and resolves them to recordings via AcoustID.

    The two external steps -- running ``fpcalc`` and calling the web service --
    are isolated in :meth:`_run_fpcalc` and :meth:`_call_api` so they can be
    replaced in tests without a binary or a network.
    """

    def __init__(self, api_key: Optional[str] = None, database=None,
                 min_score: float = 0.7, rate_limit: float = DEFAULT_RATE_LIMIT,
                 fpcalc_path: Optional[str] = None, timeout: int = 15) -> None:
        self.api_key = (api_key or "").strip()
        self.database = database
        self.min_score = min_score
        self.rate_limit = rate_limit if rate_limit > 0 else DEFAULT_RATE_LIMIT
        self.timeout = timeout
        self.fpcalc_path = fpcalc_path or shutil.which("fpcalc")

        self._last_call = 0.0
        self.stats = {"fingerprinted": 0, "cache_hits": 0, "identified": 0,
                      "no_match": 0, "errors": 0}

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @property
    def has_fpcalc(self) -> bool:
        return bool(self.fpcalc_path)

    @property
    def available(self) -> bool:
        """True when fingerprinting *and* lookups can both be performed."""
        return self.has_fpcalc and bool(self.api_key)

    def unavailable_reason(self) -> str:
        if not self.has_fpcalc:
            return ("fpcalc not found on PATH - install Chromaprint "
                    "(https://acoustid.org/chromaprint) or set FINGERPRINT.fpcalc_path")
        if not self.api_key:
            return ("no AcoustID API key - set FINGERPRINT.api_key or the "
                    "ACOUSTID_API_KEY environment variable "
                    "(free key: https://acoustid.org/new-application)")
        return ""

    # ------------------------------------------------------------------
    # External steps (stubbed in tests)
    # ------------------------------------------------------------------

    def _run_fpcalc(self, path: Path) -> Optional[Dict]:
        """Run Chromaprint. Returns ``{"duration": int, "fingerprint": str}``."""
        if not self.fpcalc_path:
            return None
        try:
            completed = subprocess.run(
                [self.fpcalc_path, "-json", str(path)],
                capture_output=True, timeout=FPCALC_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOG.debug("fpcalc failed for %s: %s", path, exc)
            return None

        if completed.returncode != 0:
            LOG.debug("fpcalc returned %d for %s", completed.returncode, path)
            return None

        try:
            payload = json.loads(completed.stdout.decode("utf-8", "replace"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            LOG.debug("Could not parse fpcalc output for %s: %s", path, exc)
            return None

        duration = payload.get("duration")
        fingerprint = payload.get("fingerprint")
        if not fingerprint:
            return None
        return {"duration": int(duration or 0), "fingerprint": fingerprint}

    def _call_api(self, duration: int, fingerprint: str) -> Optional[Dict]:
        """Query AcoustID. Returns the decoded JSON payload."""
        parameters = urllib.parse.urlencode({
            "client": self.api_key,
            "meta": "recordings+releasegroups+compress",
            "duration": str(duration),
            "fingerprint": fingerprint,
        }).encode("utf-8")

        request = urllib.request.Request(
            ACOUSTID_ENDPOINT, data=parameters,
            headers={"User-Agent": "music-organizer/2.1"},
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            LOG.debug("AcoustID lookup failed: %s", exc)
            return None

    def _throttle(self) -> None:
        """Hold the request rate at or below the configured limit."""
        minimum_gap = 1.0 / self.rate_limit
        elapsed = time.monotonic() - self._last_call
        if 0 <= elapsed < minimum_gap:
            time.sleep(minimum_gap - elapsed)
        self._last_call = time.monotonic()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_response(payload: Optional[Dict]) -> Optional[RecordingMatch]:
        """Pick the best recording out of an AcoustID response."""
        if not payload or payload.get("status") != "ok":
            return None

        best: Optional[RecordingMatch] = None

        for result in payload.get("results") or []:
            score = float(result.get("score") or 0.0)
            for recording in result.get("recordings") or []:
                recording_id = recording.get("id")
                if not recording_id:
                    continue

                artists = recording.get("artists") or []
                artist = artists[0].get("name") if artists else None

                album = year = None
                groups = recording.get("releasegroups") or []
                if groups:
                    album = groups[0].get("title")
                    # Prefer an album release group over a single/compilation.
                    for group in groups:
                        if (group.get("type") or "").lower() == "album":
                            album = group.get("title")
                            break

                candidate = RecordingMatch(
                    recording_id=recording_id, score=score, artist=artist,
                    title=recording.get("title"), album=album, year=year,
                )
                if best is None or candidate.score > best.score:
                    best = candidate

            if best is None and result.get("id"):
                # A scored result with no recording metadata attached is still
                # useful for grouping different encodes together.
                candidate = RecordingMatch(recording_id=result["id"], score=score)
                if best is None or candidate.score > best.score:
                    best = candidate

        return best

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def identify(self, path: Path, audio_hash: Optional[str] = None
                 ) -> Optional[RecordingMatch]:
        """Identify one file, using and populating the cache.

        Returns None when the file cannot be fingerprinted, the service knows
        nothing about it, or the best match scores below ``min_score``.
        """
        if audio_hash and self.database is not None:
            cached = self.database.cached_fingerprint(audio_hash)
            if cached is not None:
                self.stats["cache_hits"] += 1
                match = RecordingMatch.from_row(cached)
                return match if match and match.score >= self.min_score else None

        if not self.available:
            return None

        fingerprint_data = self._run_fpcalc(path)
        if not fingerprint_data:
            self.stats["errors"] += 1
            return None
        self.stats["fingerprinted"] += 1

        self._throttle()
        payload = self._call_api(fingerprint_data["duration"],
                                 fingerprint_data["fingerprint"])
        match = self.parse_response(payload)

        if audio_hash and self.database is not None:
            self.database.store_fingerprint(
                audio_hash, fingerprint_data["duration"],
                fingerprint_data["fingerprint"],
                match.as_dict() if match else None,
            )

        if match is None:
            self.stats["no_match"] += 1
            return None

        if match.score < self.min_score:
            LOG.debug("Discarding low-scoring match for %s (%.2f)", path, match.score)
            return None

        self.stats["identified"] += 1
        return match

    def summary(self) -> str:
        return (f"fingerprinted {self.stats['fingerprinted']}, "
                f"identified {self.stats['identified']}, "
                f"no match {self.stats['no_match']}, "
                f"cached {self.stats['cache_hits']}, "
                f"errors {self.stats['errors']}")
