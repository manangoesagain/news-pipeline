#!/usr/bin/env python3
"""
Pattern detection over the permanent archive, plus a predictions ledger.
detect_patterns() needs no LLM. review_predictions() uses one batched LLM call and
silently leaves predictions open if the LLM is unavailable. Nothing here raises.
"""

import glob
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

SAVE_DIR = "_data"
ARCHIVE_DIR = os.path.join(SAVE_DIR, "archive")
THREADS_FILE = os.path.join(SAVE_DIR, "threads.json")
PREDICTIONS_FILE = os.path.join(SAVE_DIR, "predictions.json")

RECENT_DAYS = 3          # "now" window for topics
PREV_DAYS = 8            # baseline window for topics (days 3..10 back)
HEAT_DAYS = 7
MIN_TERM_STORIES = 3
MAX_TERMS = 8
PREDICTION_DUE_DAYS = 14
REVIEW_AFTER_DAYS = 7
MAX_REVIEW = 12
MAX_HEADLINES = 150
DEDUPE_JACCARD = 0.5
RESOLVED_KEEP_DAYS = 90
PREDICTIONS_ARCHIVE_FILE = os.path.join(SAVE_DIR, "predictions_archive.json")

try:
    from archive import tokens as _tokens
except Exception:  # pragma: no cover
    _STOP = {"the", "and", "for", "with", "from", "after", "over", "says", "new", "amid", "into", "than"}

    def _tokens(title):
        return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if w not in _STOP and len(w) > 2}


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _day(now, back):
    return (now - timedelta(days=back)).strftime("%Y-%m-%d")


def _load_archive(now, days_back):
    """{date: [stories]} for archive files within the last days_back days."""
    oldest = _day(now, days_back)
    out = {}
    for path in glob.glob(os.path.join(ARCHIVE_DIR, "*.json")):
        name = os.path.basename(path)[:-5]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) or name < oldest or name > now.strftime("%Y-%m-%d"):
            continue
        data = _load(path, {})
        stories = data.get("stories") if isinstance(data, dict) else None
        if isinstance(stories, list):
            out[name] = [s for s in stories if isinstance(s, dict) and s.get("title")]
    return out


def detect_patterns(now=None) -> dict:
    now = now or datetime.now()
    result = {"heating": [], "cooling": [], "cross_domain": [], "heat": {}}
    try:
        _detect(now, result)
    except Exception as e:
        print(f"[PATTERNS] detect failed: {e}")
    return result


def _detect(now, result):
    archive = _load_archive(now, RECENT_DAYS + PREV_DAYS)
    threads = _load(THREADS_FILE, {"threads": []}).get("threads", [])
    recent_dates = {_day(now, i) for i in range(RECENT_DAYS)}
    prev_dates = {_day(now, i) for i in range(RECENT_DAYS, RECENT_DAYS + PREV_DAYS)}

    # --- heating / cooling terms ---
    n_recent = len([d for d in archive if d in recent_dates])
    n_prev = len([d for d in archive if d in prev_dates])
    counts = defaultdict(lambda: [0, 0])
    doms = defaultdict(set)
    for date, stories in archive.items():
        idx = 0 if date in recent_dates else 1 if date in prev_dates else None
        if idx is None:
            continue
        for s in stories:
            for w in _tokens(s["title"]):
                counts[w][idx] += 1
                if s.get("domain"):
                    doms[w].add(s["domain"])
    if n_recent and n_prev:
        rows = []
        for w, (r, p) in counts.items():
            if r + p < MIN_TERM_STORIES:
                continue
            # per-day rates, expressed as a 3-day-equivalent change
            delta = round((r / n_recent - p / n_prev) * RECENT_DAYS, 1)
            if abs(delta) >= 2:
                rows.append((w, delta))
        best_thread = {}
        for w, _ in rows:
            cands = [t for t in threads if w in (t.get("keywords") or [])]
            if cands:
                best_thread[w] = max(cands, key=lambda t: len(t.get("stories", [])))["id"]
        mk = lambda w, d: {"term": w, "thread_id": best_thread.get(w), "delta": d, "domains": sorted(doms[w])}
        result["heating"] = [mk(w, d) for w, d in sorted(rows, key=lambda x: -x[1]) if d > 0][:MAX_TERMS]
        result["cooling"] = [mk(w, d) for w, d in sorted(rows, key=lambda x: x[1]) if d < 0][:MAX_TERMS]

    # --- domain x region heat, 7d vs previous 7d ---
    heat_archive = _load_archive(now, HEAT_DAYS * 2)
    now_dates = {_day(now, i) for i in range(HEAT_DAYS)}
    heat = {}
    for date, stories in heat_archive.items():
        key = "now" if date in now_dates else "prev"
        for s in stories:
            d, r = s.get("domain"), s.get("region")
            if not d or not r:
                continue
            cell = heat.setdefault(d, {}).setdefault(r, {"now": 0, "prev": 0})
            cell[key] += 1
    result["heat"] = heat

    # --- cross-domain threads with a story in the last 48h ---
    since = _day(now, 2)
    cross = []
    for t in threads:
        ds = t.get("domains") or []
        last = [x.get("date", "") for x in t.get("stories", [])]
        if len(ds) >= 2 and last and max(last) >= since:
            cross.append({"thread_id": t["id"], "title": t.get("title", ""), "domains": ds,
                          "last_seen": t.get("last_seen", "")})
    cross.sort(key=lambda c: (-len(c["domains"]), c["last_seen"]), reverse=False)
    cross.sort(key=lambda c: c["last_seen"], reverse=True)
    result["cross_domain"] = cross[:10]


# ----------------------------------------------------------------- predictions

def _ptoks(p):
    return _tokens(f"{p.get('if', '')} {p.get('then', '')}")


def _similar(a, b):
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= DEDUPE_JACCARD


def _load_preds():
    d = _load(PREDICTIONS_FILE, {"predictions": []})
    if not isinstance(d, dict) or not isinstance(d.get("predictions"), list):
        d = {"predictions": []}
    return d


def _prune_resolved(store, now):
    """Move resolved predictions older than RESOLVED_KEEP_DAYS into an archive file. True if changed."""
    cut = _day(now, RESOLVED_KEEP_DAYS)
    keep, old = [], []
    for p in store["predictions"]:
        if p.get("status") != "open" and (p.get("resolved") or p.get("created") or "9999") < cut:
            old.append(p)
        else:
            keep.append(p)
    if not old:
        return False
    arch = _load(PREDICTIONS_ARCHIVE_FILE, {"predictions": []})
    if not isinstance(arch, dict) or not isinstance(arch.get("predictions"), list):
        arch = {"predictions": []}
    have = {p.get("id") for p in arch["predictions"]}
    arch["predictions"].extend(p for p in old if p.get("id") not in have)
    _save(PREDICTIONS_ARCHIVE_FILE, arch)
    store["predictions"] = keep
    return True


def record_predictions(scenarios, now=None) -> list:
    """Store intel scenarios as open predictions. Returns the newly added ones."""
    now = now or datetime.now()
    added = []
    try:
        store = _load_preds()
        preds = store["predictions"]
        seen = [_ptoks(p) for p in preds]
        n = len(preds)
        for s in scenarios or []:
            if not isinstance(s, dict) or not s.get("if") or not s.get("then"):
                continue
            t = _ptoks(s)
            if any(_similar(t, o) for o in seen):
                continue
            n += 1
            p = {"id": f"p{now.strftime('%Y%m%d')}-{n}", "if": str(s["if"]), "then": str(s["then"]),
                 "likelihood": str(s.get("likelihood", "")), "signpost": str(s.get("signpost", "")),
                 "created": now.strftime("%Y-%m-%d"),
                 "due": (now + timedelta(days=PREDICTION_DUE_DAYS)).strftime("%Y-%m-%d"),
                 "status": "open"}
            preds.append(p)
            seen.append(t)
            added.append(p)
        if added:
            _save(PREDICTIONS_FILE, store)
    except Exception as e:
        print(f"[PREDICTIONS] record failed: {e}")
    return added


VALID = {"happened", "partly", "did_not_happen", "unclear"}

REVIEW_PROMPT = """You are checking earlier forecasts against what has since been reported. For each prediction, decide from the recent headlines only whether it "happened", "partly" happened, "did_not_happen", or is "unclear" (headlines give no evidence either way). Do not guess: use "unclear" when the headlines are silent.

PREDICTIONS:
{preds}

HEADLINES FROM THE LAST 3 DAYS:
{heads}

Return ONLY JSON: {{"results": [{{"id": "<prediction id>", "status": "happened|partly|did_not_happen|unclear", "evidence": "one short line"}}]}}"""


def review_predictions(now=None, llm_module=None, recent_story_titles=None) -> list:
    """Resolve open predictions older than 7 days with one batched LLM call.
    Returns the list of newly resolved predictions. Never raises."""
    now = now or datetime.now()
    try:
        if llm_module is None:
            return []
        store = _load_preds()
        if _prune_resolved(store, now):
            _save(PREDICTIONS_FILE, store)
        cutoff = _day(now, REVIEW_AFTER_DAYS)
        due = [p for p in store["predictions"] if p.get("status") == "open" and p.get("created", "9") <= cutoff]
        if not due:
            return []
        # never-reviewed first, then oldest first
        due.sort(key=lambda p: (bool(p.get("reviewed")), p.get("created", "")))
        due = due[:MAX_REVIEW]
        heads = [str(t) for t in (recent_story_titles or []) if t][:MAX_HEADLINES]
        if not heads:
            return []
        plist = "\n".join(f'- id={p["id"]} (made {p["created"]}): IF {p["if"]} THEN {p["then"]}'
                          f' (signpost: {p.get("signpost", "")})' for p in due)
        out = llm_module.generate_json(
            REVIEW_PROMPT.format(preds=plist, heads="\n".join(f"- {h}" for h in heads)),
            label="PREDICT", require="results", max_tokens=2048, temperature=0.1, budget_s=90)
        results = out.get("results") if isinstance(out, dict) else None
        if not isinstance(results, list):
            return []
        by_id = {p["id"]: p for p in due}
        today = now.strftime("%Y-%m-%d")
        resolved = []
        for r in results:
            if not isinstance(r, dict):
                continue
            p = by_id.get(r.get("id"))
            status = r.get("status")
            if p is None or status not in VALID:
                continue
            p["evidence"] = str(r.get("evidence", ""))[:300]
            p["reviewed"] = today
            if status == "unclear":
                if p.get("due", "9999") < today:   # past due and still no evidence: close it
                    p["status"] = "unclear"
                    p["resolved"] = today
                    resolved.append(p)
                continue
            p["status"] = status
            p["resolved"] = today
            resolved.append(p)
        if results:
            _save(PREDICTIONS_FILE, store)
        return resolved
    except Exception as e:
        print(f"[PREDICTIONS] review failed: {e}")
        return []


def accuracy_summary() -> dict:
    out = {"resolved": 0, "happened": 0, "partly": 0, "missed": 0, "hit_rate": None}
    try:
        for p in _load_preds()["predictions"]:
            s = p.get("status")
            if s == "happened":
                out["happened"] += 1
            elif s == "partly":
                out["partly"] += 1
            elif s == "did_not_happen":
                out["missed"] += 1
        out["resolved"] = out["happened"] + out["partly"] + out["missed"]
        if out["resolved"]:
            out["hit_rate"] = round((out["happened"] + 0.5 * out["partly"]) / out["resolved"], 3)
    except Exception:
        pass
    return out
