"""digest.py wiring of the self-learning loop (lctx, learning_cycle) plus end-to-end scenarios
in a temp directory: record -> grade -> scorecard -> learn (fake LLM) -> prompt context -> auto-revert,
with a check that nothing outside _data/_learning (and no .py file) is touched."""
import glob
import hashlib
import json
import os
import pathlib
import time
import unittest
from datetime import datetime, timedelta

import _stubs  # noqa: F401
from _learn_support import TmpCwdCase, tree_snapshot
import digest
import learning_io
import learning_ledger as L
import learning_memory as M
import market_history

D0 = datetime(2026, 5, 1, 7, 0)
LEARN = learning_io.LEARN_DIR


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _jload(p):
    return json.loads(_read(p))


def fixture_graph():
    with open(_stubs.fixture("digest_sample.json"), encoding="utf-8") as f:
        g = json.load(f)["intelligence"]["graph"]
    digest.compute_impact(g)
    return g


def py_hashes():
    out = {}
    for pat in ("*.py", os.path.join("tests", "*.py")):
        for p in glob.glob(os.path.join(_stubs.ROOT, pat)):
            with open(p, "rb") as f:
                out[p] = hashlib.sha1(f.read()).hexdigest()
    return out


class FakeLLM:
    """Stands in for the llm module: available, and answers the LEARN call with valid JSON."""

    def __init__(self, available=True, out=None, boom=False):
        self._available, self.boom, self.calls = available, boom, 0
        self.HARD_STOP = time.time() + 10_000   # plenty of time left unless a test says otherwise
        self.out = out

    def available(self):
        if self.boom:
            raise RuntimeError("availability exploded")
        return self._available

    def generate_json(self, *a, **k):
        self.calls += 1
        if self.boom:
            raise RuntimeError("llm exploded")
        if self.out is not None:
            return self.out
        sc = L.track_record()
        n, hr = int(sc["n_graded"]), round(float(sc["hit_rate"] or 0) * 100)
        return {
            "lessons": "# Rules\n- Oil moves were called right often enough (n=%d, hit %d%%)\n"
                       "- Link types with few checks stay unknown for now (unknown)\n"
                       "- Made up rule with a fake count (n=99999)" % (n, hr),
            "playbooks": [{"slug": "oil-to-rupee", "title": "Oil to rupee",
                           "body": "- Applies when crude jumps and the rupee weakens (n=%d, hit %d%%)" % (n, hr)}],
            "changelog": "added oil playbook",
        }


class LctxTests(TmpCwdCase):
    def test_empty_without_data(self):
        for kind in ("rank", "brief", "graph", "cards", "bogus"):
            self.assertEqual(digest.lctx(kind), "")

    def test_never_raises_when_memory_raises(self):
        def boom(kind):
            raise RuntimeError("memory exploded")
        self.mp.setattr(digest.learning_memory, "prompt_context", boom)
        with self.capture():
            self.assertEqual(digest.lctx("rank"), "")

    def test_wraps_text_with_blank_line_prefix(self):
        self.mp.setattr(digest.learning_memory, "prompt_context", lambda k: "HELLO\n")
        self.assertEqual(digest.lctx("rank"), "\n\nHELLO\n")

    def test_none_result_is_safe(self):
        self.mp.setattr(digest.learning_memory, "prompt_context", lambda k: None)
        self.assertEqual(digest.lctx("rank"), "")


class LearningCycleTests(TmpCwdCase):
    def setUp(self):
        super().setUp()
        self.mp.setattr(market_history, "HISTORY_FILE", pathlib.Path(self.tmp) / "_data" / "market_history.json")
        self.mp.setattr(digest, "NOW", D0 + timedelta(days=20))

    def test_no_llm_available_does_not_raise_or_learn(self):
        fake = FakeLLM(available=False)
        self.mp.setattr(digest, "llm", fake)
        with self.capture():
            digest.learning_cycle(llm_step=True)
        self.assertEqual(fake.calls, 0)
        self.assertFalse(os.path.exists(os.path.join(LEARN, "lessons.md")))

    def test_llm_module_raising_does_not_propagate(self):
        self.mp.setattr(digest, "llm", FakeLLM(boom=True))
        with self.capture():
            digest.learning_cycle(llm_step=True)

    def test_ledger_raising_does_not_propagate(self):
        def boom(*a):
            raise RuntimeError("ledger exploded")
        self.mp.setattr(digest.learning_ledger, "grade_calls", boom)
        self.mp.setattr(digest, "llm", FakeLLM())
        with self.capture():
            digest.learning_cycle(llm_step=True)

    def test_memory_raising_does_not_propagate(self):
        def boom(*a):
            raise RuntimeError("memory exploded")
        self.mp.setattr(digest.learning_memory, "run_daily", boom)
        self.mp.setattr(digest, "llm", FakeLLM())
        with self.capture():
            digest.learning_cycle(llm_step=True)

    def test_grading_only_call_never_uses_the_llm(self):
        fake = FakeLLM()
        self.mp.setattr(digest, "llm", fake)
        with self.capture():
            digest.learning_cycle()
        self.assertEqual(fake.calls, 0)

    def test_learning_step_skipped_when_time_is_short(self):
        fake = FakeLLM()
        fake.HARD_STOP = time.time() + 100
        self.mp.setattr(digest, "llm", fake)
        with self.capture():
            digest.learning_cycle(llm_step=True)
        self.assertEqual(fake.calls, 0)

    def test_empty_directory_cycle_is_harmless(self):
        self.mp.setattr(digest, "llm", FakeLLM())
        with self.capture():
            digest.learning_cycle(llm_step=True)
        self.assertFalse(os.path.exists(os.path.join(LEARN, "lessons.md")))


class EndToEnd(TmpCwdCase):
    def setUp(self):
        super().setUp()
        self.mp.setattr(market_history, "HISTORY_FILE", pathlib.Path(self.tmp) / "_data" / "market_history.json")
        self.graph = fixture_graph()
        self._write_history()

    def _write_history(self):
        base = {r["label"]: float(str(r["price"]).replace(",", "")) for r in self.graph["market"]}
        base["US Dollar Index"] = 100.0
        rows = []
        for k in range(-5, 31):
            d = (D0 + timedelta(days=k)).strftime("%Y-%m-%d")
            rows.append({"date": d, "labels": {lab: round(p * (1 + 0.01 * k), 4) for lab, p in base.items()},
                         "change_pct": {}})
        market_history.HISTORY_FILE.write_text(json.dumps(rows), encoding="utf-8")

    def _record(self):
        """Record the graph's calls on day 0, then write the hand-made 'did it happen?' answers
        (_data/predictions.json, a pipeline input) for the open prediction calls so they can be graded."""
        L.record_calls(self.graph, D0)
        L.record_scenarios([{"if": "Fed cuts rates sharply", "then": "dollar index falls", "likelihood": "low"}], D0)
        soft = [c for c in _jload(L.CALLS_FILE)["calls"] if not c.get("instrument")]
        preds = []
        for i, c in enumerate(soft):
            preds.append({"id": f"p{i}", "if": c.get("if", ""), "then": c.get("then", ""),
                          "status": "did_not_happen" if i % 3 == 2 else "happened", "evidence": "checked"})
        self.assertGreaterEqual(len(preds), 20)
        with open(os.path.join("_data", "predictions.json"), "w", encoding="utf-8") as f:
            json.dump({"predictions": preds}, f)

    def _cycle(self, days):
        self.mp.setattr(digest, "NOW", D0 + timedelta(days=days))
        with self.capture():
            digest.learning_cycle(llm_step=True)

    def test_full_cycle_stays_inside_learning_folder(self):
        before = tree_snapshot(".")
        py_before = py_hashes()
        fake = FakeLLM()
        self.mp.setattr(digest, "llm", fake)

        self._record()
        preds_file = os.path.join("_data", "predictions.json")
        before[preds_file] = tree_snapshot(".")[preds_file]   # an input made by this test, must stay untouched
        calls = _jload(L.CALLS_FILE)["calls"]
        self.assertGreaterEqual(len(calls), 36)
        self.assertTrue(all(c["status"] == "open" for c in calls))

        # nothing is due yet, so nothing is graded and the model is not asked
        self._cycle(1)
        self.assertFalse(L.track_record()["enough_data"])
        self.assertEqual(fake.calls, 0)

        self._cycle(25)
        sc = L.scorecard(D0 + timedelta(days=25))
        self.assertTrue(sc["enough_data"], sc)
        self.assertGreaterEqual(sc["n_graded"], 20)
        self.assertEqual(fake.calls, 1)

        for name in ("lessons.md", "weights.json", "changelog.md", "scorecard.json",
                     os.path.join("playbooks", "oil-to-rupee.md")):
            self.assertTrue(os.path.exists(os.path.join(LEARN, name)), name)
        lessons = _read(os.path.join(LEARN, "lessons.md"))
        self.assertRegex(lessons, r"\(n=\d+, hit \d+%\)")
        self.assertNotIn("99999", lessons)       # ungrounded bullet rejected
        self.assertIn("added oil playbook", _read(os.path.join(LEARN, "changelog.md")))
        self.assertIn("confidence_scale", _jload(os.path.join(LEARN, "weights.json")))

        # even if an address got into a lessons file by some other route, it never reaches a prompt
        with open(os.path.join(LEARN, "lessons.md"), "a", encoding="utf-8") as f:
            f.write("- Mail leak@example.com about oil (unknown)" + chr(10))
        for kind, cap in M.CONTEXT_CAPS.items():
            ctx = digest.lctx(kind)
            self.assertTrue(ctx.startswith("\n\n" + M.NOTES_OPEN), kind)
            self.assertIn("WHAT WE HAVE LEARNED", ctx)
            self.assertIn(M.NOTES_CLOSE, ctx)
            self.assertLessEqual(len(ctx), cap + 2, kind)
            self.assertNotIn("@", ctx)
            self.assertNotIn("leak", ctx)

        after = tree_snapshot(".")
        touched = {p for p in after if before.get(p) != after[p]} | (set(before) - set(after))
        self.assertTrue(touched)
        prefix = os.path.join("_data", "_learning") + os.sep
        outside = sorted(p for p in touched if not p.startswith(prefix))
        self.assertEqual(outside, [], outside)
        for p in touched:
            self.assertIn(os.path.splitext(p)[1], (".json", ".md", ".txt"), p)
        self.assertEqual(py_before, py_hashes())
        self.assertFalse(glob.glob("**/*.py", recursive=True))
        hist = os.path.join("_data", "market_history.json")
        self.assertEqual(before[hist], after[hist])

    def test_induced_accuracy_drop_auto_reverts(self):
        self.mp.setattr(digest, "llm", FakeLLM())
        self._record()
        self._cycle(25)
        lessons_path = os.path.join(LEARN, "lessons.md")
        pb_path = os.path.join(LEARN, "playbooks", "oil-to-rupee.md")
        self.assertTrue(_read(lessons_path).strip())
        self.assertTrue(os.path.exists(pb_path))

        
        store = _jload(L.CALLS_FILE)
        for i in range(25):      # comparable earlier calls, mostly right
            store["calls"].append({"id": f"good{i}", "made": "2026-05-02", "kind": "market", "subject": "X",
                                   "text": "", "direction": "up", "confidence": "high", "topic": "oil",
                                   "link_type": "a->b", "instrument": "X", "due": "2026-05-05", "status": "graded",
                                   "grade": {"result": "right", "soft": False, "at": "2026-05-06",
                                             "baseline_right": True}})
        for i in range(45):      # newer calls, every one wrong
            store["calls"].append({"id": f"bad{i}", "made": "2026-05-28", "kind": "market", "subject": "X",
                                   "text": "", "direction": "up", "confidence": "high", "topic": "oil",
                                   "link_type": "a->b", "instrument": "X", "due": "2026-05-29",
                                   "status": "graded",
                                   "grade": {"result": "wrong", "soft": False, "at": "2026-05-30",
                                             "baseline_right": False}})
        learning_io.safe_write_json(L.CALLS_FILE, store)

        self._cycle(30)
        self.assertIn("AUTO-REVERT", _read(os.path.join(LEARN, "changelog.md")))
        self.assertFalse(_read(lessons_path).strip())
        self.assertFalse(os.path.exists(pb_path))

    def test_memory_understands_ledger_recent_graded_shape(self):
        store = {"calls": [{"id": "g1", "made": "2026-05-01", "kind": "market", "subject": "X", "status": "graded",
                            "due": "2026-05-04", "grade": {"result": "right", "soft": False, "at": "2026-05-05"}}]}
        learning_io.safe_write_json(L.CALLS_FILE, store)
        got = M._graded(10)
        self.assertEqual(len(got), 1)
        self.assertEqual(M.hard_stats(got), {"market": [1, 1.0]})

    def test_stable_accuracy_does_not_revert(self):
        self.mp.setattr(digest, "llm", FakeLLM())
        self._record()
        self._cycle(25)
        self._cycle(26)
        self.assertNotIn("AUTO-REVERT", _read(os.path.join(LEARN, "changelog.md")))
        self.assertTrue(_read(os.path.join(LEARN, "lessons.md")).strip())

    def test_telegram_line_from_real_track_record(self):
        import track_page
        self.assertIn("building", track_page.telegram_line(L.track_record()))
        self.mp.setattr(digest, "llm", FakeLLM())
        self._record()
        self._cycle(25)
        line = track_page.telegram_line(L.track_record())
        self.assertRegex(line, r"^Track record: \d+% of \d+ checked calls right")


if __name__ == "__main__":
    unittest.main()
