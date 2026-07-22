"""Tests for the ops layer: cache, budget, retry. All offline, no key."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from askrepo.ops import (  # noqa: E402
    Budget, BudgetExceeded, ResponseCache, TransientError,
    cache_key, is_transient, with_retry,
)


class TestCache(unittest.TestCase):
    def _cache(self, **kw):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)  # start empty; ResponseCache creates on set
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return ResponseCache(path=path, **kw), path

    def test_miss_then_hit(self):
        cache, _ = self._cache()
        self.assertIsNone(cache.get("k"))
        cache.set("k", "answer")
        self.assertEqual(cache.get("k"), "answer")
        self.assertEqual(cache.hits, 1)
        self.assertEqual(cache.misses, 1)

    def test_persists_across_instances(self):
        # the CLI-specific reason the cache is disk-backed: one process per ask
        cache, path = self._cache()
        cache.set("k", "answer")
        reopened = ResponseCache(path=path)
        self.assertEqual(reopened.get("k"), "answer")

    def test_ttl_expiry(self):
        cache, _ = self._cache(ttl_s=-1)  # everything is already expired
        cache.set("k", "answer")
        self.assertIsNone(cache.get("k"))

    def test_corrupt_file_is_a_miss_not_a_crash(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b"{not valid json")
        os.close(fd)
        self.addCleanup(lambda: os.unlink(path))
        cache = ResponseCache(path=path)
        self.assertIsNone(cache.get("k"))

    def test_key_is_sensitive_to_every_part(self):
        base = cache_key("openai", "gpt-4o-mini", "2", "rag", 5, "0.7", False, (), "q")
        changed = cache_key("openai", "gpt-4o-mini", "2", "rag", 5, "0.7", False, (), "q2")
        model = cache_key("openai", "gpt-4o", "2", "rag", 5, "0.7", False, (), "q")
        contract = cache_key("openai", "gpt-4o-mini", "3", "rag", 5, "0.7", False, (), "q")
        self.assertEqual(len({base, changed, model, contract}), 4)


class TestBudget(unittest.TestCase):
    def test_unlimited_never_refuses(self):
        b = Budget(0.0)
        b.record(1000.0)
        b.check(1000.0)  # no raise
        self.assertEqual(b.remaining_usd, float("inf"))

    def test_refuses_over_ceiling(self):
        b = Budget(0.001)
        b.check(0.0005)  # under: fine
        b.record(0.0008)
        with self.assertRaises(BudgetExceeded):
            b.check(0.0005)  # 0.0008 + 0.0005 > 0.001

    def test_tracks_spend_and_calls(self):
        b = Budget(1.0)
        b.record(0.1)
        b.record(0.2)
        self.assertAlmostEqual(b.spent_usd, 0.3)
        self.assertEqual(b.calls, 2)
        self.assertAlmostEqual(b.remaining_usd, 0.7)


class TestRetry(unittest.TestCase):
    def test_succeeds_after_transient_flaps(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientError("429")
            return "ok"

        result = with_retry(flaky, sleep=lambda d: None)
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)

    def test_gives_up_after_max_attempts(self):
        def always_fails():
            raise TransientError("still down")

        with self.assertRaises(TransientError):
            with_retry(always_fails, max_attempts=3, sleep=lambda d: None)

    def test_non_transient_raises_immediately(self):
        calls = {"n": 0}

        def bad_request():
            calls["n"] += 1
            raise ValueError("your bug; don't retry")

        with self.assertRaises(ValueError):
            with_retry(bad_request, sleep=lambda d: None)
        self.assertEqual(calls["n"], 1)  # exactly one attempt

    def test_is_transient_classification(self):
        self.assertTrue(is_transient(TransientError("x")))
        self.assertTrue(is_transient(TimeoutError()))
        self.assertTrue(is_transient(ConnectionError()))
        self.assertFalse(is_transient(ValueError("bad request")))

        class RateLimitError(Exception):  # mimic an SDK exception by name
            status_code = 429

        class BadRequestError(Exception):
            status_code = 400

        self.assertTrue(is_transient(RateLimitError()))
        self.assertFalse(is_transient(BadRequestError()))


if __name__ == "__main__":
    unittest.main()
