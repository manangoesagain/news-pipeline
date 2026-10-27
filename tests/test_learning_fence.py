"""The learning folder fence: writes stay inside _data/_learning, data file types only,
emails and key-like tokens are redacted, and the size cap never breaks normal JSON."""
import json
import os
import unittest

import _stubs  # noqa: F401
from _learn_support import TmpCwdCase
import learning_io as io_


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class FenceTests(TmpCwdCase):
    def ok(self, *parts):
        return os.path.join(io_.LEARN_DIR, *parts)

    def test_writes_inside_folder_succeed_and_create_dirs(self):
        io_.safe_write_text(self.ok("sub", "deep", "a.md"), "hello")
        self.assertEqual(_read(self.ok("sub", "deep", "a.md")), "hello")

    def test_refuses_paths_outside_folder(self):
        for p in ("outside.md", os.path.join("_data", "x.json"), os.path.join("_data", "market_history.json"),
                  "digest.json", os.path.join("tests", "a.txt"), os.path.abspath("abs.md"),
                  os.path.join(os.sep, "tmp", "x.md")):
            with self.subTest(p=p):
                with self.assertRaises(io_.FenceError):
                    io_.safe_write_text(p, "x")

    def test_refuses_dotdot_escapes(self):
        for p in (os.path.join(io_.LEARN_DIR, "..", "a.md"),
                  os.path.join(io_.LEARN_DIR, "..", "..", "a.md"),
                  os.path.join(io_.LEARN_DIR, "sub", "..", "..", "a.json"),
                  io_.LEARN_DIR + "/../escape.txt",
                  io_.LEARN_DIR + r"\..\escape.txt"):
            with self.subTest(p=p):
                with self.assertRaises(io_.FenceError):
                    io_.safe_write_text(p, "x")
        self.assertFalse(os.path.exists(os.path.join("_data", "a.md")))
        self.assertFalse(os.path.exists("a.md"))

    def test_abspath_tricks_normalised(self):
        # an absolute path that normalises to a sibling folder with the same prefix must not pass
        sibling = os.path.abspath(io_.LEARN_DIR) + "_evil"
        with self.assertRaises(io_.FenceError):
            io_.safe_write_text(os.path.join(sibling, "a.md"), "x")
        # a path that goes out and back in is fine (still inside)
        p = os.path.join(io_.LEARN_DIR, "x", "..", "back.md")
        io_.safe_write_text(p, "ok")
        self.assertTrue(os.path.exists(self.ok("back.md")))
        # the folder itself is not a file
        with self.assertRaises(Exception):
            io_.safe_write_text(io_.LEARN_DIR, "x")

    def test_refuses_code_like_extensions(self):
        for name in ("a.py", "a.yml", "a.yaml", "a.sh", "a.PY", "a.bat", "a.ps1", "a.js", "a.html", "noext",
                     "a.md.py", "a.json.sh"):
            with self.subTest(name=name):
                with self.assertRaises(io_.FenceError):
                    io_.safe_write_text(self.ok(name), "x")
                self.assertFalse(os.path.exists(self.ok(name)))

    def test_data_extensions_allowed(self):
        for name in ("a.json", "a.md", "a.txt", "A.MD"):
            io_.safe_write_text(self.ok(name), "x")
            self.assertTrue(os.path.exists(self.ok(name)))

    def test_json_writer_obeys_same_fence(self):
        with self.assertRaises(io_.FenceError):
            io_.safe_write_json("outside.json", {"a": 1})
        with self.assertRaises(io_.FenceError):
            io_.safe_write_json(self.ok("x.py"), {"a": 1})

    def test_redacts_emails(self):
        io_.safe_write_text(self.ok("a.md"), "mail me at someone.name+tag@example.co.uk now")
        t = _read(self.ok("a.md"))
        self.assertNotIn("@", t)
        self.assertNotIn("example.co.uk", t)
        self.assertIn("[address removed]", t)

    def test_redacts_key_like_tokens(self):
        keys = ["sk-abcdefghijklmnopqrstuvwx", "nvapi-ABCDEFGHIJKLMNOPQRSTUV12", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
                "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123", "gsk_ABCDEFGHIJKLMNOPQRSTUVWX",
                "a" * 45]
        for k in keys:
            with self.subTest(k=k[:8]):
                io_.safe_write_text(self.ok("k.md"), f"the value is {k} ok")
                t = _read(self.ok("k.md"))
                self.assertNotIn(k, t)
                self.assertIn("the value is", t)

    def test_redact_handles_none_and_non_strings(self):
        self.assertEqual(io_.redact(None), "")
        self.assertEqual(io_.redact(12), "12")

    def test_redaction_keeps_json_valid(self):
        io_.safe_write_json(self.ok("j.json"), {"t": "a@b.com and sk-abcdefghijklmnopqrstuv", "n": [1, 2]})
        d = json.loads(_read(self.ok("j.json")))
        self.assertEqual(d["n"], [1, 2])
        self.assertNotIn("@", d["t"])

    def test_normal_sizes_stay_valid_json(self):
        obj = {"calls": [{"id": f"c{i}", "text": "word " * 20} for i in range(1500)]}
        raw = json.dumps(obj, separators=(",", ":"))
        self.assertLess(len(raw.encode()), io_.MAX_FILE_BYTES)
        io_.safe_write_json(self.ok("big.json"), obj)
        self.assertEqual(json.loads(_read(self.ok("big.json"))), obj)

    def test_oversize_is_capped(self):
        io_.safe_write_text(self.ok("huge.txt"), "x " * 500_000)
        self.assertLessEqual(os.path.getsize(self.ok("huge.txt")), io_.MAX_FILE_BYTES)

    def test_load_helpers_default_on_missing_or_corrupt(self):
        self.assertEqual(io_.load_json("nope.json", {"d": 1}), {"d": 1})
        self.assertEqual(io_.load_text("nope.txt", "dflt"), "dflt")
        io_.safe_write_text(self.ok("bad.json"), "{not json")
        self.assertIsNone(io_.load_json(self.ok("bad.json")))

    def test_failed_write_leaves_no_file_outside(self):
        before = set(os.listdir("."))
        with self.assertRaises(io_.FenceError):
            io_.safe_write_text("evil.md", "x")
        self.assertEqual(set(os.listdir(".")), before)

    def test_json_too_large_raises_instead_of_truncating(self):
        with self.assertRaises(io_.FenceError):
            io_.safe_write_json(self.ok("huge.json"), {"t": "word " * 200_000})
        self.assertFalse(os.path.exists(self.ok("huge.json")))

    def test_atomic_write_leaves_no_temp_files(self):
        io_.safe_write_text(self.ok("a.md"), "one")
        io_.safe_write_text(self.ok("a.md"), "two")
        self.assertEqual(os.listdir(io_.LEARN_DIR), ["a.md"])
        self.assertEqual(_read(self.ok("a.md")), "two")

    def test_symlink_inside_folder_refused(self):
        os.makedirs(io_.LEARN_DIR)
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside)
        try:
            os.symlink(outside, self.ok("link"), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlinks not permitted here")
        with self.assertRaises(io_.FenceError):
            io_.safe_write_text(self.ok("link", "a.md"), "x")
        self.assertEqual(os.listdir(outside), [])

    def test_redacts_other_secret_shapes(self):
        samples = ["123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0", "Bearer abcdefgh12345678",
                   "ghp_" + "A" * 36, "AKIAABCDEFGHIJKLMNOP", "0123456789abcdef0123456789abcdef0123"]
        for s in samples:
            with self.subTest(s=s[:10]):
                self.assertNotIn(s.split()[-1], io_.redact(f"x {s} y"))

    def test_redacts_secret_values_from_environment(self):
        old = os.environ.get("TEST_FAKE_API_KEY")
        os.environ["TEST_FAKE_API_KEY"] = "plainsecretvalue"
        try:
            self.assertNotIn("plainsecretvalue", io_.redact("the value is plainsecretvalue ok"))
        finally:
            if old is None:
                os.environ.pop("TEST_FAKE_API_KEY", None)
            else:
                os.environ["TEST_FAKE_API_KEY"] = old

    def test_ordinary_prose_and_slugs_survive_redaction(self):
        s = "Oil calls hit 80% (n=10); long-link-type-name stays readable."
        self.assertEqual(io_.redact(s), s)

    def test_safe_slug(self):
        self.assertEqual(io_.safe_slug("oil-shock"), "oil-shock")
        for bad in ("ab", "Bad", "../x", "a" * 41, "ok-slug" + chr(10), "", None, 5):
            self.assertIsNone(io_.safe_slug(bad), bad)

    def test_backup_corrupt_copies_inside_folder(self):
        io_.safe_write_text(self.ok("calls.json"), "{not json mail a@b.com")
        self.assertTrue(io_.backup_corrupt(self.ok("calls.json")))
        names = os.listdir(io_.LEARN_DIR)
        self.assertEqual(len([n for n in names if ".corrupt-" in n]), 1)
        backup = [n for n in names if ".corrupt-" in n][0]
        self.assertTrue(backup.endswith(".txt"))
        self.assertNotIn("a@b.com", _read(self.ok(backup)))
        io_.safe_write_json(self.ok("good.json"), {"a": 1})
        self.assertFalse(io_.backup_corrupt(self.ok("good.json")))
        self.assertFalse(io_.backup_corrupt(self.ok("missing.json")))


if __name__ == "__main__":
    unittest.main()
