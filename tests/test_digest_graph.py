import copy
import json
import re
import unittest

import _stubs  # noqa: F401
import digest

LEAK = re.compile(r"\bn\d{1,3}\b|\(\s*ids?\b", re.I)
DOM = "AI & ML"


def node(i, label, **kw):
    d = {"id": f"n{i}", "label": label, "type": "event", "domain": DOM, "region": "US",
         "detail": f"Detail for {label}", "direction": "up"}
    d.update(kw)
    return d


def edge(a, b, label="raises prices", **kw):
    d = {"from": f"n{a}", "to": f"n{b}", "label": label, "effect": "up", "strength": 2,
         "confidence": "medium", "status": "reported", "order": 1, "kind": "causal"}
    d.update(kw)
    return d


def base_graph():
    nodes = [node(1, "Iran strike fears"), node(2, "Oil price surge", type="commodity"),
             node(3, "Inflation worries"), node(4, "Rupee pressure", type="market"),
             node(5, "Fed rate outlook")]
    edges = [edge(1, 2), edge(2, 3), edge(3, 4), edge(3, 5)]
    return {"nodes": nodes, "edges": edges}


def by_label(g, label):
    return next((n for n in g["nodes"] if n["label"] == label), None)


def get_edge(g, a, b):
    return next(e for e in g["edges"] if (e["from"], e["to"]) == (f"n{a}", f"n{b}"))


class SanitizeGraphTests(unittest.TestCase):
    def test_base_graph_survives(self):
        g = digest.sanitize_graph(base_graph())
        self.assertEqual(len(g["nodes"]), 5)
        self.assertEqual(len(g["edges"]), 4)

    def test_non_dict_none(self):
        for bad in (None, [], "x", 3):
            self.assertIsNone(digest.sanitize_graph(bad))

    def test_type_normalisation(self):
        raw = base_graph()
        raw["nodes"][0]["type"] = "  COMPANY "
        raw["nodes"][2]["type"] = "totally-bogus"
        g = digest.sanitize_graph(raw)
        self.assertEqual(by_label(g, "Iran strike fears")["type"], "company")
        self.assertEqual(by_label(g, "Inflation worries")["type"], "event")
        self.assertEqual(by_label(g, "Rupee pressure")["type"], "index")  # 'market' -> 'index'

    def test_unknown_domain_blanked(self):
        raw = base_graph()
        raw["nodes"][0]["domain"] = "Cooking"
        g = digest.sanitize_graph(raw)
        self.assertEqual(by_label(g, "Iran strike fears")["domain"], "")
        self.assertEqual(by_label(g, "Oil price surge")["domain"], DOM)

    def test_ids_and_labels_do_not_leak(self):
        raw = base_graph()
        raw["nodes"][0]["label"] = "Iran strike fears (IDs 5, 18)"
        raw["nodes"][1]["label"] = "Oil n12 surge"
        raw["nodes"][2]["detail"] = "Driven by n12 and node n7 (ids 5,18) per reports"
        raw["nodes"][2]["why_it_matters"] = "See (IDs 5,18)"
        raw["edges"][0]["label"] = "n1 pushes n2 higher"
        raw["edges"][0]["why"] = "because node n1 (ids 3, 4)"
        g = digest.sanitize_graph(raw)
        for n in g["nodes"]:
            for k in ("label", "detail", "why_it_matters"):
                self.assertIsNone(LEAK.search(n.get(k, "")), f"{k} leaked: {n.get(k)!r}")
        for e in g["edges"]:
            for k in ("label", "why"):
                self.assertIsNone(LEAK.search(e.get(k, "")), f"edge {k} leaked: {e.get(k)!r}")
        self.assertIsNotNone(by_label(g, "Iran strike fears"))

    def test_ids_list_numeric_and_capped(self):
        raw = base_graph()
        raw["nodes"][0]["ids"] = [1, "2", "x", 3, 4, 5, 6]
        g = digest.sanitize_graph(raw)
        self.assertEqual(by_label(g, "Iran strike fears")["ids"], [1, 2, 3, 4])

    def test_duplicate_ticker_nodes_merge(self):
        raw = {"nodes": [node(1, "Nifty 50", type="index", market="NIFTY"),
                         node(2, "Nifty 50 Index", type="index", market="NIFTY"),
                         node(3, "WTI Crude", type="commodity"),
                         node(4, "WTI Crude Oil", type="commodity"),
                         node(5, "Oil demand"), node(6, "Rate cut hopes")],
               "edges": [edge(5, 3), edge(5, 4, "also lifts"), edge(4, 2, "feeds"), edge(3, 6), edge(6, 1)]}
        g = digest.sanitize_graph(raw)
        labels = [n["label"] for n in g["nodes"]]
        self.assertEqual(sum(1 for l in labels if l.startswith("Nifty")), 1, labels)
        self.assertEqual(sum(1 for l in labels if l.startswith("WTI")), 1, labels)
        ids = {n["id"] for n in g["nodes"]}
        for e in g["edges"]:
            self.assertIn(e["from"], ids)
            self.assertIn(e["to"], ids)
            self.assertNotEqual(e["from"], e["to"])
        pairs = [(e["from"], e["to"]) for e in g["edges"]]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_distinct_nodes_not_merged(self):
        g = digest.sanitize_graph(base_graph())
        self.assertEqual(len(g["nodes"]), 5)

    def test_one_direction_per_pair_keeps_stronger(self):
        raw = base_graph()
        raw["edges"].append(edge(2, 1, "reverse", strength=3))
        raw["edges"][0]["strength"] = 1
        g = digest.sanitize_graph(raw)
        pair = [e for e in g["edges"] if {e["from"], e["to"]} == {"n1", "n2"}]
        self.assertEqual(len(pair), 1)
        self.assertEqual((pair[0]["from"], pair[0]["to"]), ("n2", "n1"))

    def test_feedback_loop_keeps_both_directions(self):
        raw = base_graph()
        raw["edges"].append(edge(2, 1, "loop", kind="feedback"))
        g = digest.sanitize_graph(raw)
        pair = [e for e in g["edges"] if {e["from"], e["to"]} == {"n1", "n2"}]
        self.assertEqual(len(pair), 2)

    def test_edge_label_cut_on_word_boundary(self):
        raw = base_graph()
        long = "increases borrowing costs for heavily indebted emerging market importers"
        raw["edges"][0]["label"] = long
        g = digest.sanitize_graph(raw)
        lab = get_edge(g, 1, 2)["label"]
        self.assertLessEqual(len(lab), 38)
        self.assertTrue(long.startswith(lab))
        self.assertEqual(long[len(lab)], " ", f"cut mid-word: {lab!r}")
        self.assertFalse(lab.endswith("…"))

    def test_enum_defaults(self):
        raw = base_graph()
        raw["edges"][0].update(effect="SIDEWAYS", confidence="??", status="maybe", kind="weird", lag="years")
        g = digest.sanitize_graph(raw)
        e = get_edge(g, 1, 2)
        self.assertEqual((e["effect"], e["confidence"], e["status"], e["kind"]),
                         ("mixed", "medium", "predicted", "causal"))
        self.assertNotIn("lag", e)

    def test_effect_words_preserved(self):
        raw = base_graph()
        raw["edges"][0]["effect"] = "down"
        raw["edges"][1]["effect"] = "UP"
        raw["edges"][2]["effect"] = "mixed"
        g = digest.sanitize_graph(raw)
        self.assertEqual(get_edge(g, 1, 2)["effect"], "down")
        self.assertEqual(get_edge(g, 2, 3)["effect"], "up")
        self.assertEqual(get_edge(g, 3, 4)["effect"], "mixed")

    def test_strength_and_order_clamped(self):
        raw = base_graph()
        raw["edges"][0].update(strength=99, order="x")
        raw["edges"][1].update(strength=-4, order=7)
        g = digest.sanitize_graph(raw)
        e0, e1 = get_edge(g, 1, 2), get_edge(g, 2, 3)
        self.assertEqual((e0["strength"], e0["order"]), (3, 1))
        self.assertEqual((e1["strength"], e1["order"]), (1, 3))

    def test_weak_flag(self):
        raw = base_graph()
        raw["edges"][0].update(strength=1, confidence="low")                      # weak
        raw["edges"][1].update(strength=1, confidence="low", kind="contradicts")  # contradicts is never weak
        raw["edges"][2].update(strength=2, confidence="low")                      # not weak
        g = digest.sanitize_graph(raw)
        self.assertTrue(get_edge(g, 1, 2).get("weak"))
        self.assertFalse(get_edge(g, 2, 3).get("weak"))
        self.assertFalse(get_edge(g, 3, 4).get("weak"))
        self.assertFalse(get_edge(g, 3, 5).get("weak"))

    def test_weak_links_are_kept_not_dropped(self):
        raw = base_graph()
        raw["edges"][3].update(strength=1, confidence="low")
        g = digest.sanitize_graph(raw)
        self.assertEqual(len(g["edges"]), 4)

    def test_orphan_nodes_dropped(self):
        raw = base_graph()
        raw["nodes"].append(node(9, "Lonely orphan"))
        g = digest.sanitize_graph(raw)
        self.assertIsNone(by_label(g, "Lonely orphan"))
        self.assertEqual(len(g["nodes"]), 5)

    def test_edges_to_unknown_nodes_and_self_loops_dropped(self):
        raw = base_graph()
        raw["edges"].append(edge(1, 99))
        raw["edges"].append(edge(3, 3))
        g = digest.sanitize_graph(raw)
        self.assertEqual(len(g["edges"]), 4)

    def test_too_few_nodes_none(self):
        raw = base_graph()
        raw["nodes"] = raw["nodes"][:3]
        raw["edges"] = [edge(1, 2), edge(2, 3), edge(1, 3)]
        self.assertIsNone(digest.sanitize_graph(raw))

    def test_too_few_edges_none(self):
        raw = base_graph()
        raw["edges"] = raw["edges"][:2]
        self.assertIsNone(digest.sanitize_graph(raw))

    def test_garbage_entries_ignored(self):
        raw = base_graph()
        raw["nodes"] += [None, "x", 5, {"id": "", "label": "no id"}, {"id": "n77"}]
        raw["edges"] += [None, "x", {"from": "n1"}]
        g = digest.sanitize_graph(raw)
        self.assertEqual(len(g["nodes"]), 5)

    def test_caps(self):
        nodes = [node(i, f"Distinct topic {chr(97 + i % 26)}{i} word{i}") for i in range(1, 70)]
        edges = [edge(i, i + 1, strength=1 + i % 3) for i in range(1, 69)]
        g = digest.sanitize_graph({"nodes": nodes, "edges": edges})
        self.assertLessEqual(len(g["nodes"]), digest.MAX_NODES)
        self.assertLessEqual(len(g["edges"]), digest.MAX_EDGES)
        ids = {n["id"] for n in g["nodes"]}
        for e in g["edges"]:
            self.assertTrue(e["from"] in ids and e["to"] in ids)


def sg():
    return digest.sanitize_graph(base_graph())


def card(**kw):
    c = {"so_what": "Oil jump hurts importers like India", "headline": "Oil surges on war fears",
         "points": ["What: oil +4%", "Why: risk premium", "Next: if talks fail", "Watch: tankers"],
         "for_you": {"India": {"dir": "down", "why": "Costlier imports"}, "US": {"dir": "UP", "why": "Producers gain"},
                     "Canada": {"dir": "sideways", "why": "Mixed"}},
         "currency": {"INR": {"dir": "weaker", "why": "Oil import bill"}, "CAD": {"dir": "stronger", "why": "Oil exporter"},
                      "USD": {"dir": "Wobbly", "why": "x"}},
         "predictions": [{"if": "talks fail", "then": "oil rises further", "odds": "HIGH", "horizon": "3 days",
                          "reasoning": "r", "watch": "w", "wrong_if": "x"}],
         "against": ["Demand is soft"], "pattern": "Spikes often fade"}
    c.update(kw)
    return c


class SanitizeCardsTests(unittest.TestCase):
    def run_cards(self, c, nid="n2"):
        g = sg()
        kept = digest.sanitize_cards({"cards": {nid: c}, "links": {}}, g, [nid])
        return g, kept, next(n for n in g["nodes"] if n["id"] == nid)

    def test_basic_card_attached(self):
        g, kept, n = self.run_cards(card())
        self.assertEqual(kept, 1)
        self.assertEqual(n["card"]["so_what"], "Oil jump hurts importers like India")
        self.assertEqual(len(n["card"]["points"]), 4)

    def test_so_what_truncated(self):
        _, _, n = self.run_cards(card(so_what="word " * 60))
        self.assertLessEqual(len(n["card"]["so_what"]), 110)

    def test_for_you_dict_normalised(self):
        _, _, n = self.run_cards(card())
        fy = n["card"]["for_you"]
        self.assertEqual(fy["India"]["dir"], "down")
        self.assertEqual(fy["US"]["dir"], "up")
        self.assertEqual(fy["Canada"]["dir"], "neutral")

    def test_for_you_legacy_string(self):
        _, _, n = self.run_cards(card(for_you={"India": "Costlier imports", "US": "", "Canada": None}))
        self.assertEqual(n["card"]["for_you"], {"India": {"dir": "neutral", "why": "Costlier imports"}})

    def test_for_you_garbage(self):
        _, _, n = self.run_cards(card(for_you="nonsense"))
        self.assertEqual(n["card"]["for_you"], {})

    def test_currency_dirs(self):
        _, _, n = self.run_cards(card())
        cur = n["card"]["currency"]
        self.assertEqual(cur["INR"]["dir"], "weaker")
        self.assertEqual(cur["CAD"]["dir"], "stronger")
        self.assertNotIn("USD", cur)  # invalid direction dropped

    def test_predictions_normalised(self):
        preds = [{"if": "a", "then": "b", "odds": "maybe", "horizon": "months"},
                 {"if": "a", "then": "b", "odds": "Low", "horizon": "2 Days"},
                 {"if": "no then"}, "junk"]
        _, _, n = self.run_cards(card(predictions=preds))
        p = n["card"]["predictions"]
        self.assertEqual(len(p), 2)
        self.assertEqual((p[0]["odds"], p[0]["horizon"]), ("medium", "weeks"))
        self.assertEqual((p[1]["odds"], p[1]["horizon"]), ("low", "days"))
        n2 = self.run_cards(card())[2]
        self.assertEqual(n2["card"]["predictions"][0]["odds"], "high")
        self.assertEqual(n2["card"]["predictions"][0]["horizon"], "days")

    def test_predictions_capped_at_three(self):
        preds = [{"if": "a", "then": f"b{i}"} for i in range(6)]
        _, _, n = self.run_cards(card(predictions=preds))
        self.assertEqual(len(n["card"]["predictions"]), 3)

    def test_card_without_points_rejected(self):
        g, kept, n = self.run_cards(card(points=[]))
        self.assertEqual(kept, 0)
        self.assertNotIn("card", n)

    def test_unknown_node_and_bad_input(self):
        g = sg()
        self.assertEqual(digest.sanitize_cards({"cards": {"n999": card()}}, g, []), 0)
        self.assertEqual(digest.sanitize_cards(None, g, []), 0)
        self.assertEqual(digest.sanitize_cards({"cards": "x", "links": []}, g, []), 0)
        self.assertEqual(digest.sanitize_cards({"cards": {"n2": "str"}}, g, []), 0)

    def test_leaked_ids_stripped_everywhere(self):
        c = card(so_what="Oil n12 hurts India (IDs 5, 18)", headline="n3 drives (ids 4,5) oil",
                 points=["n12 rose (IDs 5,18)", "node n7 matters", "ok", "fine"],
                 predictions=[{"if": "n3 falls", "then": "n4 rises (ids 1, 2)", "reasoning": "node n9"}],
                 against=["n5 (IDs 5, 18) argues"], pattern="like n1")
        _, _, n = self.run_cards(c)
        blob = json.dumps(n["card"])
        self.assertIsNone(LEAK.search(blob), blob)

    def test_edge_evidence_describing_data_fields_dropped(self):
        for bad in ("The labeled node says oil rose", "per the detail field", "the node states oil rose", "Labelled A -> B"):
            g = sg()
            digest.sanitize_cards({"cards": {}, "links": {"n1>n2": {"steps": ["a", "b"], "evidence": bad}}}, g, [])
            e = get_edge(g, 1, 2)
            self.assertEqual(e["card"]["evidence"], "", bad)

    def test_edge_evidence_real_fact_kept(self):
        g = sg()
        digest.sanitize_cards({"cards": {}, "links": {"n1>n2": {
            "steps": ["a"], "evidence": "WTI rose 3.8% on Iran fears", "weaken": "calm", "next": "CPI",
            "seen_before": "often"}}}, g, [])
        e = get_edge(g, 1, 2)
        self.assertEqual(e["card"]["evidence"], "WTI rose 3.8% on Iran fears")
        self.assertEqual(e["card"]["steps"], ["a"])

    def test_edge_card_needs_steps(self):
        g = sg()
        digest.sanitize_cards({"cards": {}, "links": {"n1>n2": {"steps": [], "evidence": "x"}}}, g, [])
        self.assertTrue(all("card" not in e for e in g["edges"]))


class LightCardsAndImpactTests(unittest.TestCase):
    def test_hub_ids_ranked_by_degree(self):
        g = sg()
        hubs, deg = digest.hub_ids(g)
        self.assertEqual(hubs[0], "n3")  # n3 has 3 links
        self.assertEqual(deg["n3"], 3)

    def test_light_cards_filled_and_not_overwriting(self):
        g = sg()
        existing = {"so_what": "keep", "points": ["p"], "light": False}
        next(n for n in g["nodes"] if n["id"] == "n3")["card"] = existing
        digest.light_cards(g)
        self.assertIs(next(n for n in g["nodes"] if n["id"] == "n3")["card"], existing)
        n2 = next(n for n in g["nodes"] if n["id"] == "n2")
        self.assertTrue(n2["card"]["light"])
        self.assertTrue(n2["card"]["points"])
        self.assertIn("lifts", n2["card"]["so_what"])
        self.assertIn("Inflation worries", n2["card"]["so_what"])
        for k in ("headline", "for_you", "currency", "predictions", "against", "pattern"):
            self.assertIn(k, n2["card"])
        self.assertIsNone(LEAK.search(json.dumps(n2["card"])))

    def test_light_cards_down_effect(self):
        raw = base_graph()
        raw["edges"][1]["effect"] = "down"
        g = digest.sanitize_graph(raw)
        digest.light_cards(g)
        n2 = next(n for n in g["nodes"] if n["id"] == "n2")
        self.assertIn("hurts", n2["card"]["so_what"])

    def test_compute_impact_small_graph(self):
        g = sg()
        g["nodes"][2]["market"] = {"change_pct": 2.5}
        digest.compute_impact(g)
        for n in g["nodes"]:
            im = n["impact"]
            self.assertIn(im["size"], ("small", "medium", "big"))
            self.assertIn(im["sure"], ("low", "medium", "high"))
            self.assertIn(im["when"], ("days", "weeks"))
        top = {n["impact"]["top5"]: n for n in g["nodes"] if n["impact"]["top5"]}
        self.assertEqual(top[1]["id"], "n3")  # hub with market move ranks first
        self.assertEqual(top[1]["impact"]["size"], "big")
        self.assertLessEqual(len(top), 5)
        self.assertEqual(sorted(top), list(range(1, len(top) + 1)))
        for n in g["nodes"]:
            if n["impact"]["size"] == "small":
                self.assertEqual(n["impact"]["top5"], 0)

    def test_leaf_nodes_are_small(self):
        g = sg()
        digest.compute_impact(g)
        leaf = next(n for n in g["nodes"] if n["id"] == "n1")  # degree 1, no market
        self.assertEqual(leaf["impact"]["size"], "small")

    def test_compute_impact_real_graph(self):
        with open(_stubs.fixture("digest_sample.json"), encoding="utf-8") as f:
            g = copy.deepcopy(json.load(f)["intelligence"]["graph"])
        digest.compute_impact(g)
        self.assertTrue(all("impact" in n for n in g["nodes"]))
        ranks = sorted(n["impact"]["top5"] for n in g["nodes"] if n["impact"]["top5"])
        self.assertEqual(ranks, [1, 2, 3, 4, 5])
        sizes = {n["impact"]["size"] for n in g["nodes"]}
        self.assertTrue({"big", "small"} <= sizes, sizes)

    def test_compute_impact_never_raises_on_bad_graph(self):
        digest.compute_impact({"nodes": [{"id": "a"}], "edges": [{"from": "a"}]})


class RealGraphRoundTrip(unittest.TestCase):
    def test_sanitize_real_graph_keeps_effects(self):
        with open(_stubs.fixture("digest_sample.json"), encoding="utf-8") as f:
            g = json.load(f)["intelligence"]["graph"]
        out = digest.sanitize_graph(copy.deepcopy(g))
        self.assertIsNotNone(out)
        self.assertGreaterEqual(len(out["nodes"]), 30)
        self.assertTrue(all(e["effect"] in ("up", "down", "mixed") for e in out["edges"]))
        self.assertGreater(sum(1 for e in out["edges"] if e["effect"] == "up"), 10)


if __name__ == "__main__":
    unittest.main()
