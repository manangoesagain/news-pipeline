"""Make the repo importable on a bare Python: stub third-party modules that are not installed
(feedparser, requests, matplotlib, numpy). Import this first in every test module."""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _stub(name, **attrs):
    try:
        __import__(name)
        return
    except Exception:
        pass
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


def _no_network(*a, **k):
    raise RuntimeError("network access is disabled in tests")


_stub("feedparser", parse=_no_network)
_stub("requests", get=_no_network, post=_no_network, Session=_no_network)
_stub("matplotlib")
_stub("numpy")


def fixture(name):
    return os.path.join(FIXTURES, name)
