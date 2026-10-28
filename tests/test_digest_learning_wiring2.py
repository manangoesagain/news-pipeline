"""digest.py wiring of the second learning round: the optional Jev link filter and soft grader, the
learning_cycle step order and its never-raise guarantee, per-batch experiment arms on focus cards, and the
delivery-time recording block. Collaborators (jev_judge, learning_*, llm) are replaced by small fakes."""
import ast
import contextlib
import inspect
import io
import os
import time
import types
import unittest
from datetime import datetime, timedelta

import _stubs  # noqa: F401
from _learn_support import MonkeyPatch
import digest
import jev_judge

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=digest.ET)


class Quiet(unittest.TestCase):
    def setUp(self):
        self.mp = MonkeyPatch()
        self.mp.setattr(digest, "NOW", NOW)
        self.mp.setitem(os.environ, "LEARNING_ENABLED", "")
        self.out = io.StringIO()
        self._cm = contextlib.redirect_stdout(self.out)
        self._cm.__enter__()

    def tearDown(self):
        self._cm.__exit__(None, None, None)
        self.mp.undo()


def ns(**kw):
    return types.SimpleNamespace(**kw)


def edge(a, b, **kw):
    d = {"from": f"n{a}", "to": f"n{b}", "label": "raises", "effect": "up", "strength": 2, "confidence": "medium",
         "status": "reported", "kind": "causal"}
    d.update(kw)
    return d


def graph(n_nodes=8, n_edges=None):
    nodes = [{"id": f"n{i}", "label": f"Node {i}", "type": "event", "domain": "AI & ML", "detail": f"Detail {i}"}
             for i in range(n_nodes)]
    edges = [edge(i, (i + 1) % n_nodes) for i in range(n_edges if n_edges is not None else n_nodes)]
    return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------------------- jev_filter_links

class FakeJev:
    def __init__(self, available=True, probs=None, dropped=None, raises=False, keep_edges=None):
        self._available, self.probs, self.raises = available, probs, raises
        self.dropped = dropped if dropped is not None else []
        self.keep_edges = keep_edges
        self.phases, self.verify_calls, self.drop_calls = [], 0, 0

    def available(self):
        return self._available

    def begin_phase(self, *a):
        self.phases.append(a)

    def verify_links(self, g, *a, **k):
        self.verify_calls += 1
        if self.raises:
            raise RuntimeError("jev exploded")
        return self.probs if self.probs is not None else {}

    def drop_unsupported(self, g, probs, *a, **k):
        self.drop_calls += 1
        n = len(g["edges"]) if self.keep_edges is None else self.keep_edges
        g2 = dict(g)
        g2["edges"] = g["edges"][:n]
        return g2, self.dropped


class JevFilterLinks(Quiet):
    def setUp(self):
        super().setUp()
        self.mp.setattr(digest, "llm", ns(HARD_STOP=time.time() + 5000))

    def use(self, fake):
        self.mp.setattr(digest, "jev_judge", fake)
        return fake

    def test_not_available_returns_the_same_graph_untouched(self):
        fake = self.use(FakeJev(available=False))
        g = graph()
        self.assertIs(digest.jev_filter_links(g), g)
        self.assertEqual((fake.verify_calls, fake.phases), (0, []))

    def test_empty_or_missing_graph_passes_through(self):
        fake = self.use(FakeJev())
        for g in (None, {}, {"nodes": [], "edges": []}):
            self.assertEqual(digest.jev_filter_links(g), g)
        self.assertEqual(fake.verify_calls, 0)

    def test_skipped_when_time_is_short(self):
        self.mp.setattr(digest, "llm", ns(HARD_STOP=time.time() + 100))
        fake = self.use(FakeJev(probs={0: 0.0}))
        g = graph()
        self.assertIs(digest.jev_filter_links(g), g)
        self.assertEqual(fake.verify_calls, 0)

    def test_drops_unsupported_edges_and_uses_the_links_phase(self):
        fake = self.use(FakeJev(probs={0: 0.0, 1: 0.9}, dropped=[{"index": 0}], keep_edges=9))
        g = graph(8, 10)
        out = digest.jev_filter_links(g)
        self.assertEqual(len(out["edges"]), 9)
        self.assertEqual(len(g["edges"]), 10)                    # the caller's graph is not modified in place
        self.assertEqual(fake.phases, [("links", 60, 40)])
        self.assertEqual((fake.verify_calls, fake.drop_calls), (1, 1))

    def test_no_answers_keeps_the_graph(self):
        fake = self.use(FakeJev(probs={}))
        g = graph()
        self.assertIs(digest.jev_filter_links(g), g)
        self.assertEqual(fake.drop_calls, 0)

    def test_result_with_too_few_edges_is_refused(self):
        self.use(FakeJev(probs={0: 0.0}, dropped=[{"index": 0}], keep_edges=8))
        g = graph(8, 10)
        self.assertEqual(len(digest.jev_filter_links(g)["edges"]), 8)       # exactly 8 is still accepted
        self.use(FakeJev(probs={0: 0.0}, dropped=[{"index": 0}], keep_edges=7))
        self.assertIs(digest.jev_filter_links(g), g)             # would leave under 8 edges: keep the original

    def test_judge_failure_never_raises_and_keeps_the_graph(self):
        self.use(FakeJev(raises=True))
        g = graph()
        self.assertIs(digest.jev_filter_links(g), g)
        self.assertIn("[JEV] link check failed", self.out.getvalue())
        self.mp.setattr(digest, "jev_judge", ns(available=lambda: 1 / 0))
        self.assertIs(digest.jev_filter_links(g), g)

    def test_real_module_without_a_key_is_inert(self):
        self.mp.setitem(os.environ, "TYPESAFE_API_KEY", "")
        self.mp.setitem(os.environ, "JEV_API_KEY", "")
        self.mp.setattr(digest, "jev_judge", jev_judge)
        g = graph()
        self.assertIs(digest.jev_filter_links(g), g)


# ------------------------------------------------------------------------- jev_grade_soft_calls

class FakeLedger:
    def __init__(self, calls=None, raises=False):
        self.calls, self.raises = calls or [], raises
        self.applied, self.asked = [], []

    def open_soft_calls(self, min_age, limit, now):
        self.asked.append((min_age, limit))
        if self.raises:
            raise RuntimeError("ledger exploded")
        return list(self.calls)

    def apply_external_soft_grades(self, grades, now, source):
        self.applied.append((dict(grades), source))
        return list(grades)


class SoftGrading(Quiet):
    def setUp(self):
        super().setUp()
        self.mp.setattr(digest, "_recent_headlines", lambda *a, **k: [f"Headline {i} about things" for i in range(20)])

    def soft_calls(self, n):
        return [{"id": f"s{i}", "if": f"crude oil jumps {i}", "then": f"rupee weakens {i}"} for i in range(n)]

    def jev(self, answer=0.9, available=True, boom=False):
        seen = []

        def grade_soft(preds, picked):
            seen.append((list(preds), list(picked)))
            if boom:
                raise RuntimeError("jev down")
            return {p["id"]: answer for p in preds}
        j = ns(available=lambda: available, begin_phase=lambda *a: seen.append(("phase", a)),
               grade_soft=grade_soft, classify=jev_judge.classify)
        j.seen = seen
        self.mp.setattr(digest, "jev_judge", j)
        return j

    def test_grades_are_classified_and_applied_with_source_jev(self):
        led = FakeLedger(self.soft_calls(12))
        self.mp.setattr(digest, "learning_ledger", led)
        j = self.jev(0.9)
        digest.jev_grade_soft_calls()
        self.assertEqual(len([s for s in j.seen if s[0] != "phase"]), 3)       # chunks of 5, 5, 2
        self.assertIn(("phase", ("soft", 30, 15)), j.seen)
        self.assertEqual(len(led.applied), 1)
        grades, source = led.applied[0]
        self.assertEqual(source, "jev")
        self.assertEqual(set(grades), {f"s{i}" for i in range(12)})
        self.assertEqual(set(grades.values()), {"right"})
        self.assertEqual(led.asked, [(2, 30)])

    def test_probabilities_map_to_right_wrong_unclear(self):
        led = FakeLedger(self.soft_calls(3))
        self.mp.setattr(digest, "learning_ledger", led)
        j = self.jev()
        probs = {"s0": 0.95, "s1": 0.05, "s2": 0.5}
        j.grade_soft = lambda preds, picked: probs
        digest.jev_grade_soft_calls()
        self.assertEqual(led.applied[0][0], {"s0": "right", "s1": "wrong", "s2": "unclear"})

    def test_headlines_are_ranked_by_overlap_and_capped(self):
        heads = ["Stocks drift lower", "Crude oil jumps as rupee weakens sharply", "Unrelated sports news"] + \
                [f"Filler {i}" for i in range(30)]
        self.mp.setattr(digest, "_recent_headlines", lambda *a, **k: heads)
        self.mp.setattr(digest, "learning_ledger", FakeLedger(self.soft_calls(1)))
        j = self.jev()
        digest.jev_grade_soft_calls()
        picked = [s for s in j.seen if s[0] != "phase"][0][1]
        self.assertLessEqual(len(picked), 12)
        self.assertEqual(picked[0], "Crude oil jumps as rupee weakens sharply")

    def test_inert_without_a_key_or_when_learning_is_off_or_nothing_to_grade(self):
        led = FakeLedger(self.soft_calls(3))
        self.mp.setattr(digest, "learning_ledger", led)
        j = self.jev(available=False)
        digest.jev_grade_soft_calls()
        self.assertEqual((led.asked, led.applied, j.seen), ([], [], []))
        j = self.jev()
        self.mp.setitem(os.environ, "LEARNING_ENABLED", "off")
        digest.jev_grade_soft_calls()
        self.assertEqual((led.asked, led.applied), ([], []))
        self.mp.setitem(os.environ, "LEARNING_ENABLED", "")
        self.mp.setattr(digest, "_recent_headlines", lambda *a, **k: [])
        digest.jev_grade_soft_calls()
        self.assertEqual(led.applied, [])
        self.mp.setattr(digest, "_recent_headlines", lambda *a, **k: ["a headline"])
        self.mp.setattr(digest, "learning_ledger", FakeLedger([]))
        digest.jev_grade_soft_calls()
        self.assertEqual([s for s in j.seen if s[0] != "phase"], [])

    def test_no_grades_means_nothing_applied(self):
        led = FakeLedger(self.soft_calls(2))
        self.mp.setattr(digest, "learning_ledger", led)
        j = self.jev()
        j.grade_soft = lambda preds, picked: {}
        digest.jev_grade_soft_calls()
        self.assertEqual(led.applied, [])

    def test_never_raises(self):
        self.mp.setattr(digest, "learning_ledger", FakeLedger(self.soft_calls(3), raises=True))
        self.jev()
        digest.jev_grade_soft_calls()
        led = FakeLedger(self.soft_calls(3))
        self.mp.setattr(digest, "learning_ledger", led)
        self.jev(boom=True)
        digest.jev_grade_soft_calls()
        self.assertEqual(led.applied, [])
        self.assertIn("[JEV] soft grading failed", self.out.getvalue())
        self.mp.setattr(digest, "learning_ledger", FakeLedger([{"id": "x"}, None, 5]))
        self.jev()
        digest.jev_grade_soft_calls()


# ------------------------------------------------------------------------- learning_cycle

class CycleFakes:
    """Records the order of calls made by learning_cycle."""

    def __init__(self, priors_present=False, llm_available=True, hard_stop_in=5000, boom=None):
        self.log, self.boom = [], boom or ()
        self.priors_present = priors_present

        def step(name, ret=None):
            def f(*a, **k):
                self.log.append(name)
                if name in self.boom:
                    raise RuntimeError(name + " exploded")
                return ret
            return f
        self.priors = ns(load_priors=lambda: ({"x": 1} if self.priors_present else {}),
                         refresh_priors=step("refresh_priors", True))
        self.ledger = ns(grade_calls=step("grade_calls"), scorecard=step("scorecard"))
        self.memory = ns(run_daily=step("run_daily", {"learned": True}))
        self.llm = ns(available=lambda: llm_available, HARD_STOP=time.time() + hard_stop_in)
        self.jev = ns(begin_phase=step("begin_phase"))
        self.soft = step("jev_grade_soft_calls")

    def install(self, mp):
        mp.setattr(digest, "learning_priors", self.priors)
        mp.setattr(digest, "learning_ledger", self.ledger)
        mp.setattr(digest, "learning_memory", self.memory)
        mp.setattr(digest, "llm", self.llm)
        mp.setattr(digest, "jev_judge", self.jev)
        mp.setattr(digest, "jev_grade_soft_calls", self.soft)
        return self


class LearningCycle(Quiet):
    def test_before_delivery_fetches_priors_only_when_missing_then_grades(self):
        f = CycleFakes(priors_present=False).install(self.mp)
        digest.learning_cycle(llm_step=False)
        self.assertEqual(f.log, ["refresh_priors", "grade_calls", "scorecard"])
        f = CycleFakes(priors_present=True).install(self.mp)
        digest.learning_cycle(llm_step=False)
        self.assertEqual(f.log, ["grade_calls", "scorecard"])

    def test_after_delivery_full_sequence(self):
        f = CycleFakes(priors_present=True).install(self.mp)
        digest.learning_cycle(llm_step=True)
        self.assertEqual(f.log, ["grade_calls", "scorecard", "refresh_priors", "jev_grade_soft_calls",
                                 "begin_phase", "run_daily", "grade_calls", "scorecard"])

    def test_step_skipped_when_time_is_short(self):
        f = CycleFakes(priors_present=True, hard_stop_in=100).install(self.mp)
        digest.learning_cycle(llm_step=True)
        self.assertEqual(f.log, ["grade_calls", "scorecard"])
        self.assertIn("not enough time left", self.out.getvalue())

    def test_no_llm_means_no_lesson_step(self):
        f = CycleFakes(priors_present=True, llm_available=False).install(self.mp)
        digest.learning_cycle(llm_step=True)
        self.assertNotIn("run_daily", f.log)
        self.assertIn("refresh_priors", f.log)
        self.assertEqual(f.log[-2:], ["grade_calls", "scorecard"])

    def test_every_collaborator_can_raise_without_escaping(self):
        for name in ("refresh_priors", "grade_calls", "scorecard", "run_daily", "begin_phase",
                     "jev_grade_soft_calls"):
            for step in (False, True):
                with self.subTest(name=name, llm_step=step):
                    f = CycleFakes(priors_present=(name != "refresh_priors"), boom=(name,)).install(self.mp)
                    try:
                        digest.learning_cycle(llm_step=step)
                    except Exception as e:                      # pragma: no cover
                        self.fail(f"learning_cycle raised {type(e).__name__}")

    def test_missing_attributes_and_nonsense_modules_do_not_raise(self):
        self.mp.setattr(digest, "learning_priors", None)
        self.mp.setattr(digest, "learning_ledger", object())
        self.mp.setattr(digest, "llm", object())
        digest.learning_cycle(llm_step=True)
        digest.learning_cycle(llm_step=False)

    def test_kill_switch_still_never_raises(self):
        self.mp.setitem(os.environ, "LEARNING_ENABLED", "off")
        f = CycleFakes(priors_present=True).install(self.mp)
        digest.learning_cycle(llm_step=True)

    def test_lctx_wraps_and_survives_a_kill_switched_memory(self):
        self.mp.setitem(os.environ, "LEARNING_ENABLED", "off")
        self.assertIsInstance(digest.lctx("cards"), str)


# ------------------------------------------------------------------------- focus-card arms

class FocusCardArms(Quiet):
    def setUp(self):
        super().setUp()
        self.prompts = []

        def generate_json(prompt, **kw):
            self.prompts.append((prompt, kw))
            return None
        self.llm = ns(generate_json=generate_json, HARD_STOP=time.time() - 10, available=lambda: True)
        self.mp.setattr(digest, "llm", self.llm)
        self.keys = []

    def memory(self, arm_for=None, hint="Use plain words and short sentences.", boom_arm=False, boom_hint=False):
        def assign_arm(key, kind):
            self.keys.append((key, kind))
            if boom_arm:
                raise RuntimeError("arm exploded")
            return arm_for(key) if arm_for else "exp-20261027-ab12"

        def variant_text(kind, arm):
            if boom_hint:
                raise RuntimeError("hint exploded")
            return hint if arm != "control" else ""
        self.mp.setattr(digest, "learning_memory", ns(assign_arm=assign_arm, variant_text=variant_text,
                                                      prompt_context=lambda k: ""))

    def test_every_node_of_a_batch_carries_the_arm_and_the_prompt_the_hint(self):
        self.memory()
        g = digest.add_focus_cards(graph(4), [])
        self.assertTrue(all(n.get("arm") == "exp-20261027-ab12" for n in g["nodes"]))
        self.assertTrue(self.prompts)
        self.assertTrue(all("Style hint: Use plain words and short sentences." in p for p, _ in self.prompts))
        self.assertTrue(all(k == "cards" for _, k in self.keys))

    def test_control_arm_has_no_hint(self):
        self.memory(arm_for=lambda key: "control")
        g = digest.add_focus_cards(graph(4), [])
        self.assertTrue(all(n.get("arm") == "control" for n in g["nodes"]))
        self.assertTrue(all("Style hint" not in p for p, _ in self.prompts))

    def test_arm_is_chosen_per_batch_from_the_date_and_the_batch_ids(self):
        self.memory(arm_for=lambda key: "exp-20261027-ab12" if "n0" in key.split("|") else "control")
        g = digest.add_focus_cards(graph(9), [])
        batches = {}
        for n in g["nodes"]:
            batches.setdefault(n["arm"], []).append(n["id"])
        self.assertEqual(set(batches), {"exp-20261027-ab12", "control"})
        self.assertEqual(len(self.keys), 2)                          # 9 nodes -> batches of 5 and 4
        for key, _ in self.keys:
            self.assertTrue(key.startswith(f"{NOW:%Y%m%d%H}|"), key)  # a new day gives a new draw
        keyed = sorted(k.split("|")[1:] for k, _ in self.keys)
        self.assertEqual(sorted(len(b) for b in keyed), [4, 5])
        arm_of = {n["id"]: n["arm"] for n in g["nodes"]}
        for ids in keyed:                                            # all nodes of one batch share one arm
            self.assertEqual(len({arm_of[i] for i in ids}), 1)

    def test_a_failing_memory_degrades_to_control_and_cards_still_run(self):
        for kw in ({"boom_arm": True}, {"boom_hint": True}):
            with self.subTest(kw=kw):
                self.prompts.clear()
                self.memory(**kw)
                g = digest.add_focus_cards(graph(4), [])
                self.assertTrue(self.prompts)
                self.assertTrue(all("Style hint" not in p for p, _ in self.prompts))

    def test_failing_arm_assignment_tags_control(self):
        self.memory(boom_arm=True)
        g = digest.add_focus_cards(graph(4), [])
        self.assertTrue(all(n.get("arm", "control") == "control" for n in g["nodes"]))

    def test_BUG_hint_lookup_failure_must_not_tag_the_variant_arm(self):
        # REAL BUG (digest.add_focus_cards): assign_arm succeeds, variant_text raises -> hint is '' (a control
        # prompt) but the nodes are still tagged with the variant arm, which pollutes the experiment's counts.
        self.memory(boom_hint=True)
        g = digest.add_focus_cards(graph(4), [])
        self.assertTrue(all(n.get("arm", "control") == "control" for n in g["nodes"]))

    def test_never_raises_with_a_broken_llm_or_graph(self):
        self.memory()
        self.mp.setattr(digest, "llm", ns(generate_json=lambda *a, **k: 1 / 0, HARD_STOP=time.time() - 10))
        g = digest.add_focus_cards(graph(4), [])
        self.assertIsInstance(g, dict)
        self.assertEqual(digest.add_focus_cards({"nodes": [], "edges": []}, []), {"nodes": [], "edges": []})
        digest.add_focus_cards({"nodes": None}, None)               # garbage in: no exception out

    def test_arm_is_a_plain_string_the_ledger_can_store(self):
        self.memory()
        g = digest.add_focus_cards(graph(3), [])
        for n in g["nodes"]:
            self.assertIsInstance(n["arm"], str)
            self.assertLessEqual(len(n["arm"]), 64)


# ------------------------------------------------------------------------- delivery-time recording block

class RecordingBlock(unittest.TestCase):
    """main() is far too large to run offline, so check the structure that guarantees the promise: every
    learning call at delivery time sits inside a try whose handler swallows Exception."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(inspect.getsource(digest.main).lstrip() if False else
                             "\n".join(l for l in inspect.getsource(digest.main).splitlines()))

    def _enclosing_try(self, attr):
        found = []

        class V(ast.NodeVisitor):
            stack = []

            def visit_Try(self, node):
                self.stack.append(node)
                for child in node.body:
                    self.visit(child)
                self.stack.pop()
                for h in node.handlers:
                    for child in h.body:
                        self.visit(child)

            def visit_Call(self, node):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr == attr:
                    found.append(list(self.stack))
                self.generic_visit(node)
        V.stack = []
        V().visit(self.tree)
        return found

    def _swallows(self, try_node):
        for h in try_node.handlers:
            t = h.type
            broad = t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))
            reraises = any(isinstance(n, ast.Raise) for n in ast.walk(h))
            if broad and not reraises:
                return True
        return False

    def test_learning_calls_in_main_are_guarded(self):
        for attr in ("record_calls", "record_next_day_calls", "record_scenarios", "weekly_note",
                     "telegram_line", "learning_cycle"):
            sites = self._enclosing_try(attr)
            if not sites and attr == "learning_cycle":
                continue                     # learning_cycle guards itself (see LearningCycle tests)
            self.assertTrue(sites, f"{attr} is not called in main()")
            for stack in sites:
                self.assertTrue(any(self._swallows(t) for t in stack), f"{attr} is not inside a guarding try")


class RecordingWithRealLedger(unittest.TestCase):
    def test_recording_graph_with_arms_does_not_raise(self):
        import tempfile
        import shutil
        import learning_ledger as L
        old = os.getcwd()
        tmp = tempfile.mkdtemp(prefix="wiring2_")
        q = contextlib.redirect_stdout(io.StringIO())
        try:
            os.chdir(tmp)
            os.makedirs("_data")
            g = graph(6)
            for n in g["nodes"]:
                n["arm"] = "exp-20261027-ab12"
            g["market"] = []
            L.record_calls(g, NOW)
            L.record_calls(g, NOW, arm="control")
            L.record_next_day_calls(g, [], NOW)
            L.record_scenarios([{"if": "Fed cuts", "then": "dollar falls", "likelihood": "low"}], NOW)
            L.record_calls(None, NOW)
            L.record_calls({"nodes": "x"}, NOW)
            L.scorecard(NOW)
        finally:
            os.chdir(old)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
