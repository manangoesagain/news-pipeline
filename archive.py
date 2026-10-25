#!/usr/bin/env python3
"""
Permanent memory for the digest: every story that was ever shown, grouped by day
(_data/archive/YYYY-MM-DD.json + index.json), and story threads that follow one
topic across days and domains (_data/threads.json). No LLM needed: threads are
matched by headline word overlap, so this never slows a digest down.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta

SAVE_DIR = "_data"
ARCHIVE_DIR = os.path.join(SAVE_DIR, "archive")
INDEX_FILE = os.path.join(ARCHIVE_DIR, "index.json")
THREADS_FILE = os.path.join(SAVE_DIR, "threads.json")

MATCH_MIN_SHARED = 3        # headline words a story must share with a thread
KEYWORDS_PER_THREAD = 14
THREAD_STORY_CAP = 80       # keep the file small; the daily archive has everything
THREAD_IDLE_DAYS = 60       # a thread not seen for this long stops matching new stories
SINGLETON_KEEP_DAYS = 21    # one-story threads older than this are dropped
THREAD_KEEP_DAYS = 120      # any thread idle this long is dropped
WC_CAP = 40

STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "as", "at", "by",
        "with", "from", "after", "over", "says", "say", "new", "its", "it", "be", "has", "have", "will",
        "how", "why", "what", "who", "this", "that", "amid", "into", "than", "more", "not", "but",
        "could", "would", "about", "now", "out", "up", "us", "their", "his", "her", "was", "were",
        "report", "reports", "news", "live", "update", "updates"}


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def tokens(title: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOP and len(w) > 2}


def _compact(s, breaking=False):
    published = s["published"]
    return {
        "title": s["title"],
        "link": s["link"],
        "source": s["source"],
        "sources": s.get("sources") or [s["source"]],
        "domain": s["domain"],
        "region": s["region"],
        "published": published.isoformat() if isinstance(published, datetime) else published,
        "score": s.get("score", 0),
        "why": s.get("why", ""),
        "summary": s.get("summary", ""),
        "breaking": breaking,
    }


def update_archive(domain_items: dict, breaking: list, now: datetime) -> list:
    """Add this digest's stories to today's archive file. Returns the compact stories added."""
    day = now.strftime("%Y-%m-%d")
    path = os.path.join(ARCHIVE_DIR, f"{day}.json")
    data = _load(path, {"date": day, "stories": []})
    by_link = {s["link"]: s for s in data["stories"]}
    slot = now.strftime("%H:%M")
    shown = []
    breaking_links = {b["link"] for b in breaking}
    for s in breaking + [x for items in domain_items.values() for x in items]:
        c = _compact(s, s["link"] in breaking_links)
        old = by_link.get(c["link"])
        if old:
            old["breaking"] = old.get("breaking") or c["breaking"]
            old["sources"] = list(dict.fromkeys(old.get("sources", []) + c["sources"]))
            old["slots"] = sorted(set(old.get("slots", []) + [slot]))
            continue
        c["slots"] = [slot]
        by_link[c["link"]] = c
        shown.append(c)
    data["stories"] = sorted(by_link.values(), key=lambda s: s["published"], reverse=True)
    _save(path, data)

    index = _load(INDEX_FILE, {"days": []})
    days = {d["date"]: d for d in index["days"]}
    days[day] = {"date": day, "count": len(data["stories"])}
    index["days"] = sorted(days.values(), key=lambda d: d["date"], reverse=True)
    index["updated"] = now.isoformat()
    _save(INDEX_FILE, index)
    print(f"[ARCHIVE] {day}: {len(data['stories'])} stories ({len(shown)} new), {len(index['days'])} days kept")
    return shown


def update_threads(stories: list, now: datetime) -> dict:
    """Attach stories to the best-matching thread, or start a new one."""
    store = _load(THREADS_FILE, {"threads": []})
    threads = store["threads"]
    cutoff = (now - timedelta(days=THREAD_IDLE_DAYS)).isoformat()
    active = [t for t in threads if t["last_seen"] >= cutoff]
    next_id = max([t["id"] for t in threads], default=0) + 1
    touched = set()

    for s in sorted(stories, key=lambda x: x["published"]):
        toks = tokens(s["title"])
        if len(toks) < 3:
            continue
        best, best_shared = None, 0
        for t in active:
            if any(x["link"] == s["link"] for x in t["stories"]):
                best, best_shared = t, 99
                break
            shared = len(toks & set(t["keywords"]))
            if shared > best_shared:
                best, best_shared = t, shared
        entry = {"title": s["title"], "link": s["link"], "source": s["source"], "domain": s["domain"],
                 "region": s["region"], "date": s["published"][:10]}
        if best is not None and best_shared >= MATCH_MIN_SHARED:
            t = best
            if best_shared == 99:
                continue  # already in this thread
        else:
            t = {"id": next_id, "title": s["title"], "keywords": [], "domains": [], "stories": [],
                 "first_seen": s["published"], "last_seen": s["published"], "wc": {}}
            next_id += 1
            threads.append(t)
            active.append(t)
        t["stories"].append(entry)
        t["stories"] = t["stories"][-THREAD_STORY_CAP:]
        t["last_seen"] = max(t["last_seen"], s["published"])
        t["first_seen"] = min(t["first_seen"], s["published"])
        for w in toks:
            t["wc"][w] = t["wc"].get(w, 0) + 1
        t["keywords"] = [w for w, _ in Counter(t["wc"]).most_common(KEYWORDS_PER_THREAD)]
        if len(t["wc"]) > WC_CAP:
            t["wc"] = dict(Counter(t["wc"]).most_common(WC_CAP))
        t["domains"] = sorted({x["domain"] for x in t["stories"]})
        touched.add(t["id"])

    single_cut = (now - timedelta(days=SINGLETON_KEEP_DAYS)).isoformat()
    keep_cut = (now - timedelta(days=THREAD_KEEP_DAYS)).isoformat()
    threads = [t for t in threads if t["last_seen"] >= keep_cut and (len(t["stories"]) > 1 or t["last_seen"] >= single_cut)]
    threads.sort(key=lambda t: t["last_seen"], reverse=True)
    store["threads"] = threads
    store["updated"] = now.isoformat()
    _save(THREADS_FILE, store)
    multi = sum(1 for t in threads if len(t["stories"]) >= 2)
    print(f"[THREADS] {len(touched)} touched, {multi} threads with 2+ stories, {len(threads)} total")
    return store


def backfill():
    """Ingest saved digest_*.json files not yet in the archive (idempotent)."""
    import glob
    for path in sorted(glob.glob(os.path.join(SAVE_DIR, "digest_*.json"))):
        d = _load(path, None)
        if not d or "domains" not in d:
            continue
        when = datetime.fromisoformat(d["datetime"])
        added = update_archive(d["domains"], d.get("breaking", []), when)
        if added:
            update_threads(added, when)


if __name__ == "__main__":
    backfill()
