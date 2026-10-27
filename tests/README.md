# Offline regression tests

Run from the repo root: `python -m unittest discover -s tests`

No network, no third-party packages needed (`tests/_stubs.py` stubs feedparser, requests, matplotlib).
Fixtures: `tests/fixtures/` (a real failed LLM reply and a real graph wrapped as a digest).
