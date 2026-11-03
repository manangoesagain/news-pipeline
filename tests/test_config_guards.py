"""Guards for three latent failures: a domain string that is not in DOMAIN_ORDER, a settings key
without bounds, and a ledger key long enough for the redactor to eat.

None of these can happen in the code as it stands. Each becomes reachable with a one-line edit,
and each one fails somewhere far from the edit that caused it, so they are pinned here.
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime

import _stubs  # noqa: F401
import digest
import learning_io
import learning_memory

NOW = datetime(2026, 10, 22, 18, 0)


def story(i, domain, score=1.0):
    return {"title": f"Headline {i}", "link": f"https://example.com/{i}", "source": "Src",
            "sources": ["Src"], "domain": domain, "region": "US", "published": NOW,
            "score": score, "why": "", "summary": ""}


class DomainConfigStaysInSync(unittest.TestCase):
    """Every domain string the pipeline can produce must be a DOMAIN_ORDER key."""

    def test_feed_and_keyword_domains_are_all_known(self):
        order = set(digest.DOMAIN_ORDER)
        for name, rows in (("RSS_SOURCES", digest.RSS_SOURCES),
                           ("GOOGLE_NEWS_QUERIES", digest.GOOGLE_NEWS_QUERIES),
                           ("GNEWS_CALLS", digest.GNEWS_CALLS),
                           ("NEWSDATA_CALLS", digest.NEWSDATA_CALLS)):
            for row in rows:
                self.assertIn(row[0], order, f"{name}: domain hint {row[0]!r} is not in DOMAIN_ORDER")
        self.assertEqual(set(digest.DOMAIN_KEYWORDS), order, "DOMAIN_KEYWORDS and DOMAIN_ORDER differ")

    def test_classify_domain_only_returns_known_domains(self):
        order = set(digest.DOMAIN_ORDER)
        for hint in sorted({r[0] for r in digest.RSS_SOURCES}):
            got = digest.classify_domain("A headline with no keywords at all", "", hint)
            self.assertIn(got, order)


class SelectDigestSurvivesAnUnknownDomain(unittest.TestCase):
    """select_digest is called from main() with no handler around it, so it must not raise."""

    def test_unknown_domain_is_skipped_not_raised(self):
        known = digest.DOMAIN_ORDER[0]
        stories = [story(1, known, score=5.0), story(2, "Sports & Lifestyle", score=9.0)]
        out = digest.select_digest(stories, [])
        self.assertEqual(sorted(out), sorted(digest.DOMAIN_ORDER))
        self.assertEqual([s["link"] for s in out[known]], ["https://example.com/1"])
        self.assertNotIn("Sports & Lifestyle", out)

    def test_known_domains_are_unaffected(self):
        known = digest.DOMAIN_ORDER[1]
        stories = [story(i, known, score=float(i)) for i in range(digest.MAX_PER_DOMAIN + 3)]
        out = digest.select_digest(stories, [])
        self.assertEqual(len(out[known]), digest.MAX_PER_DOMAIN)


class SettingsKeysHaveBounds(unittest.TestCase):
    """settings_get() iterates DEFAULT_SETTINGS and indexes SETTING_BOUNDS unguarded. A key in one
    and not the other raises KeyError inside broad handlers, which silently disables the snapshot,
    auto-revert and weekly-tuner path with a single log line."""

    def test_defaults_and_bounds_hold_the_same_keys(self):
        self.assertEqual(set(learning_memory.DEFAULT_SETTINGS), set(learning_memory.SETTING_BOUNDS))

    def test_every_default_sits_inside_its_own_bounds(self):
        for key, default in learning_memory.DEFAULT_SETTINGS.items():
            lo, hi = learning_memory.SETTING_BOUNDS[key]
            self.assertLessEqual(lo, default, f"{key}: default {default} is below its lower bound {lo}")
            self.assertLessEqual(default, hi, f"{key}: default {default} is above its upper bound {hi}")

    def test_settings_get_does_not_raise(self):
        learning_memory.settings_get()


class LedgerKeysSurviveTheRedactor(unittest.TestCase):
    """learning_io redacts dict KEYS as well as values, and _HEX eats any run of 32+ hex characters.
    A ledger keyed by a full md5/sha hex digest would be written as "[token removed]", collapsing
    every row into one bucket, and the write would still succeed. Keep ledger ids short."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jev_ledger_keys_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = learning_io.LEARN_DIR
        learning_io.LEARN_DIR = os.path.join(self.tmp, "_data", "_learning")
        os.makedirs(learning_io.LEARN_DIR, exist_ok=True)
        self.addCleanup(setattr, learning_io, "LEARN_DIR", self._saved)

    def _round_trip(self, key):
        path = os.path.join(learning_io.LEARN_DIR, "probe.json")
        learning_io.safe_write_json(path, {key: 1})
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_a_full_hex_digest_key_is_destroyed(self):
        full = hashlib.md5(b"a headline").hexdigest()
        self.assertEqual(len(full), 32)
        self.assertNotIn(full, self._round_trip(full),
                         "a 32-char hex key survived; the redactor changed and this guard is stale")

    def test_a_short_prefixed_key_survives(self):
        key = "s-" + hashlib.sha256(b"a headline").hexdigest()[:16]
        self.assertIn(key, self._round_trip(key))

    def test_existing_learning_ids_are_short_enough(self):
        self.assertEqual(len(learning_memory._hash("some lesson text")), 16)
        self.assertIn(learning_memory._hash("some lesson text"),
                      self._round_trip(learning_memory._hash("some lesson text")))


if __name__ == "__main__":
    unittest.main()
