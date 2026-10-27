import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

import _stubs  # noqa: F401
import archive
import patterns

WORKFLOW = os.path.join(_stubs.ROOT, ".github", "workflows", "digest.yml")
NOW = datetime(2026, 10, 22, 18, 0)


def story(i, title, domain="AI & ML", region="US", published=None):
    return {"title": title, "link": f"https://example.com/{i}", "source": "Src", "sources": ["Src"],
            "domain": domain, "region": region, "published": (published or NOW).isoformat(),
            "score": 1.0, "why": "", "summary": ""}


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="digest_tests_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        sd = os.path.join(self.tmp, "_data")
        self._saved = []

        def patch(mod, name, val):
            self._saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, val)

        patch(archive, "SAVE_DIR", sd)
        patch(archive, "ARCHIVE_DIR", os.path.join(sd, "archive"))
        patch(archive, "INDEX_FILE", os.path.join(sd, "archive", "index.json"))
        patch(archive, "THREADS_FILE", os.path.join(sd, "threads.json"))
        patch(patterns, "SAVE_DIR", sd)
        patch(patterns, "ARCHIVE_DIR", os.path.join(sd, "archive"))
        patch(patterns, "THREADS_FILE", os.path.join(sd, "threads.json"))
        patch(patterns, "PREDICTIONS_FILE", os.path.join(sd, "predictions.json"))
        patch(patterns, "PREDICTIONS_ARCHIVE_FILE", os.path.join(sd, "predictions_archive.json"))
        self.addCleanup(self._restore)

    def _restore(self):
        for mod, name, val in reversed(self._saved):
            setattr(mod, name, val)


class ArchiveTests(TempDirCase):
    def test_update_archive_writes_day_and_index_idempotently(self):
        items = {"AI & ML": [story(1, "Anthropic files for public listing"), story(2, "Nvidia chip export curbs widen")]}
        shown = archive.update_archive(items, [], NOW)
        self.assertEqual(len(shown), 2)
        shown2 = archive.update_archive(items, [story(3, "Breaking oil spike hits markets")], NOW + timedelta(hours=3))
        self.assertEqual(len(shown2), 1)
        with open(os.path.join(archive.ARCHIVE_DIR, "2026-10-22.json"), encoding="utf-8") as f:
            day = json.load(f)
        self.assertEqual(len(day["stories"]), 3)
        self.assertTrue(any(s["breaking"] for s in day["stories"]))
        with open(archive.INDEX_FILE, encoding="utf-8") as f:
            idx = json.load(f)
        self.assertEqual(idx["days"][0], {"date": "2026-10-22", "count": 3})

    def test_threads_group_similar_headlines(self):
        a = story(1, "Iran strike fears push oil prices higher", published=NOW)
        b = story(2, "Oil prices climb as Iran strike fears grow", published=NOW + timedelta(hours=1))
        c = story(3, "Totally unrelated gardening festival opens downtown", published=NOW)
        store = archive.update_threads([a, b, c], NOW + timedelta(hours=2))
        sizes = sorted(len(t["stories"]) for t in store["threads"])
        self.assertEqual(sizes, [1, 2])
        # re-ingesting is idempotent
        store2 = archive.update_threads([a, b, c], NOW + timedelta(hours=3))
        self.assertEqual(sorted(len(t["stories"]) for t in store2["threads"]), [1, 2])

    def test_norm_time_is_idempotent_and_tolerant(self):
        once = archive.norm_time("2026-10-22T18:00:00")
        self.assertEqual(archive.norm_time(once), once)
        self.assertEqual(archive.norm_time("not a date"), "not a date")

    def test_load_tolerates_corrupt_file(self):
        os.makedirs(archive.ARCHIVE_DIR)
        with open(os.path.join(archive.ARCHIVE_DIR, "2026-10-22.json"), "w") as f:
            f.write("{broken")
        shown = archive.update_archive({"AI & ML": [story(1, "Recovered after corrupt archive file")]}, [], NOW)
        self.assertEqual(len(shown), 1)


class PatternsTests(TempDirCase):
    def _seed_archive(self):
        for back, n, word in ((0, 4, "tariff"), (1, 4, "tariff"), (2, 4, "tariff"), (4, 1, "tariff"), (6, 1, "gardening")):
            day = NOW - timedelta(days=back)
            stories = [story(f"{back}-{i}", f"{word} escalation story number {i} on {back}", published=day) for i in range(n)]
            archive.update_archive({"AI & ML": stories}, [], day)

    def test_detect_patterns_on_empty_dir_returns_shape(self):
        r = patterns.detect_patterns(NOW)
        self.assertEqual(set(r), {"heating", "cooling", "cross_domain", "heat"})

    def test_detect_patterns_flags_heating_term(self):
        self._seed_archive()
        r = patterns.detect_patterns(NOW)
        self.assertIn("tariff", [h["term"] for h in r["heating"]])
        self.assertIn("AI & ML", r["heat"])

    def test_predictions_record_dedupe_and_accuracy(self):
        sc = [{"if": "Iran strikes oil facilities", "then": "Brent rises above recent highs", "likelihood": "high"},
              {"if": "Iran strikes oil facilities soon", "then": "Brent rises above recent highs again"},
              {"if": "", "then": "ignored"}, "junk"]
        added = patterns.record_predictions(sc, NOW)
        self.assertEqual(len(added), 1)
        self.assertEqual(patterns.record_predictions(sc, NOW), [])
        self.assertEqual(patterns.accuracy_summary()["resolved"], 0)

    def test_review_predictions_with_fake_llm(self):
        old = NOW - timedelta(days=10)
        added = patterns.record_predictions([{"if": "Fed cuts rates", "then": "Dollar weakens broadly"}], old)
        pid = added[0]["id"]

        class FakeLLM:
            @staticmethod
            def generate_json(*a, **k):
                return {"results": [{"id": pid, "status": "happened", "evidence": "DXY fell"},
                                    {"id": "nope", "status": "happened"}, {"id": pid, "status": "bogus"}]}

        out = patterns.review_predictions(NOW, FakeLLM, ["Dollar index falls after Fed cut"])
        self.assertEqual([p["id"] for p in out], [pid])
        self.assertEqual(patterns.accuracy_summary()["happened"], 1)
        self.assertEqual(patterns.accuracy_summary()["hit_rate"], 1.0)

    def test_review_without_llm_or_headlines_is_noop(self):
        self.assertEqual(patterns.review_predictions(NOW, None, ["x"]), [])

    def test_enrich_nodes_with_stats_never_raises(self):
        self._seed_archive()
        g = {"nodes": [{"id": "n1", "label": "Tariff escalation", "detail": "story on tariffs"}, {"id": "n2"}, None]}
        out = patterns.enrich_nodes_with_stats(g, NOW)
        self.assertIn("history", out["nodes"][0])
        self.assertIn("trend", out["nodes"][0]["history"])


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WORKFLOW, encoding="utf-8") as f:
            cls.text = f.read()
        try:
            import yaml  # type: ignore
            cls.doc = yaml.safe_load(cls.text)
        except Exception:
            cls.doc = None

    def steps_by_name(self):
        """name -> raw text block of that step (text-only parse so it works without PyYAML)."""
        blocks, cur, name = {}, [], None
        for line in self.text.splitlines():
            m = re.match(r"^      - (?:name: (.+)|uses: .+)$", line)
            if m:
                if name is not None or cur:
                    blocks[name or f"_{len(blocks)}"] = "\n".join(cur)
                name, cur = (m.group(1).strip() if m.group(1) else None), [line]
            else:
                cur.append(line)
        blocks[name or f"_{len(blocks)}"] = "\n".join(cur)
        return blocks

    def test_parses_as_yaml_when_available(self):
        if self.doc is None:
            self.skipTest("PyYAML not installed; text checks below still run")
        self.assertIn("jobs", self.doc)
        on = self.doc.get("on", self.doc.get(True))  # PyYAML reads bare `on` as True
        self.assertIn("workflow_dispatch", on)
        self.assertIn("dry_run", on["workflow_dispatch"]["inputs"])

    def test_dry_run_input_declared(self):
        self.assertRegex(self.text, r"(?m)^      dry_run:")

    def test_deploy_steps_guarded_by_dry_run(self):
        blocks = self.steps_by_name()
        deploy = {n: b for n, b in blocks.items()
                  if re.search(r"peaceiris/actions-gh-pages|gh api -X POST repos/.*/pages/builds|git push", b)}
        self.assertGreaterEqual(len(deploy), 2, f"expected deploy + pages rebuild steps, found {list(deploy)}")
        for name, b in deploy.items():
            m = re.search(r"(?m)^\s+if: (.+)$", b)
            self.assertIsNotNone(m, f"step {name!r} has no if: guard")
            self.assertIn("!inputs.dry_run", m.group(1), f"step {name!r} not guarded by !inputs.dry_run")

    def test_dry_run_is_passed_to_digest_script(self):
        self.assertRegex(self.text, r"DRY_RUN: \$\{\{ inputs\.dry_run \}\}")

    def test_secrets_only_in_env_blocks_never_in_run_or_echo(self):
        in_run = False
        for i, line in enumerate(self.text.splitlines(), 1):
            if re.match(r"^\s+run:", line):
                in_run = True
                if "secrets." in line:
                    self.fail(f"line {i}: secret referenced inside run: {line.strip()}")
                continue
            if in_run:
                if re.match(r"^      - |^        \w[\w-]*:", line) and not line.startswith("          "):
                    in_run = False
                elif "secrets." in line:
                    self.fail(f"line {i}: secret referenced inside a run block: {line.strip()}")
        for i, line in enumerate(self.text.splitlines(), 1):
            if re.search(r"\becho\b|\bprintenv\b|\benv\s*$|set -x", line) and re.search(
                    r"secrets\.|API_KEY|TOKEN|RESEND|\$\{\{", line):
                self.fail(f"line {i}: possible secret echo: {line.strip()}")

    def test_no_pull_request_target_trigger(self):
        self.assertNotIn("pull_request_target", self.text)

    def test_least_privilege_permissions_declared(self):
        self.assertRegex(self.text, r"(?m)^    permissions:")


if __name__ == "__main__":
    unittest.main()
