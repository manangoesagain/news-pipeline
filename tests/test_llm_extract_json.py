import json
import unittest

import _stubs  # noqa: F401  (must come first)
import llm

ex = llm._extract_json
GOOD = {"graph": {"nodes": [{"id": "n1", "label": "A"}, {"id": "n2", "label": "B"}], "edges": []}}


class ExtractJsonTests(unittest.TestCase):
    def test_valid_json(self):
        self.assertEqual(ex(json.dumps(GOOD), "graph"), GOOD)

    def test_code_fenced(self):
        self.assertEqual(ex("```json\n" + json.dumps(GOOD) + "\n```", "graph"), GOOD)
        self.assertEqual(ex("```\n" + json.dumps(GOOD) + "\n```", "graph"), GOOD)

    def test_reasoning_text_before_json(self):
        txt = "Let me think. The user wants {a graph}. Here it is:\n" + json.dumps(GOOD) + "\nHope that helps"
        self.assertEqual(ex(txt, "graph"), GOOD)

    def test_think_block_removed(self):
        txt = '<think>{"graph": "decoy"}</think>' + json.dumps(GOOD)
        self.assertEqual(ex(txt, "graph"), GOOD)

    def test_trailing_commas(self):
        txt = '{"graph": {"nodes": [{"id": "n1",}, {"id": "n2"},], "edges": [],},}'
        r = ex(txt, "graph")
        self.assertIsNotNone(r)
        self.assertEqual(len(r["graph"]["nodes"]), 2)

    def test_stray_closer_slip(self):
        txt = '{"graph": {"nodes": [{"id": "n1"}, {"id": "n2"}]], "edges": [{"from": "n1", "to": "n2"}]}}'
        r = ex(txt, "graph")
        self.assertIsNotNone(r)
        self.assertEqual(len(r["graph"]["nodes"]), 2)
        self.assertEqual(len(r["graph"]["edges"]), 1)

    def test_real_saved_failure_reply(self):
        with open(_stubs.fixture("llm_fail_graph.txt"), encoding="utf-8") as f:
            r = ex(f.read(), "graph")
        self.assertIsNotNone(r, "real saved reply (stray closers) must parse")
        g = r["graph"]
        self.assertGreater(len(g["nodes"]), 10)
        self.assertGreater(len(g["edges"]), 10)
        # recovered edges must reference the last nodes too, i.e. nothing was truncated away
        self.assertIn("n22", {e["from"] for e in g["edges"]} | {e["to"] for e in g["edges"]})

    def test_truncated_mid_string(self):
        txt = '{"graph": {"nodes": [{"id": "n1", "label": "A"}, {"id": "n2", "label": "B"}, {"id": "n3", "label": "cut off he'
        r = ex(txt, "graph")
        self.assertIsNotNone(r)
        self.assertGreaterEqual(len(r["graph"]["nodes"]), 2)
        self.assertEqual(r["graph"]["nodes"][0]["id"], "n1")

    def test_truncated_mid_array(self):
        txt = '{"graph": {"nodes": [{"id": "n1"}, {"id": "n2"}, '
        r = ex(txt, "graph")
        self.assertIsNotNone(r)
        self.assertEqual([n["id"] for n in r["graph"]["nodes"]], ["n1", "n2"])

    def test_truncated_without_any_complete_value_is_none(self):
        self.assertIsNone(ex('{"graph": {"nodes": [{"id": "n1', "graph"))

    def test_curly_quotes_inside_strings_survive(self):
        d = {"graph": {"nodes": [], "note": "He said “hello” and ‘bye’ — fine"}}
        r = ex(json.dumps(d, ensure_ascii=False), "graph")
        self.assertEqual(r, d)
        r2 = ex("Sure!\n```json\n" + json.dumps(d, ensure_ascii=False) + "\n```", "graph")
        self.assertEqual(r2, d)

    def test_escaped_quotes_and_braces_in_strings(self):
        d = {"graph": {"t": 'a "quoted" } ] brace', "u": "x"}}
        self.assertEqual(ex(json.dumps(d), "graph"), d)

    def test_missing_require_returns_none(self):
        self.assertIsNone(ex(json.dumps({"other": 1}), "graph"))
        self.assertIsNone(ex("```json\n" + json.dumps({"other": 1}) + "\n```", "graph"))
        self.assertIsNone(ex('{"other": [1, 2', "graph"))

    def test_require_optional(self):
        self.assertEqual(ex('{"a": 1}'), {"a": 1})

    def test_garbage_returns_none(self):
        self.assertIsNone(ex("no json here", "graph"))
        self.assertIsNone(ex("", "graph"))

    def test_picks_object_with_required_key(self):
        txt = '{"x": 1} then {"graph": {"nodes": []}}'
        self.assertEqual(ex(txt, "graph"), {"graph": {"nodes": []}})


if __name__ == "__main__":
    unittest.main()
