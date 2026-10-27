"""Track-record strip: never raises, honest empty state, escapes hostile text,
no percentage for buckets under 5 calls, Telegram line wording."""
import re
import unittest

import _stubs  # noqa: F401
import track_page as T

HOSTILE = "</script><img src=x onerror=alert(1)>"


def rich():
    return {"n_graded": 40, "n_open": 3, "enough_data": True, "hit_rate": 0.6, "hit_rate_baseline": 0.5,
            "calibrated": True, "as_of": "2026-10-27",
            "by_confidence": {"high": {"n": 15, "hits": 12, "rate": 0.8}, "medium": {"n": 15, "hits": 8, "rate": 0.533},
                              "low": {"n": 10, "hits": 4, "rate": 0.4}},
            "by_topic": {"oil": {"n": 12, "hits": 9, "rate": 0.75}, "rare": {"n": 3, "hits": 3, "rate": 1.0}},
            "recent_30d": {"n": 20, "hit_rate": 0.65}, "prev_30d": {"n": 20, "hit_rate": 0.55}}


def text_of(html_):
    return re.sub(r"<[^>]+>", " ", html_)


class EmptyAndThin(unittest.TestCase):
    def test_garbage_inputs_never_raise(self):
        for sc in (None, {}, [], "x", 5, {"n_graded": "a", "hit_rate": "b", "by_confidence": 3, "by_topic": [1]},
                   {"n_graded": float("nan"), "hit_rate": float("inf"), "enough_data": True},
                   {"n_graded": -4, "enough_data": True, "hit_rate": 2.5, "by_topic": {"a": None, "b": {"n": "z"}}},
                   {"n_graded": True, "hit_rate": True}):
            with self.subTest(sc=repr(sc)[:40]):
                h = T.track_record_html(sc, None, None)
                self.assertTrue(h.strip())
                self.assertIsInstance(T.telegram_line(sc), str)
        T.track_record_html(rich(), 5, 7)

    def test_empty_state_is_honest(self):
        h = T.track_record_html({})
        txt = text_of(h)
        self.assertRegex(txt, r"Not enough (graded|checked) calls yet")
        self.assertNotRegex(txt, r"\d+%")
        self.assertNotIn("baseline", txt.lower())

    def test_thin_scorecard_hides_percentages(self):
        sc = {"n_graded": 4, "enough_data": True, "hit_rate": 1.0, "hit_rate_baseline": 1.0}
        h = T.track_record_html(sc)
        self.assertNotRegex(text_of(h), r"\d+%")
        self.assertIn("4 of 20", h)

    def test_enough_data_flag_without_rate_is_not_trusted(self):
        sc = {"n_graded": 30, "enough_data": True, "hit_rate": None}
        self.assertIn("Not enough", T.track_record_html(sc))
        self.assertIn("building", T.telegram_line(sc))

    def test_empty_changelog_message(self):
        self.assertIn("Nothing yet", T.track_record_html({}, "", ""))


class Rich(unittest.TestCase):
    def test_rich_shows_numbers_and_sections(self):
        h = T.track_record_html(rich(), "- 2026-10-27 added oil playbook\n", "- Oil calls hit 75% (n=12)")
        txt = text_of(h)
        self.assertIn("60% right across 40 graded calls", txt)
        self.assertIn("momentum baseline 50%", txt)
        self.assertIn("Does high confidence beat low?", txt)
        self.assertIn("40 points more often", txt)
        self.assertIn("High-confidence calls beat low-confidence calls by at least 5 points", txt)
        self.assertIn("added oil playbook", txt)
        self.assertIn("Oil calls hit 75%", txt)
        self.assertIn("3 more calls still waiting", txt)

    def test_bucket_under_five_has_no_percentage(self):
        h = T.track_record_html(rich())
        # the 'rare' topic (n=3, rate 100%) must not be shown at all
        self.assertNotIn("rare", h)
        sc = rich()
        sc["by_confidence"]["low"] = {"n": 4, "hits": 4, "rate": 1.0}
        h = T.track_record_html(sc)
        self.assertIn("n too small", h)
        self.assertIn("Too early to say", h)
        low_row = re.search(r'<div class="tr-row"><span class="tr-lab">Low.*?</div>', h, re.S).group(0)
        self.assertNotRegex(low_row, r"\d+%")
        self.assertIn("(4)", low_row)

    def test_n_price_graded_is_the_n_shown_and_labelled_checked(self):
        sc = rich()
        sc["n_price_graded"] = 30
        txt = text_of(T.track_record_html(sc))
        self.assertIn("60% right across 30 checked calls", txt)
        self.assertEqual(T.telegram_line(sc).split(" (")[0], "Track record: 60% of 30 checked calls right")
        sc["n_price_graded"] = 3     # too few price-graded calls behind the rate: no percentage
        self.assertNotRegex(text_of(T.track_record_html(sc)), r"60%")
        self.assertIn("building", T.telegram_line(sc))

    def test_calibration_note_text(self):
        sc = rich()
        sc["calibrated"] = False
        self.assertIn("Not yet", text_of(T.track_record_html(sc)))

    def test_BUG_trend_line_reads_the_keys_the_ledger_writes(self):
        # learning_ledger.scorecard() writes recent_30d / prev_30d as {"n", "hit_rate"};
        # track_page._bucket only reads "rate"/"hits", so the "Last 30 days ... up/down" line never appears.
        txt = text_of(T.track_record_html(rich()))
        self.assertIn("Last 30 days 65%", txt)
        self.assertIn("up 10 points", txt)

    def test_exactly_five_is_shown(self):
        sc = rich()
        sc["by_topic"] = {"five": {"n": 5, "hits": 4, "rate": 0.8}}
        self.assertIn("80%", text_of(T.track_record_html(sc)))

    def test_topic_overflow_is_summarised(self):
        sc = rich()
        sc["by_topic"] = {f"t{i}": {"n": 6, "hits": 3, "rate": 0.5} for i in range(11)}
        self.assertIn("+3 more topics", T.track_record_html(sc))

    def test_no_script_or_event_handlers_in_output(self):
        h = T.track_record_html(rich(), "- x", "- y")
        self.assertNotIn("<script", h.lower())
        self.assertNotRegex(h, r"\son\w+=")


class Hostile(unittest.TestCase):
    def test_hostile_strings_escaped_everywhere(self):
        sc = rich()
        sc["as_of"] = HOSTILE
        sc["by_topic"] = {HOSTILE: {"n": 9, "hits": 5, "rate": 0.55}}
        log = f"- {HOSTILE}\n# {HOSTILE}\n- <b>bold</b> & more"
        h = T.track_record_html(sc, log, HOSTILE + "\n- " + HOSTILE)
        self.assertNotIn("</script>", h)
        self.assertNotIn("<img", h)
        self.assertNotIn("<b>bold</b>", h)
        self.assertIn("&lt;/script&gt;", h)
        self.assertIn("&amp; more", h)

    def test_changelog_headings_dropped_and_lines_capped(self):
        long_ = "- " + "word " * 200
        h = T.track_record_html(rich(), "# Heading line\n" + long_ + "\n" + "\n".join(f"- e{i}" for i in range(20)))
        self.assertNotIn("Heading line", h)
        self.assertLessEqual(h.count("<li>"), T.CHANGELOG_LINES)
        self.assertIn("…", h)

    def test_lessons_capped(self):
        h = T.track_record_html(rich(), "", "z" * 20000)
        self.assertLess(h.count("z"), T.LESSONS_CAP + 10)

    def test_wrong_types_for_text_args(self):
        self.assertTrue(T.track_record_html(rich(), {"a": 1}, ["x"]).strip())


class TelegramLine(unittest.TestCase):
    def test_building_wording(self):
        self.assertEqual(T.telegram_line({}), "Track record: building (0 of 20 graded calls so far)")
        self.assertEqual(T.telegram_line({"n_graded": 7}), "Track record: building (7 of 20 graded calls so far)")

    def test_rich_wording(self):
        self.assertEqual(T.telegram_line(rich()),
                         "Track record: 60% of 40 graded calls right (momentum baseline 50%); high-confidence calls 80%")

    def test_small_high_bucket_omitted(self):
        sc = rich()
        sc["by_confidence"]["high"] = {"n": 3, "hits": 3, "rate": 1.0}
        self.assertNotIn("high-confidence", T.telegram_line(sc))

    def test_no_baseline_omitted(self):
        sc = rich()
        sc["hit_rate_baseline"] = None
        self.assertNotIn("baseline", T.telegram_line(sc))

    def test_plain_text_no_html(self):
        sc = rich()
        self.assertNotIn("<", T.telegram_line(sc))

    def test_css_is_a_string(self):
        self.assertIn(".track", T.CSS)
        self.assertNotIn("<script", T.CSS.lower())


if __name__ == "__main__":
    unittest.main()
