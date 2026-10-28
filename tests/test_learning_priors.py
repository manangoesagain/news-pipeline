"""learning_priors: Wilson maths, walk-forward alignment (no look-ahead), flat band, minimum sample,
prior_prob bounds and shrinkage, prompt text caps, packed priors.json round trip, network fence
(host whitelist, strict parsers, fallback chain through the FETCHERS dict) and the never-raise guarantee.
Everything is offline: requests.get is replaced, FETCHERS is monkeypatched."""
import json
import math
import os
import random
import time
import unittest
from datetime import date, datetime, timedelta, timezone

import _stubs  # noqa: F401
from _learn_support import TmpCwdCase, tree_snapshot
import learning_io
import learning_priors as P

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def iso_days(n, start=date(2024, 1, 1)):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def walk(n, seed, drift=0.0, vol=0.01, start_price=100.0):
    rnd = random.Random(seed)
    p, out = start_price, []
    for _ in range(n):
        p *= 1 + drift + rnd.gauss(0, vol)
        out.append(round(max(p, 0.01), 4))
    return out


def pat(driver="WTI Crude", d="up", N=3, target="USD/INR", h=3, n=80, n_ind=40, p_up=0.65, sig=True,
        unstable=False, recent=0.62, recent_n=30, base_p=0.5, flat_share=0.0):
    return {"driver": driver, "dir": d, "window_days": N, "target": target, "horizon_days": h, "n": n,
            "n_ind": n_ind, "p_up": p_up, "p_down": round(1 - p_up, 4), "up_lo": max(0.0, p_up - 0.1),
            "up_hi": min(1.0, p_up + 0.1), "sig": sig, "recent_p_up": recent, "recent_n": recent_n,
            "unstable": unstable, "edge": round(p_up - base_p, 4), "base_p": base_p, "flat_share": flat_share}


def priors_doc(patterns=None, base=None):
    return {"generated": "2026-10-27", "since": "2016-10-27", "history_days": {"min": 3000, "max": 3650},
            "tests": len(patterns or []), "z_sig": 3.8, "n_sig": 1,
            "instruments": {"WTI Crude": {"slug": "wti-crude", "first": "2016-10-27", "last": "2026-10-26",
                                          "n": 2500, "sigma_daily_pct": 2.0},
                            "USD/INR": {"slug": "usd-inr", "first": "2016-10-27", "last": "2026-10-26",
                                        "n": 2500, "sigma_daily_pct": 0.3}},
            "base": base if base is not None else {},
            "patterns": patterns or []}


def base_row(target="USD/INR", h=3, n=100, p_up=0.6, persist=0.55):
    return {"target": target, "horizon_days": h, "n": n, "p_up": p_up, "flat_share": 0.1,
            "persist_acc": persist, "persist_n": 50}


def write_priors(doc):
    learning_io.safe_write_json(P.PRIORS_FILE, P._pack(doc))


class FakeResp:
    def __init__(self, status=200, body=b"x", chunks=None):
        self.status_code, self._body, self._chunks, self.closed = status, body, chunks, False

    def iter_content(self, n):
        if self._chunks is not None:
            yield from self._chunks
        else:
            yield self._body

    def close(self):
        self.closed = True


class _Quiet:
    def setUp(self):
        super().setUp()
        import contextlib
        import io
        self._q = contextlib.redirect_stdout(io.StringIO())
        self._q.__enter__()

    def tearDown(self):
        self._q.__exit__(None, None, None)
        super().tearDown()


class WilsonAndMath(unittest.TestCase):
    def test_known_values(self):
        lo, hi = P.wilson(50, 100)
        self.assertAlmostEqual(lo, 0.4038, places=3)
        self.assertAlmostEqual(hi, 0.5962, places=3)
        lo, hi = P.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.2775, places=3)

    def test_degenerate_and_bounds(self):
        self.assertEqual(P.wilson(0, 0), (0.0, 1.0))
        self.assertEqual(P.wilson(3, -1), (0.0, 1.0))
        for n in (1, 5, 30, 400):
            for k in (0, n // 2, n):
                lo, hi = P.wilson(k, n)
                self.assertTrue(0.0 <= lo <= k / n <= hi <= 1.0, (k, n, lo, hi))

    def test_interval_narrows_with_n(self):
        w_small = P.wilson(6, 10)
        w_big = P.wilson(600, 1000)
        self.assertGreater(w_small[1] - w_small[0], w_big[1] - w_big[0])

    def test_bonferroni_z_grows_with_tests(self):
        self.assertGreater(P._z_bonferroni(360), P._z_bonferroni(10))
        self.assertAlmostEqual(P._z_bonferroni(1), 1.96, places=2)
        self.assertGreater(P._z_bonferroni(0), 1.9)      # never divides by zero


class FlatBandAndDirection(unittest.TestCase):
    def test_direction_flat_band(self):
        self.assertEqual(P._direction(0.10, 0.15), 0)
        self.assertEqual(P._direction(-0.10, 0.15), 0)
        self.assertEqual(P._direction(0.15, 0.15), 1)
        self.assertEqual(P._direction(-0.15, 0.15), -1)
        self.assertEqual(P._direction(3.0, 0.15), 1)

    def test_flat_threshold_scales_with_horizon_and_never_zero(self):
        for label in P.INSTRUMENTS:
            short, long_ = P._flat_pct(label, 3), P._flat_pct(label, 7)
            self.assertGreater(short, 0.0, label)
            self.assertGreaterEqual(long_, short, label)

    def test_flat_outcome_is_zero_in_outcomes(self):
        dates = iso_days(10)
        closes = [100.0] * 10
        closes[5] = 100.1            # +0.1% over 3 days from index 2: inside a 0.5% flat band
        res = P._outcomes(dates, dates, closes, [2], 3, 0.5)
        self.assertEqual([(i, o) for i, _, o in res], [(2, 0)])
        res = P._outcomes(dates, dates, closes, [2], 3, 0.05)
        self.assertEqual([(i, o) for i, _, o in res], [(2, 1)])


class NoLookAhead(unittest.TestCase):
    def test_outcome_uses_only_the_event_date_and_h_days_later(self):
        dates = iso_days(12)
        base = [100.0 + i * 0.0 for i in range(12)]
        base[5] = 103.0               # event at index 2, h=3 -> reads closes[2] and closes[5]
        a = P._outcomes(dates, dates, list(base), [2], 3, 0.5)
        mut = list(base)
        for i in (0, 1):              # earlier history must not matter
            mut[i] = 1.0
        for i in range(6, 12):        # later history must not matter
            mut[i] = 999.0
        b = P._outcomes(dates, dates, mut, [2], 3, 0.5)
        self.assertEqual(a, b)
        self.assertEqual(a[0][2], 1)

    def test_unobserved_horizon_and_missing_target_dates_are_skipped(self):
        dates = iso_days(10)
        closes = [100.0] * 10
        res = P._outcomes(dates, dates, closes, [8, 9], 3, 0.5)        # 8+3 and 9+3 are past the end
        self.assertEqual(res, [])
        tdates = [d for k, d in enumerate(dates) if k != 4]            # target has no row on index 4
        tcloses = [100.0] * len(tdates)
        res = P._outcomes(dates, tdates, tcloses, [4], 1, 0.5)
        self.assertEqual(res, [])

    def test_alignment_by_date_not_by_position(self):
        # target has an extra early date, so positions differ from the driver's; dates must be matched
        dates = iso_days(10, date(2024, 2, 1))
        tdates = ["2024-01-30"] + dates
        tcloses = [50.0] + [100.0] * 10
        tcloses[1 + 3 + 1] = 110.0          # target index of dates[4] is 5: so the event at driver index 1
        res = P._outcomes(dates, tdates, tcloses, [3], 1, 0.5)   # event dates[3] -> target idx 4 -> idx 5
        self.assertEqual(res[0][2], 1)

    def test_sigma_is_trailing_only(self):
        closes = walk(400, seed=3)
        sig = P._daily_sigma(closes)
        poisoned = closes[:300] + [c * 5 for c in closes[300:]]
        sig2 = P._daily_sigma(poisoned)
        self.assertEqual(sig[:300], sig2[:300])
        self.assertIsNone(sig[0])
        self.assertIsNone(sig[P.MIN_SIGMA_OBS - 1])
        self.assertIsNotNone(sig[P.MIN_SIGMA_OBS + 5])

    def test_events_depend_on_past_only(self):
        closes = walk(500, seed=4)
        dates = iso_days(500)
        sig = P._daily_sigma(closes)
        ev = P._events(dates, closes, sig, 3, 1)
        poisoned = closes[:350] + [c * 3 for c in closes[350:]]
        ev2 = P._events(dates, poisoned, P._daily_sigma(poisoned), 3, 1)
        self.assertEqual([i for i in ev if i < 350], [i for i in ev2 if i < 350])
        self.assertTrue(all(i >= 3 for i in ev))

    def test_event_fires_on_a_clear_spike_only(self):
        closes = [100.0 * (1 + 0.001 * (-1) ** i) for i in range(300)]
        closes[250] = closes[249] * 1.05
        dates = iso_days(300)
        ev = P._events(dates, closes, P._daily_sigma(closes), 1, 1)
        self.assertIn(250, ev)
        evd = P._events(dates, closes, P._daily_sigma(closes), 1, -1)
        self.assertNotIn(250, evd)


class ComputePriors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = iso_days(1500, date(2020, 1, 1))
        cls.series = {"WTI Crude": (d, walk(1500, 11, vol=0.02)), "USD/INR": (d, walk(1500, 12, vol=0.004))}
        cls.pri = P.compute_priors(cls.series)

    def test_every_pattern_has_enough_events_and_sane_numbers(self):
        pats = self.pri["patterns"]
        self.assertTrue(pats)
        ids = [p["id"] for p in pats]
        self.assertEqual(len(ids), len(set(ids)))
        for p in pats:
            self.assertGreaterEqual(p["n"], P.MIN_EVENTS, p["id"])
            self.assertLessEqual(p["n_ind"], p["n"])
            self.assertTrue(0.0 <= p["up_lo"] <= p["p_up"] <= p["up_hi"] <= 1.0 or p["n_ind"] < p["n"], p["id"])
            self.assertAlmostEqual(p["p_up"] + p["p_down"], 1.0, places=3)
            self.assertIsInstance(p["sig"], bool)
        self.assertEqual(self.pri["tests"], len(pats))

    def test_random_walk_has_few_significant_patterns(self):
        # a Bonferroni-corrected, stability-checked test must not light up on pure noise
        self.assertLessEqual(self.pri["n_sig"], max(2, len(self.pri["patterns"]) // 10))

    def test_short_history_gives_no_patterns(self):
        d = iso_days(130)
        out = P.compute_priors({"WTI Crude": (d, walk(130, 1)), "USD/INR": (d, walk(130, 2))})
        self.assertEqual(out["patterns"], [])

    def test_unknown_or_tiny_series_are_ignored(self):
        d = iso_days(50)
        out = P.compute_priors({"Nope": (d, walk(50, 1)), "Gold": (d, walk(50, 2))})
        self.assertEqual((out["patterns"], out["instruments"]), ([], {}))

    def test_trailing_sample_below_minimum_is_dropped(self):
        # 30 non-flat events are required: a series with only a handful of spikes yields none
        closes = [100.0 * (1 + 0.0005 * (-1) ** i) for i in range(400)]
        for i in (200, 250, 300):
            closes[i] = closes[i - 1] * 1.06
        d = iso_days(400)
        out = P.compute_priors({"WTI Crude": (d, closes), "USD/INR": (d, closes)})
        self.assertTrue(all(p["n"] >= P.MIN_EVENTS for p in out["patterns"]))

    def test_deterministic(self):
        again = P.compute_priors(self.series)
        again["generated"] = self.pri["generated"]
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.pri, sort_keys=True))


class PriorProbTests(_Quiet, TmpCwdCase):
    def test_no_data_is_neutral(self):
        self.assertEqual(P.prior_prob("USD/INR", "up", 3), (0.5, 0, "none"))
        write_priors(priors_doc())
        self.assertEqual(P.prior_prob("Not An Instrument", "up", 3), (0.5, 0, "none"))
        self.assertEqual(P.prior_prob("USD/INR", "up", 3), (0.5, 0, "none"))   # no base row for it

    def test_base_rate_is_shrunk_and_symmetric(self):
        write_priors(priors_doc(base={"USD/INR|3": base_row(n=100, p_up=0.6)}))
        p_up, n, src = P.prior_prob("USD/INR", "up", 3)
        p_dn, _, _ = P.prior_prob("USD/INR", "down", 3)
        # documented rule: pulled toward 0.5 by n/(n+K), then by the share of cases that end flat (0.1 here)
        w = 100 / (100 + P.SHRINK_K)
        self.assertAlmostEqual(p_up, 0.5 + 0.1 * w * (1 - 0.1), places=4)
        self.assertAlmostEqual(p_up + p_dn, 1.0, places=3)
        self.assertEqual(src, "base-rate:USD/INR|3")
        self.assertGreater(n, 0)
        self.assertTrue(0.5 < p_up < 0.6)            # shrunk toward 0.5, never past the raw rate
        for alias in ("rupee", "usdinr", "usd/inr"):
            self.assertEqual(P.prior_prob(alias, "up", 3)[0], p_up)

    def test_more_data_means_less_shrinkage(self):
        write_priors(priors_doc(base={"USD/INR|3": base_row(n=40, p_up=0.7)}))
        small = P.prior_prob("USD/INR", "up", 3)[0]
        write_priors(priors_doc(base={"USD/INR|3": base_row(n=4000, p_up=0.7)}))
        big = P.prior_prob("USD/INR", "up", 3)[0]
        self.assertLess(small, big)

    def test_bounds_hold_for_extreme_rates(self):
        for raw in (0.0, 0.01, 0.5, 0.99, 1.0):
            write_priors(priors_doc(base={"USD/INR|3": base_row(n=10 ** 6, p_up=raw)}))
            for direction in ("up", "down"):
                p = P.prior_prob("USD/INR", direction, 3)[0]
                self.assertTrue(0.2 <= p <= 0.8, (raw, direction, p))
        write_priors(priors_doc(base={"USD/INR|3": base_row(n=10 ** 6, p_up=1.0)}))
        self.assertEqual(P.prior_prob("USD/INR", "up", 3)[0], 0.8)
        self.assertEqual(P.prior_prob("USD/INR", "down", 3)[0], 0.2)

    def test_horizon_snaps_to_nearest_supported(self):
        write_priors(priors_doc(base={"USD/INR|3": base_row(), "USD/INR|7": base_row(h=7, p_up=0.7)}))
        self.assertTrue(P.prior_prob("USD/INR", "up", 5)[2].endswith("|3"))      # tie -> shorter
        self.assertTrue(P.prior_prob("USD/INR", "up", 9)[2].endswith("|7"))
        self.assertTrue(P.prior_prob("USD/INR", "up", "garbage")[2].endswith("|3"))

    def test_matching_pattern_beats_base_rate_only_when_driver_moved_enough(self):
        write_priors(priors_doc([pat(p_up=0.7, n_ind=60)], {"USD/INR|3": base_row(p_up=0.5)}))
        # WTI sigma 2.0% -> a 3-day 1-sigma move is 2*sqrt(3)=3.46%
        p, n, src = P.prior_prob("INR", "up", 3, drivers={"wti": 5.0})
        self.assertTrue(src.startswith("pattern:"), src)
        # winner's-curse shrinkage: k = SHRINK_K + 0.5 * (patterns searched for this target; 1 here)
        self.assertAlmostEqual(p, 0.5 + 0.2 * (60 / (60 + P.SHRINK_K + 0.5)), places=3)
        self.assertTrue(0.5 < p < 0.7)
        self.assertAlmostEqual(P.prior_prob("INR", "down", 3, drivers={"wti": 5.0})[0], 1.0 - p, places=3)
        self.assertEqual(P.prior_prob("INR", "up", 3, drivers={"wti": 5.0})[1], 60)   # independent sample size
        self.assertTrue(P.prior_prob("INR", "up", 3, drivers={"wti": 1.0})[2].startswith("base-rate"))
        self.assertTrue(P.prior_prob("INR", "up", 3, drivers={"wti": -5.0})[2].startswith("base-rate"))
        for bad in ({"wti": "x"}, {"wti": float("nan")}, {"unknown": 9}, {"wti": None}, "not a dict", 7):
            P.prior_prob("INR", "up", 3, drivers=bad)       # must not raise

    def test_unstable_pattern_loses_to_a_steadier_one(self):
        steady = pat(N=1, p_up=0.62, n_ind=60)
        shaky = pat(N=3, p_up=0.66, n_ind=60, unstable=True)
        write_priors(priors_doc([shaky, steady], {"USD/INR|3": base_row()}))
        _, _, src = P.prior_prob("USD/INR", "up", 3, drivers={"WTI Crude": 9.0})
        self.assertIn("up1", src)


class PriorsTextTests(_Quiet, TmpCwdCase):
    def test_empty_and_filtering(self):
        self.assertEqual(P.priors_text(), "")
        write_priors(priors_doc([pat(), pat(sig=False, N=1), pat(unstable=True, N=7)],
                                {"USD/INR|3": base_row(), "USD/INR|7": base_row(h=7)}))
        txt = P.priors_text()
        lines = txt.splitlines()
        self.assertEqual(lines[-1], P.CAVEAT)          # the caveat line closes every non-empty text
        self.assertEqual(sum("sigma over" in l for l in lines), 1)      # only the significant, stable one
        self.assertEqual(sum(l.startswith("Base rate") for l in lines), 1)   # only the 3-day base rate
        self.assertEqual(P.priors_text(["Gold"]), "")                    # nothing known about gold
        self.assertEqual(P.priors_text(["nonsense"]), "")
        self.assertIn("USD/INR", P.priors_text(["rupee"]))

    def test_caps_and_whole_lines_only(self):
        pats = [pat(N=n, h=h, d=d, p_up=0.6 + 0.01 * i) for i, (n, h, d) in
                enumerate((n, h, d) for n in (1, 3, 7) for h in (1, 3, 7) for d in ("up", "down"))]
        write_priors(priors_doc(pats, {"USD/INR|3": base_row(), "WTI Crude|3": base_row("WTI Crude")}))
        full = P.priors_text(max_chars=100000).splitlines()
        self.assertGreater(len(full), 3)
        for cap in (250, 400, 600, 900):
            out = P.priors_text(max_chars=cap)
            self.assertLessEqual(len(out), cap)
            for line in out.splitlines():
                self.assertIn(line, full)             # never a truncated fragment
        self.assertLessEqual(len(P.priors_text()), 900)
        self.assertEqual(P.priors_text(max_chars=5), "")
        self.assertLessEqual(sum("sigma over" in l for l in full), 12)

    def test_text_is_numbers_and_known_names_only(self):
        write_priors(priors_doc([pat()], {"USD/INR|3": base_row()}))
        for line in P.priors_text().splitlines():
            self.assertRegex(line, r"^[A-Za-z0-9 /&'>=:;%().,~-]+$")


class PackRoundTrip(_Quiet, TmpCwdCase):
    def test_pack_unpack_round_trip(self):
        doc = priors_doc([pat(), pat("USD/INR", "down", 7, "USD/INR", 1, sig=False, unstable=True, recent=None,
                                    recent_n=0)], {"USD/INR|3": base_row()})
        write_priors(doc)
        with open(P.PRIORS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertIn("rows", raw)
        self.assertNotIn("patterns", raw)
        self.assertTrue(all(isinstance(v, (int, float)) or v is None for r in raw["rows"] for v in r))
        got = P.load_priors()
        self.assertEqual(len(got["patterns"]), 2)
        for a, b in zip(doc["patterns"], got["patterns"]):
            for k in ("driver", "dir", "window_days", "target", "horizon_days", "n", "n_ind", "p_up", "up_lo",
                      "up_hi", "sig", "recent_p_up", "recent_n", "unstable", "base_p", "flat_share"):
                self.assertEqual(a[k], b[k], k)
        self.assertEqual(got["base"], doc["base"])
        self.assertEqual(got["instruments"], doc["instruments"])

    def test_packed_file_is_much_smaller_than_json_patterns(self):
        pats = [pat(N=n, h=h, d=d) for n in (1, 3, 7) for h in (1, 3, 7) for d in ("up", "down")] * 10
        doc = priors_doc(pats)
        self.assertLess(len(json.dumps(P._pack(doc))), len(json.dumps(doc)) // 2)

    def test_load_priors_survives_garbage(self):
        os.makedirs(os.path.dirname(P.PRIORS_FILE), exist_ok=True)
        for junk in ("not json{", "[1,2,3]", '"str"', '{"rows": 5, "labels": 3}', '{"rows": [[1]], "labels": ["a"]}'):
            with open(P.PRIORS_FILE, "w", encoding="utf-8") as f:
                f.write(junk)
            self.assertIsInstance(P.load_priors(), dict)
            self.assertIsInstance(P.priors_text(), str)
            self.assertEqual(P.prior_prob("USD/INR", "up", 3)[0] >= 0.2, True)
            self.assertIsInstance(P.history_summary(), dict)


class NetworkFence(_Quiet, TmpCwdCase):
    def setUp(self):
        super().setUp()
        import requests
        self.requests = requests
        self.gets = []

        def fake_get(url, **kw):
            self.gets.append((url, kw))
            return self.resp
        self.mp.setattr(requests, "get", fake_get)
        self.resp = FakeResp(200, b"ok")

    def test_only_whitelisted_https_hosts(self):
        bad = ["https://evil.example.com/x", "http://fred.stlouisfed.org/x",
               "https://fred.stlouisfed.org.evil.com/x", "https://fred.stlouisfed.org@evil.com/x",
               "https://evil.com/fred.stlouisfed.org/", "ftp://fred.stlouisfed.org/x", "file:///etc/passwd",
               "https://127.0.0.1/x", "javascript:alert(1)", "", "https://fred.stlouisfed.org"]
        for url in bad:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    P._http_get(url, 5)
        self.assertEqual(self.gets, [])           # nothing was even attempted

    def test_allowed_hosts_ok_with_safe_request_options(self):
        for host in P.ALLOWED_HOSTS:
            self.assertEqual(P._http_get(f"https://{host}/x?y=1", 5), b"ok")
        url, kw = self.gets[0]
        self.assertFalse(kw["allow_redirects"])
        self.assertIn("User-Agent", kw["headers"])
        self.assertEqual({k.lower() for k in kw["headers"]}, {"user-agent"})   # no credentials, ever

    def test_non_200_and_redirects_fail(self):
        for code in (301, 302, 403, 404, 500):
            self.resp = FakeResp(code)
            with self.assertRaises(ValueError):
                P._http_get("https://fred.stlouisfed.org/x", 5)
            self.assertTrue(self.resp.closed)

    def test_response_size_cap(self):
        self.mp.setattr(P, "MAX_RESPONSE_BYTES", 1000)
        self.resp = FakeResp(200, chunks=[b"a" * 600, b"b" * 600])
        with self.assertRaises(ValueError):
            P._http_get("https://fred.stlouisfed.org/x", 5)
        self.assertTrue(self.resp.closed)

    def test_total_deadline(self):
        def slow():
            yield b"a"
            time.sleep(0.05)
            yield b"b"
        resp = FakeResp(200)
        resp.iter_content = lambda n: slow()
        self.resp = resp
        with self.assertRaises(TimeoutError):
            P._http_get("https://fred.stlouisfed.org/x", 0.01)

    def test_symbols_are_validated_before_any_request(self):
        for sym in ("bad sym", "A;B", "a/b", "../x", "x" * 40, "", "DGS10&evil=1", "dgs10"):
            with self.assertRaises(ValueError):
                P._fetch_fred(sym, "2020-01-01", 5)
        for sym in ("a b", "A/B", "X" * 30, "", "A%2FB", "?x"):
            with self.assertRaises(ValueError):
                P._fetch_yahoo(sym, "2020-01-01", 5)
        self.assertEqual(self.gets, [])

    def test_fetch_builds_urls_on_allowed_hosts(self):
        self.resp = FakeResp(200, b"observation_date,DEXINUS\n2024-01-02,83.1\n")
        self.assertEqual(P._fetch_fred("DEXINUS", "2020-01-01", 5), [("2024-01-02", 83.1)])
        self.assertTrue(self.gets[-1][0].startswith("https://fred.stlouisfed.org/"))
        self.resp = FakeResp(200, json.dumps({"chart": {"result": [{"timestamp": [1700000000], "indicators": {
            "quote": [{"close": [1.5]}]}}]}}).encode())
        self.assertEqual(P._fetch_yahoo("^TNX", "2020-01-01", 5), [("2023-11-14", 1.5)])
        self.assertTrue(self.gets[-1][0].startswith("https://query1.finance.yahoo.com/"))
        self.assertIn("%5ETNX", self.gets[-1][0])             # symbol is percent-encoded


class Parsers(unittest.TestCase):
    def test_fred_strict_rows(self):
        raw = (b"observation_date,DEXINUS\n2024-01-02,83.1\n2024-01-03,.\n2024-01-04,abc\n2024-01-05,1e5\n"
               b"2024-01-06,-3\n2024-01-07,0\n2024-01-08,nan\nbad-date,5\n2024-01-09,5,6\n=cmd(),5\n"
               b"2024-01-10,84.2\n2024-01-11,inf\n2024-01-12, 85.5 \n")
        self.assertEqual(P._parse_fred_csv(raw), [("2024-01-02", 83.1), ("2024-01-10", 84.2), ("2024-01-12", 85.5)])

    def test_fred_bad_headers_and_bytes(self):
        for raw in (b"", b"a,b,c\n2024-01-01,1,2\n", b"foo,bar\n2024-01-01,1\n", b"<html><body>no</body></html>",
                    b"DATE\n2024-01-01\n"):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(ValueError):
                    P._parse_fred_csv(raw)
        with self.assertRaises(ValueError):
            P._parse_fred_csv(b"DATE,X\n\xff\xfe,1\n")           # not UTF-8

    def test_yahoo_skips_today_and_non_numbers(self):
        today_ts = int(datetime.now(timezone.utc).timestamp())
        obj = {"chart": {"result": [{"timestamp": [1700000000, 1700086400, 1700172800, 1700259200, 1700345600,
                                                   1700432000, today_ts],
                                     "indicators": {"quote": [{"close": [1.5, None, True, "9", -4.0,
                                                                         float("nan"), 7.0]}]}}]}}
        self.assertEqual(P._parse_yahoo(json.dumps(obj).encode()), [("2023-11-14", 1.5)])

    def test_yahoo_malformed_raises(self):
        for raw in (b"", b"not json", b"{}", b'{"chart": {"result": []}}', b'{"chart": {"result": [{}]}}'):
            with self.assertRaises(Exception):
                P._parse_yahoo(raw)

    def test_canon_names(self):
        self.assertEqual(P._canon("rupee"), "USD/INR")
        self.assertEqual(P._canon("S&P 500"), "S&P 500")
        self.assertEqual(P._canon("sp500"), "S&P 500")
        for bad in (None, 5, "", "Bitcoin", ["x"]):
            self.assertIsNone(P._canon(bad))


class FallbackChain(_Quiet, TmpCwdCase):
    def rows(self, n=300, start=date(2024, 1, 1)):
        return [((start + timedelta(days=i)).isoformat(), 80.0 + i * 0.01) for i in range(n)]

    def fetchers(self, **fns):
        calls = {k: [] for k in fns}
        table = {}
        for k, fn in fns.items():
            def wrap(sym, since, timeout, k=k, fn=fn):
                calls[k].append((sym, since))
                return fn(sym, since, timeout)
            table[k] = wrap
        self.mp.setattr(P, "FETCHERS", table)
        return calls

    def boom(self, *a):
        raise ConnectionError("down")

    def test_first_source_wins(self):
        calls = self.fetchers(fred=lambda *a: self.rows(), yahoo=self.boom)
        self.assertEqual(P._refresh_one("USD/INR", None, NOW, time.monotonic() + 60), "ok")
        self.assertEqual(len(calls["fred"]), 1)
        self.assertEqual(calls["yahoo"], [])
        self.assertEqual(P._load_cache("USD/INR")["source"], "fred")

    def test_falls_back_when_first_fails(self):
        calls = self.fetchers(fred=self.boom, yahoo=lambda *a: self.rows())
        self.assertEqual(P._refresh_one("USD/INR", None, NOW, time.monotonic() + 60), "ok")
        self.assertEqual(len(calls["fred"]), 1)
        c = P._load_cache("USD/INR")
        self.assertEqual(c["source"], "yahoo")
        self.assertEqual(len(c["dates"]), 300)
        self.assertEqual(c["dates"][0], "2024-01-01")

    def test_falls_back_when_first_returns_too_little(self):
        self.fetchers(fred=lambda *a: self.rows(20), yahoo=lambda *a: self.rows())
        self.assertEqual(P._refresh_one("USD/INR", None, NOW, time.monotonic() + 60), "ok")
        self.assertEqual(P._load_cache("USD/INR")["source"], "yahoo")

    def test_all_sources_fail(self):
        self.fetchers(fred=self.boom, yahoo=self.boom)
        self.assertEqual(P._refresh_one("USD/INR", None, NOW, time.monotonic() + 60), "fail")
        self.assertIsNone(P._load_cache("USD/INR"))

    def test_no_time_left_means_no_request(self):
        calls = self.fetchers(fred=lambda *a: self.rows(), yahoo=lambda *a: self.rows())
        self.assertEqual(P._refresh_one("USD/INR", None, NOW, time.monotonic() - 1), "budget")
        self.assertEqual((calls["fred"], calls["yahoo"]), ([], []))

    def test_unknown_source_kind_is_skipped(self):
        calls = self.fetchers(yahoo=lambda *a: self.rows())      # no 'fred' fetcher at all
        self.assertEqual(P._refresh_one("USD/INR", None, NOW, time.monotonic() + 60), "ok")
        self.assertEqual(len(calls["yahoo"]), 1)

    def test_tail_refresh_merges_and_requests_only_recent_days(self):
        self.fetchers(fred=lambda *a: self.rows(), yahoo=self.boom)
        P._refresh_one("USD/INR", None, NOW, time.monotonic() + 60)
        cache = P._load_cache("USD/INR")
        last = cache["dates"][-1]
        newer = [((date.fromisoformat(last) + timedelta(days=i)).isoformat(), 90.0) for i in range(-2, 4)]
        calls = self.fetchers(fred=lambda *a: newer, yahoo=self.boom)
        self.assertEqual(P._refresh_one("USD/INR", cache, NOW, time.monotonic() + 60), "ok")
        since = calls["fred"][0][1]
        self.assertEqual(since, (date.fromisoformat(last) - timedelta(days=10)).isoformat())
        merged = P._load_cache("USD/INR")
        self.assertEqual(len(merged["dates"]), 300 + 3)
        self.assertEqual(merged["dates"], sorted(set(merged["dates"])))

    def test_refresh_priors_end_to_end_and_backoff(self):
        def per_symbol(sym, since, timeout):
            seed = sum(ord(c) for c in sym)
            ds = iso_days(900, date(2024, 1, 1))
            return list(zip(ds, walk(900, seed, vol=0.01)))
        calls = self.fetchers(fred=per_symbol, yahoo=per_symbol)
        self.assertTrue(P.refresh_priors(NOW, budget_s=60))
        pri = P.load_priors()
        self.assertEqual(set(pri["instruments"]), set(P.INSTRUMENTS))
        self.assertTrue(pri["patterns"])
        self.assertLessEqual(os.path.getsize(P.PRIORS_FILE), P.MAX_PRIORS_BYTES)
        txt = P.priors_text()
        self.assertIsInstance(txt, str)
        n_calls = sum(len(v) for v in calls.values())
        # a second run within 20h reuses every cache: no new requests
        self.assertTrue(P.refresh_priors(NOW + timedelta(hours=1), budget_s=60))
        self.assertEqual(sum(len(v) for v in calls.values()), n_calls)

    def test_failed_refresh_backs_off_for_six_hours(self):
        calls = self.fetchers(fred=self.boom, yahoo=self.boom)
        self.assertFalse(P.refresh_priors(NOW, budget_s=30))
        first = sum(len(v) for v in calls.values())
        self.assertGreater(first, 0)
        self.assertFalse(P.refresh_priors(NOW + timedelta(hours=1), budget_s=30))
        self.assertEqual(sum(len(v) for v in calls.values()), first)       # backed off
        self.assertFalse(P.refresh_priors(NOW + timedelta(hours=7), budget_s=30))
        self.assertGreater(sum(len(v) for v in calls.values()), first)     # tries again later

    def test_kill_switch_means_no_network_and_no_writes(self):
        calls = self.fetchers(fred=lambda *a: self.rows(), yahoo=lambda *a: self.rows())
        self.mp.setitem(os.environ, "LEARNING_ENABLED", "off")
        before = tree_snapshot(".")
        self.assertFalse(P.refresh_priors(NOW))
        self.assertEqual(tree_snapshot("."), before)
        self.assertEqual((calls["fred"], calls["yahoo"]), ([], []))

    def test_never_raises_on_garbage_from_a_fetcher(self):
        for bad in (lambda *a: None, lambda *a: "text", lambda *a: [("x",)], lambda *a: 5,
                    lambda *a: [("2024-01-01", "a")] * 400):
            self.fetchers(fred=bad, yahoo=bad)
            try:
                out = P.refresh_priors(NOW, budget_s=20)
            except Exception as e:                    # pragma: no cover
                self.fail(f"refresh_priors raised {type(e).__name__}")
            self.assertIn(out, (True, False))
            for f in (P.STATE_FILE,):
                if os.path.exists(f):
                    os.remove(f)

    def test_corrupt_cache_is_set_aside_not_trusted(self):
        os.makedirs(P.HISTORY_DIR, exist_ok=True)
        with open(P._history_path("Gold"), "w", encoding="utf-8") as f:
            f.write('{"dates": [20240102, 20240101], "closes": [1, 2]}')      # unsorted
        self.assertIsNone(P._load_cache("Gold"))


if __name__ == "__main__":
    unittest.main()
