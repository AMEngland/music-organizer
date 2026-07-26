#!/usr/bin/env python3
"""Tests for Chromaprint/AcoustID fingerprinting.

The two external steps -- running ``fpcalc`` and calling the web service -- are
stubbed, so the suite needs neither the binary nor a network.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fingerprint  # noqa: E402
import state  # noqa: E402


def response(score=0.95, recording_id="rec-1", artist="Boards of Canada",
             title="Roygbiv", album="Music Has the Right to Children",
             group_type="Album"):
    return {
        "status": "ok",
        "results": [{
            "id": "result-1",
            "score": score,
            "recordings": [{
                "id": recording_id,
                "title": title,
                "artists": [{"name": artist}],
                "releasegroups": [{"title": album, "type": group_type}],
            }],
        }],
    }


class StubService(fingerprint.FingerprintService):
    """FingerprintService with both external steps replaced."""

    def __init__(self, payload=None, fingerprint_data=None, **kwargs):
        kwargs.setdefault("api_key", "test-key")
        super().__init__(**kwargs)
        self.fpcalc_path = kwargs.get("fpcalc_path") or "stub-fpcalc"
        self.payload = payload
        self.fingerprint_data = fingerprint_data or {"duration": 210,
                                                     "fingerprint": "AQAB..."}
        self.fpcalc_calls = 0
        self.api_calls = 0

    def _run_fpcalc(self, path):
        self.fpcalc_calls += 1
        return self.fingerprint_data

    def _call_api(self, duration, fingerprint_text):
        self.api_calls += 1
        return self.payload


class TestAvailability(unittest.TestCase):
    def test_missing_fpcalc_is_unavailable(self):
        service = fingerprint.FingerprintService(api_key="k", fpcalc_path=None)
        service.fpcalc_path = None
        self.assertFalse(service.available)
        self.assertIn("fpcalc", service.unavailable_reason())

    def test_missing_api_key_is_unavailable(self):
        service = fingerprint.FingerprintService(api_key="", fpcalc_path="fpcalc")
        self.assertFalse(service.available)
        self.assertIn("API key", service.unavailable_reason())

    def test_both_present_is_available(self):
        service = fingerprint.FingerprintService(api_key="k", fpcalc_path="fpcalc")
        self.assertTrue(service.available)
        self.assertEqual(service.unavailable_reason(), "")


class TestParseResponse(unittest.TestCase):
    def test_full_match(self):
        match = fingerprint.FingerprintService.parse_response(response())
        self.assertEqual(match.recording_id, "rec-1")
        self.assertEqual(match.artist, "Boards of Canada")
        self.assertEqual(match.title, "Roygbiv")
        self.assertEqual(match.album, "Music Has the Right to Children")
        self.assertAlmostEqual(match.score, 0.95)

    def test_highest_score_wins(self):
        payload = response(score=0.4)
        payload["results"].append(response(score=0.9, recording_id="rec-2",
                                           title="Better")["results"][0])
        match = fingerprint.FingerprintService.parse_response(payload)
        self.assertEqual(match.recording_id, "rec-2")

    def test_album_release_group_preferred_over_single(self):
        payload = response()
        payload["results"][0]["recordings"][0]["releasegroups"] = [
            {"title": "Some Single", "type": "Single"},
            {"title": "The Album", "type": "Album"},
        ]
        match = fingerprint.FingerprintService.parse_response(payload)
        self.assertEqual(match.album, "The Album")

    def test_result_without_recordings_still_yields_an_id(self):
        payload = {"status": "ok", "results": [{"id": "result-9", "score": 0.8}]}
        match = fingerprint.FingerprintService.parse_response(payload)
        self.assertEqual(match.recording_id, "result-9")
        self.assertIsNone(match.artist)

    def test_error_status_returns_none(self):
        self.assertIsNone(
            fingerprint.FingerprintService.parse_response({"status": "error"})
        )

    def test_empty_results_return_none(self):
        self.assertIsNone(
            fingerprint.FingerprintService.parse_response({"status": "ok", "results": []})
        )

    def test_none_payload_returns_none(self):
        self.assertIsNone(fingerprint.FingerprintService.parse_response(None))


class TestIdentify(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.audio = self.root / "track.mp3"
        self.audio.write_bytes(b"audio")
        self.database = state.LibraryDatabase(self.root / state.DB_FILENAME)

    def tearDown(self):
        self.database.close()
        self._tmp.cleanup()

    def test_identifies_a_track(self):
        service = StubService(payload=response(), database=self.database)
        match = service.identify(self.audio, "hash-1")

        self.assertIsNotNone(match)
        self.assertEqual(match.title, "Roygbiv")
        self.assertEqual(service.stats["identified"], 1)

    def test_low_score_is_rejected(self):
        service = StubService(payload=response(score=0.2), database=self.database,
                              min_score=0.7)
        self.assertIsNone(service.identify(self.audio, "hash-1"))

    def test_result_is_cached_and_not_queried_twice(self):
        service = StubService(payload=response(), database=self.database)
        service.identify(self.audio, "hash-1")
        service.identify(self.audio, "hash-1")

        self.assertEqual(service.api_calls, 1)
        self.assertEqual(service.stats["cache_hits"], 1)

    def test_negative_results_are_cached_too(self):
        service = StubService(payload={"status": "ok", "results": []},
                              database=self.database)
        self.assertIsNone(service.identify(self.audio, "hash-1"))
        self.assertIsNone(service.identify(self.audio, "hash-1"))

        self.assertEqual(service.api_calls, 1)
        self.assertEqual(service.stats["cache_hits"], 1)

    def test_cache_survives_a_new_service_instance(self):
        StubService(payload=response(), database=self.database).identify(self.audio, "h")
        fresh = StubService(payload=None, database=self.database)
        match = fresh.identify(self.audio, "h")

        self.assertIsNotNone(match)
        self.assertEqual(fresh.api_calls, 0)

    def test_fpcalc_failure_is_counted_not_raised(self):
        service = StubService(payload=response(), database=self.database,
                              fingerprint_data=None)
        service.fingerprint_data = None
        self.assertIsNone(service.identify(self.audio, "hash-1"))
        self.assertEqual(service.stats["errors"], 1)

    def test_unavailable_service_returns_none(self):
        service = StubService(payload=response(), database=self.database)
        service.api_key = ""
        self.assertIsNone(service.identify(self.audio, "hash-1"))

    def test_works_without_a_database(self):
        service = StubService(payload=response())
        self.assertIsNotNone(service.identify(self.audio))


class TestThrottle(unittest.TestCase):
    def test_requests_are_spaced_out(self):
        service = StubService(payload=response(), rate_limit=50.0)
        started = time.monotonic()
        service._throttle()
        service._throttle()
        elapsed = time.monotonic() - started

        # Two calls at 50/sec must be at least one gap apart.
        self.assertGreaterEqual(elapsed, 1.0 / 50.0)

    def test_invalid_rate_limit_falls_back_to_the_default(self):
        service = StubService(payload=response(), rate_limit=0)
        self.assertEqual(service.rate_limit, fingerprint.DEFAULT_RATE_LIMIT)


class TestRecordingGroups(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.database = state.LibraryDatabase(Path(self._tmp.name) / state.DB_FILENAME)

    def tearDown(self):
        self.database.close()
        self._tmp.cleanup()

    def test_hashes_group_by_recording(self):
        self.database.store_fingerprint("h1", 200, "fp1", {"recording_id": "rec-a"})
        self.database.store_fingerprint("h2", 200, "fp2", {"recording_id": "rec-a"})
        self.database.store_fingerprint("h3", 200, "fp3", {"recording_id": "rec-b"})
        self.database.store_fingerprint("h4", 200, "fp4", None)

        groups = self.database.recording_groups()
        self.assertEqual(sorted(groups["rec-a"]), ["h1", "h2"])
        self.assertEqual(groups["rec-b"], ["h3"])
        self.assertEqual(len(groups), 2)


if __name__ == "__main__":
    unittest.main()
