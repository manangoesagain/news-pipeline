"""Shared helpers for the self-learning-loop tests: a fresh temp working directory per test
(the learning folder path is relative, so chdir isolates it), and a tiny monkeypatch stand-in."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

import _stubs  # noqa: F401


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append(("attr", obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def setitem(self, mapping, key, value):
        self._undo.append(("item", mapping, key, mapping.get(key, KeyError)))
        mapping[key] = value

    def undo(self):
        for u in reversed(self._undo):
            if u[0] == "attr":
                setattr(u[1], u[2], u[3])
            elif u[3] is KeyError:
                u[1].pop(u[2], None)
            else:
                u[1][u[2]] = u[3]
        self._undo = []


class TmpCwdCase(unittest.TestCase):
    """Each test runs in its own empty temp directory; cwd is restored afterwards."""

    def setUp(self):
        self._old_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="learn_test_")
        os.chdir(self.tmp)
        self.mp = MonkeyPatch()
        os.makedirs("_data", exist_ok=True)

    def tearDown(self):
        self.mp.undo()
        os.chdir(self._old_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def capture(self):
        return contextlib.redirect_stdout(io.StringIO())


def tree_snapshot(root):
    """{relative path: (size, sha1)} for every file under root."""
    import hashlib
    out = {}
    for d, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(d, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha1(f.read()).hexdigest()
    return out
