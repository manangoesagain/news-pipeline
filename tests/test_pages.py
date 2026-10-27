import copy
import json
import re
import unittest

import _stubs  # noqa: F401
import build_page
import graph_page

HOSTILE = "</script><img src=x onerror=alert(1)>"
SCRIPT_RE = re.compile(r'<script type="application/json" class="cdata">(.*?)</script>', re.S)
# enum / structural keys that must keep their real values so the graph still builds
KEEP = {"id", "from", "to", "type", "effect", "direction", "kind", "status", "confidence", "lag",
        "odds", "horizon", "dir", "trend", "region", "domain"}


def load_digest():
    with open(_stubs.fixture("digest_sample.json"), encoding="utf-8") as f:
        return json.load(f)


def hostile(obj, key=None):
    """Replace every free-text string with a script-breaking payload; links become javascript: URLs."""
    if isinstance(obj, dict):
        return {k: hostile(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [hostile(v, key) for v in obj]
    if isinstance(obj, str):
        if key in KEEP:
            return obj
        if key in ("link", "url"):
            return "javascript:alert(1)"
        return HOSTILE + obj[:20]
    return obj


def payloads(page):
    return [json.loads(m) for m in SCRIPT_RE.findall(page)]


def check_no_breakout(tc, page):
    opens = len(re.findall(r"<script\b", page, re.I))
    closes = len(re.findall(r"</script\s*>", page, re.I))
    tc.assertEqual(opens, closes, "unbalanced <script> tags: a '</script>' payload broke out")
    tc.assertNotIn("<img src=x onerror", page, "raw hostile tag reached the HTML")
    tc.assertIsNone(re.search(r"""href\s*=\s*["']?\s*javascript:""", page, re.I), "href=javascript: in HTML")


class GraphPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = load_digest()
        cls.graph = cls.digest["intelligence"]["graph"]
        cls.html = graph_page.connection_map(copy.deepcopy(cls.graph))

    def test_builds_and_embeds_data(self):
        self.assertTrue(self.html, "connection_map returned '' (swallowed exception) for the real graph")
        self.assertEqual(len(payloads(self.html)), 1)

    def test_every_node_and_link_embedded(self):
        p = payloads(self.html)[0]
        n_expected = min(len(self.graph["nodes"]), graph_page.MAX_NODES)
        self.assertEqual(len(p["nodes"]), n_expected)
        self.assertEqual({n["id"] for n in self.graph["nodes"]}, set(p["nodes"]))
        self.assertEqual(len(p["edges"]), min(len(self.graph["edges"]), graph_page.MAX_EDGES))
        labels = {n["id"]: n["label"] for n in self.graph["nodes"]}
        for nid, jn in p["nodes"].items():
            self.assertEqual(jn["l"], labels[nid])

    def test_every_link_keeps_its_effect_word(self):
        p = payloads(self.html)[0]
        got = {(e["f"], e["t"], e["kind"]): e["effect"] for e in p["edges"]}
        seen = set()
        for e in self.graph["edges"]:
            k = (e["from"], e["to"], e.get("kind", "causal"))
            if k in seen:
                continue
            seen.add(k)
            self.assertIn(k, got)
            self.assertEqual(got[k], e["effect"], f"effect lost on {k}")
        self.assertTrue({"up", "down"} <= set(got.values()))
        self.assertNotIn("none", [v for v in got.values()])

    def test_effect_regression_after_full_page_build(self):
        page = build_page.generate_html([copy.deepcopy(self.digest)])
        p = payloads(page)
        self.assertEqual(len(p), 1)
        wanted = {(e["from"], e["to"]): e["effect"] for e in self.graph["edges"]}
        got = {(e["f"], e["t"]): e["effect"] for e in p[0]["edges"]}
        for k, eff in wanted.items():
            self.assertEqual(got.get(k), eff, f"effect on link {k} wiped by build")

    def test_links_visible_by_default_in_markup(self):
        # the Lines toggle exists, and "All" is the default (links were once hidden by default)
        self.assertRegex(self.html, r'data-lines="all"[^>]*>All')
        self.assertIn('data-lines="sel"', self.html)
        m = re.search(r'role="radio" aria-checked="(true|false)" data-lines="all"', self.html)
        self.assertEqual(m.group(1), "true")

    def test_weak_flag_survives(self):
        g = copy.deepcopy(self.graph)
        g["edges"][0]["weak"] = True
        p = payloads(graph_page.connection_map(g))[0]
        e = next(e for e in p["edges"] if (e["f"], e["t"]) == (g["edges"][0]["from"], g["edges"][0]["to"]))
        self.assertTrue(e.get("weak"))

    def test_malformed_graphs_never_raise(self):
        for bad in (None, {}, {"nodes": "x"}, {"nodes": [None, 3], "edges": [{}]}, [],
                    {"nodes": [{"id": "a"}], "edges": []}):
            self.assertIsInstance(graph_page.connection_map(bad), str)

    def test_hostile_text_cannot_break_out(self):
        h = hostile(copy.deepcopy(self.graph))
        page = graph_page.connection_map(h, note=HOSTILE)
        self.assertTrue(page)
        check_no_breakout(self, page)
        # the payload is still valid JSON and the hostile text is data, not markup
        p = payloads(page)[0]
        self.assertTrue(any("onerror" in n["det"] for n in p["nodes"].values()))

    def test_hostile_ids_cannot_break_out(self):
        h = copy.deepcopy(self.graph)
        ren = {n["id"]: n["id"] + HOSTILE for n in h["nodes"]}
        for n in h["nodes"]:
            n["id"] = ren[n["id"]]
        for e in h["edges"]:
            e["from"], e["to"] = ren.get(e["from"], e["from"]), ren.get(e["to"], e["to"])
        page = graph_page.connection_map(h)
        self.assertTrue(page)
        check_no_breakout(self, page)

    def test_javascript_source_links_dropped(self):
        h = copy.deepcopy(self.graph)
        for n in h["nodes"]:
            n["sources"] = [{"title": "t", "link": "javascript:alert(1)"}, "javascript:alert(2)",
                            {"title": "ok", "link": "https://example.com/a"}]
        p = payloads(graph_page.connection_map(h))[0]
        for jn in p["nodes"].values():
            for title, link in jn["src"]:
                self.assertTrue(link.startswith("http"), link)
            self.assertEqual(len(jn["src"]), 1)


class BuildPageTests(unittest.TestCase):
    def test_full_page_builds(self):
        d = load_digest()
        page = build_page.generate_html([d])
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn("Intelligence Brief", page)
        check_no_breakout(self, page)

    def test_empty_digest_list(self):
        self.assertIn("No digests available", build_page.generate_html([]))

    def test_hostile_digest_page(self):
        d = hostile(load_digest())
        d["datetime"] = "2026-10-22T18:00:00-04:00"
        d["intelligence"]["bottom_line"] = HOSTILE
        d["intelligence"]["scenarios"] = [{"if": HOSTILE, "then": HOSTILE, "likelihood": "high", "signpost": HOSTILE}]
        d["intelligence"]["causal_chains"] = [{"title": HOSTILE, "steps": [HOSTILE]}]
        d["breaking"] = [{"title": HOSTILE, "link": "https://ok.example/b", "source": HOSTILE, "sources": [HOSTILE]}]
        d["domains"] = {"AI & ML": [{"title": HOSTILE, "link": "https://ok.example/x", "source": HOSTILE}]}
        page = build_page.generate_html([d])
        self.assertIn("Intelligence Brief", page)
        check_no_breakout(self, page)

    def test_javascript_story_link_is_neutralised(self):
        # story links come from RSS feeds; a javascript: link must never become a clickable href
        it = {"title": "t", "link": "javascript:alert(1)", "source": "s"}
        out = build_page.item_html(it)
        self.assertIsNone(re.search(r"""href\s*=\s*["']?\s*javascript:""", out, re.I),
                          "build_page.item_html emits href=javascript: for an untrusted story link")

    def test_story_titles_escaped(self):
        out = build_page.item_html({"title": HOSTILE, "link": "https://e.com", "source": "<b>s</b>"})
        self.assertNotIn("<img", out)
        self.assertNotIn("<b>", out)


if __name__ == "__main__":
    unittest.main()
