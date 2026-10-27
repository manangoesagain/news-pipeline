"""Ledger tests: recording, grading, scorecard maths, bounded growth, fence and robustness.
Each test runs in its own temp working directory (see _learn_support)."""
import builtins
import json
import os
import pathlib
import unittest
from datetime import datetime, timedelta

import _stubs  # noqa: F401
from _learn_support import TmpCwdCase


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _jload(p):
    return json.loads(_read(p))


def _write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


D0 = datetime(2026, 5, 1, 7, 0)


def day(n):
    return (D0 + timedelta(days=n)).strftime("%Y-%m-%d")


def write_hist(rows):
    import market_history
    h = [{"date": day(k), "labels": v, "change_pct": {}} for k, v in sorted(rows.items())]
    market_history.HISTORY_FILE.write_text(json.dumps(h))


def node(i, label, **kw):
    n = {"id": i, "label": label, "domain": "commodities", "card": {"so_what": "x", "predictions": []},
         "impact": {"size": "big", "sure": "high", "when": "days", "top5": 1}}
    n.update(kw)
    return n


def graph(nodes, edges=None, market=None):
    return {"nodes": nodes, "edges": edges or [], "market": market or [
        {"label": "USD/INR", "price": "83.00", "change": 0, "change_pct": 0},
        {"label": "WTI Crude", "price": "$80.00", "change": 0, "change_pct": 0}]}


def oil_graph():
    n = node("n2", "Oil spike", direction="up",
             market={"label": "WTI Crude", "price": "80", "change": 1, "change_pct": 1})
    n["card"]["currency"] = {"INR": {"dir": "weaker", "why": "oil"}, "CAD": {"dir": "flat", "why": ""},
                             "USD": {"dir": "stronger", "why": "haven"}}
    n["card"]["predictions"] = [{"if": "OPEC cuts", "then": "oil tops 90", "odds": "70%", "horizon": "weeks"}]
    src = node("n1", "Hormuz tension")
    return graph([src, n], [{"from": "n1", "to": "n2", "strength": 3}])


def _fake_call(i, conf, res, soft=False, at=None, topic="t"):
    return {"id": f"c{i}", "made": day(0), "kind": "market", "subject": "X", "text": "", "direction": "up",
            "confidence": conf, "topic": topic, "link_type": "a->b", "instrument": "X", "due": day(3),
            "status": "graded", "grade": {"result": res, "soft": soft, "at": at or day(5),
                                          "baseline_right": (res == "right") if not soft else None}}


class LedgerTests(TmpCwdCase):
    def setUp(self):
        super().setUp()
        import market_history
        self.mp.setattr(market_history, "HISTORY_FILE", pathlib.Path(self.tmp) / "_data" / "market_history.json")

    def test_record_basic(self):
        import learning_ledger as L
        new = L.record_calls(oil_graph(), D0)
        by = {(c["kind"], c["subject"]): c for c in new}
        assert set(by) == {("currency", "INR"), ("currency", "USD"), ("market", "WTI Crude"),
                           ("prediction", "IF OPEC cuts THEN oil tops 90")}
        inr = by[("currency", "INR")]
        assert inr["instrument"] == "USD/INR" and inr["direction"] == "weaker" and inr["horizon_days"] == 3
        assert inr["due"] == day(3) and inr["baseline_price"] == 83.0 and inr["expect"] == "up"
        assert inr["link_type"] == "hormuz tension->currency" and inr["confidence"] == "high"
        assert by[("currency", "USD")]["expect"] == "up"
        assert by[("market", "WTI Crude")]["baseline_price"] == 80.0
        p = by[("prediction", "IF OPEC cuts THEN oil tops 90")]
        assert p["horizon_days"] == 10 and p["confidence"] == "high" and p["instrument"] == ""
        assert inr["id"].startswith("c-20260501-") and len({c["id"] for c in new}) == 4


    def test_dedupe_and_deterministic_ids(self):
        import learning_ledger as L
        assert len(L.record_calls(oil_graph(), D0)) == 4
        assert L.record_calls(oil_graph(), D0 + timedelta(hours=5)) == []
        # independence rule: the same idea is not re-added while it is still open
        assert L.record_calls(oil_graph(), D0 + timedelta(days=1)) == []
        calls = _jload("_data/_learning/calls.json")["calls"]
        assert len(calls) == 4 and len({c["id"] for c in calls}) == 4


    def test_light_cards_skipped(self):
        import learning_ledger as L
        g = oil_graph()
        g["nodes"][1]["card"]["light"] = True
        assert L.record_calls(g, D0) == []


    def test_cap_60(self):
        import learning_ledger as L
        n = node("n1", "A")
        n["card"]["predictions"] = [{"if": f"a{i}", "then": f"b{i}", "odds": "low"} for i in range(100)]
        assert len(L.record_calls(graph([n]), D0)) == 60


    def test_scenarios(self):
        import learning_ledger as L
        new = L.record_scenarios([{"if": "x", "then": "y", "likelihood": "low", "signpost": "s"}, "junk", {}], D0)
        assert len(new) == 1 and new[0]["horizon_days"] == 14 and new[0]["source"] == "scenario"
        assert new[0]["confidence"] == "low"


    def test_grade_right_wrong_flat(self):
        import learning_ledger as L
        L.record_calls(oil_graph(), D0)
        write_hist({-3: {"USD/INR": 82.0, "WTI Crude": 79.0, "US Dollar Index": 101},
                    0: {"USD/INR": 83.0, "WTI Crude": 80.0, "US Dollar Index": 100},
                    3: {"USD/INR": 84.0, "WTI Crude": 80.04, "US Dollar Index": 99}})
        assert L.grade_calls(D0 + timedelta(days=2)) == []
        out = L.grade_calls(D0 + timedelta(days=3))
        got = {c["subject"]: c for c in out if c["kind"] != "prediction"}
        g = got["INR"]["grade"]
        assert g["result"] == "right" and abs(g["actual_change_pct"] - 1.2048) < 0.01 and g["size_actual"] == "medium"
        assert g["size_matched"] is False
        assert g["baseline_right"] is True
        assert got["USD"]["grade"]["result"] == "wrong" and got["USD"]["grade"]["baseline_right"] is True  # persistence (down) beat the call
        assert got["WTI Crude"]["grade"]["result"] == "flat" and got["WTI Crude"]["grade"]["baseline_right"] is None
        assert L.grade_calls(D0 + timedelta(days=4)) == []


    def test_stronger_rupee_sign(self):
        import learning_ledger as L
        n = node("n1", "RBI")
        n["card"]["currency"] = {"INR": {"dir": "stronger", "why": "w"}}
        L.record_calls(graph([n]), D0)
        write_hist({0: {"USD/INR": 83.0}, 3: {"USD/INR": 82.0}})
        out = L.grade_calls(D0 + timedelta(days=3))
        assert out[0]["grade"]["result"] == "right"


    def test_unclear_when_no_price(self):
        import learning_ledger as L
        L.record_calls(oil_graph(), D0)
        write_hist({0: {"USD/INR": 83.0}, 20: {"USD/INR": 90.0}})
        assert [c for c in L.grade_calls(D0 + timedelta(days=4)) if c["instrument"]] == []
        out = [c for c in L.grade_calls(D0 + timedelta(days=7)) if c["instrument"]]
        assert out and all(c["status"] == "unclear" for c in out)


    def test_waits_for_window_then_uses_nearest(self):
        import learning_ledger as L
        n = node("n1", "RBI")
        n["card"]["currency"] = {"INR": {"dir": "weaker", "why": "w"}}
        L.record_calls(graph([n]), D0)
        write_hist({0: {"USD/INR": 83.0}, 2: {"USD/INR": 83.5}})
        assert L.grade_calls(D0 + timedelta(days=3)) == []
        out = L.grade_calls(D0 + timedelta(days=5))
        assert out[0]["grade"]["price_date"] == day(2) and out[0]["grade"]["result"] == "right"


    def test_baseline_none_without_prior_history(self):
        import learning_ledger as L
        n = node("n1", "RBI")
        n["card"]["currency"] = {"INR": {"dir": "weaker", "why": "w"}}
        L.record_calls(graph([n]), D0)
        write_hist({0: {"USD/INR": 83.0}, 3: {"USD/INR": 84.0}})
        assert L.grade_calls(D0 + timedelta(days=3))[0]["grade"]["baseline_right"] is None


    def test_soft_grading_from_patterns(self):
        import learning_ledger as L
        L.record_scenarios([{"if": "Fed cuts rates sharply", "then": "dollar index falls below 100",
                             "likelihood": "70%"}], D0)
        _write("_data/predictions.json", json.dumps({"predictions": [{"id": "p1", "if": "Fed cuts rates sharply",
                                    "then": "dollar index falls below 100",
                                    "status": "partly", "evidence": "mixed"}]}))
        out = L.grade_calls(D0 + timedelta(days=14))
        assert out[0]["grade"] == {"result": "partial", "at": day(14), "soft": True, "evidence": "mixed"}


    def test_soft_unmatched_goes_unclear_late(self):
        import learning_ledger as L
        L.record_scenarios([{"if": "aa bb cc", "then": "dd ee ff"}], D0)
        assert L.grade_calls(D0 + timedelta(days=20)) == []
        assert L.grade_calls(D0 + timedelta(days=40))[0]["status"] == "unclear"



    def test_scorecard_maths(self):
        import learning_ledger as L, learning_io
        calls = [_fake_call(i, "high", "right") for i in range(8)] + \
                [_fake_call(10 + i, "high", "wrong") for i in range(2)]
        calls += [_fake_call(20 + i, "low", "right") for i in range(4)] + \
                 [_fake_call(30 + i, "low", "wrong") for i in range(6)]
        calls += [_fake_call(40, "low", "flat"), _fake_call(41, "medium", "partial", soft=True)]
        calls += [{"id": "o", "status": "open", "made": day(0)}]
        learning_io.safe_write_json(L.CALLS_FILE, {"calls": calls})
        sc = L.scorecard(D0 + timedelta(days=6))
        assert sc["n_graded"] == 22 and sc["n_open"] == 1 and sc["n_price_graded"] == 21
        assert sc["hit_rate"] == round(12 / 20, 3)
        assert sc["hit_rate_soft"] == 0.5
        assert sc["by_confidence"]["high"] == {"n": 10, "hits": 8.0, "rate": 0.8}
        assert sc["by_confidence"]["low"]["rate"] == 0.4
        assert sc["calibrated"] is True and sc["enough_data"] is True
        assert sc["hit_rate_baseline"] == 0.6
        assert sc["recent_30d"]["n"] == 21 and sc["prev_30d"]["n"] == 0
        assert L.track_record()["n_graded"] == 22
        assert len(L.recent_graded(5)) == 5


    def test_track_record_default(self):
        import learning_ledger as L
        t = L.track_record()
        assert t["enough_data"] is False and t["n_graded"] == 0 and t["hit_rate"] is None


    def test_archive_and_bounded_growth(self):
        import learning_ledger as L, learning_io
        old = _fake_call(1, "high", "right")
        old["made"] = "2025-01-01"
        learning_io.safe_write_json(L.CALLS_FILE, {"calls": [old]})
        L.record_scenarios([{"if": "q", "then": "r"}], D0)
        assert "c1" not in [c["id"] for c in _jload(L.CALLS_FILE)["calls"]]
        assert _jload(L.ARCHIVE_FILE)["calls"][0]["id"] == "c1"
        big = [dict(_fake_call(i, "high", "right"), text="x" * 150, made=day(-5)) for i in range(3000)]
        big.append({"id": "keep-open", "status": "open", "made": day(0), "due": day(3),
                    "kind": "prediction", "subject": "s"})
        L._save_all({"calls": big}, D0)
        assert os.path.getsize(L.CALLS_FILE) <= 300_000
        assert any(c["id"] == "keep-open" for c in _jload(L.CALLS_FILE)["calls"])
        assert os.path.getsize(L.ARCHIVE_FILE) <= 300_000
        _jload(L.ARCHIVE_FILE)


    def test_garbage_never_raises(self):
        import learning_ledger as L
        weird = {"nodes": [{"card": {"currency": {"INR": None, "CAD": {"dir": 4}},
                                     "predictions": [None, {"if": 1}]},
                            "impact": 7, "market": 9, "direction": None}], "market": [None, {"label": 3}]}
        for g in (None, 5, "x", [], {"nodes": "z"},
                  {"nodes": [None, 3, {"card": 4}, {"card": {"currency": 5, "predictions": "q"}}]}, weird):
            assert isinstance(L.record_calls(g, D0), list)
        assert isinstance(L.record_calls(oil_graph(), "notadate"), list)
        assert L.record_scenarios(None) == [] and L.record_scenarios("x") == []
        assert isinstance(L.grade_calls("bad"), list)
        assert L.recent_graded("zz") == []


    def test_corrupt_files_never_raise(self):
        import learning_ledger as L, learning_io
        os.makedirs(learning_io.LEARN_DIR)
        _write(L.CALLS_FILE, "{not json")
        _write(L.SCORECARD_FILE, "[1]")
        assert isinstance(L.record_calls(oil_graph(), D0), list)
        assert L.track_record()["enough_data"] is False
        assert L.scorecard(D0)["n_graded"] == 0


    def test_failure_prints_one_line(self):
        import learning_ledger as L, learning_io

        def boom(*a):
            raise OSError("disk")
        self.mp.setattr(learning_io, "safe_write_json", boom)
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert L.record_calls(oil_graph(), D0) == []
        out = buf.getvalue().strip().splitlines()
        assert len(out) == 1 and out[0].startswith("[LEARN]")


    def test_writes_only_inside_learn_dir(self):
        import learning_ledger as L, learning_io
        write_hist({0: {"USD/INR": 83.0}, 3: {"USD/INR": 84.0}})
        real_open = builtins.open
        real_wt = pathlib.Path.write_text
        real_replace = os.replace
        written = []

        def spy(f, mode="r", *a, **k):
            if any(m in str(mode) for m in "wax+"):
                written.append(os.path.abspath(str(f)))
            return real_open(f, mode, *a, **k)

        def spy_wt(self, *a, **k):
            written.append(str(self.resolve()))
            return real_wt(self, *a, **k)
        def spy_replace(src, dst, *a, **k):
            written.append(os.path.abspath(str(dst)))
            return real_replace(src, dst, *a, **k)
        self.mp.setattr(os, "replace", spy_replace)
        self.mp.setattr(builtins, "open", spy)
        self.mp.setattr(pathlib.Path, "write_text", spy_wt)
        L.record_calls(oil_graph(), D0)
        L.record_scenarios([{"if": "a b", "then": "c d"}], D0)
        L.grade_calls(D0 + timedelta(days=30))
        L.scorecard(D0 + timedelta(days=30))
        L.track_record()
        L.recent_graded()
        base = os.path.abspath(learning_io.LEARN_DIR)
        assert written
        assert all(os.path.commonpath([base, p]) == base for p in written), written


    def test_no_email_or_key_persisted(self):
        import learning_ledger as L
        n = node("n1", "A")
        n["card"]["predictions"] = [{"if": "mail me a@b.com", "then": "key sk-abcdefghijklmnopqrstuv", "odds": "low"}]
        L.record_calls(graph([n]), D0)
        s = _read(L.CALLS_FILE)
        assert "a@b.com" not in s and "sk-abcdefghijklmnopqrstuv" not in s

    def test_score_call_rule(self):
        import learning_ledger as L
        g = lambda r: {"status": "graded", "grade": {"result": r}}
        assert [L.score_call(g(r)) for r in ("right", "partial", "wrong")] == [1.0, 0.5, 0.0]
        for c in (g("flat"), g("unclear"), {"status": "open"}, None, "x", {"status": "graded", "grade": 5}):
            assert L.score_call(c) is None

    def test_flat_threshold_is_instrument_aware(self):
        import learning_ledger as L
        assert L.flat_threshold("USD/INR", 3) < L.flat_threshold("WTI Crude", 3) < L.flat_threshold("BTC", 3)
        assert L.flat_threshold("WTI Crude", 10) == round(L.flat_threshold("WTI Crude", 5) * 1.5, 4)
        assert L.flat_threshold(None, "bad") > 0

    def test_scorecard_counts_and_enough_data_rule(self):
        import learning_ledger as L, learning_io
        calls = [_fake_call(i, "high", "right") for i in range(12)] +                 [_fake_call(20 + i, "low", "flat") for i in range(3)] +                 [dict(_fake_call(30 + i, "low", "right", soft=True)) for i in range(8)]
        calls.append(dict(_fake_call(50, "low", "wrong"), status="unclear", grade={"result": "unclear"}))
        learning_io.safe_write_json(L.CALLS_FILE, {"calls": calls})
        sc = L.scorecard(D0 + timedelta(days=6), write=False)
        assert sc["n_scored"] == 12 and sc["n_soft"] == 8 and sc["n_flat"] == 3 and sc["n_unclear"] == 1
        assert sc["enough_data"] is True          # n_scored + n_soft >= 20
        assert not os.path.exists(L.SCORECARD_FILE)   # write=False writes nothing

    def test_open_idea_blocks_duplicate_but_old_graded_idea_can_return(self):
        import learning_ledger as L
        assert len(L.record_calls(oil_graph(), D0)) == 4
        write_hist({0: {"USD/INR": 83.0, "WTI Crude": 80.0, "US Dollar Index": 100},
                    20: {"USD/INR": 84.0, "WTI Crude": 82.0, "US Dollar Index": 101}})
        assert L.record_calls(oil_graph(), D0 + timedelta(days=5)) == []
        L.grade_calls(D0 + timedelta(days=20))
        assert len(L.record_calls(oil_graph(), D0 + timedelta(days=21))) >= 1

    def test_corrupt_calls_file_is_backed_up_not_overwritten(self):
        import learning_ledger as L, learning_io
        os.makedirs(learning_io.LEARN_DIR)
        _write(L.CALLS_FILE, "{not json")
        with self.capture():
            assert L.record_calls(oil_graph(), D0) == []
        assert _read(L.CALLS_FILE) == "{not json"
        assert [n for n in os.listdir(learning_io.LEARN_DIR) if ".corrupt-" in n]


if __name__ == "__main__":
    unittest.main()
