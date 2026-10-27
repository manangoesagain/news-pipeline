"""
Ledger of the digest's own calls, graded against later prices, for the self-learning loop.

Files (all written through learning_io, so nothing leaves LEARN_DIR):
  calls.json          {"calls": [...]}   open and recently graded calls
  calls_archive.json  {"calls": [...]}   graded calls older than ARCHIVE_DAYS (bounded)
  scorecard.json      the latest scorecard

Dates: "made", "due" and every "as of" are calendar dates in America/Toronto. A timezone-aware `now`
is converted to Toronto; a naive `now` is taken to already be Toronto time.

Independence rule (keeps samples honest): one idea counts once per horizon window. A new call is skipped
when a call with the same (kind, subject, direction) is still open, or was made less than its own
horizon_days ago (graded or not), so daily re-statements of one idea with overlapping windows never
inflate the sample. Price-tied calls: `direction` is the digest's market-node outlook (digest.py nodes:
"direction: up|down|flat|none" = where the node's market price is expected to go; only up/down are
recorded) and is stored as `expect` ("up"/"down" for the instrument price).

Scoring: ONE function, score_call(call): right=1, partial=0.5, wrong=0; flat, unclear and open calls
are not scored (None). hit_rate, every by_* bucket and the recent windows all use it.

Every public function catches everything, prints one [LEARN] line on failure and returns a safe
default, so the digest never breaks over the ledger. No LLM is called here.
"""

import functools
import json
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import learning_io
import market_history

CALLS_FILE = os.path.join(learning_io.LEARN_DIR, "calls.json")
ARCHIVE_FILE = os.path.join(learning_io.LEARN_DIR, "calls_archive.json")
SCORECARD_FILE = os.path.join(learning_io.LEARN_DIR, "scorecard.json")

MAX_NEW_PER_DAY = 60
ARCHIVE_DAYS = 120
SCENARIO_HORIZON = 14
FLAT_PCT = 0.3            # default flat band (percent); see flat_threshold() for per-instrument values
ET = ZoneInfo("America/Toronto")
_MISSING = object()
DUE_WINDOW = 2            # price must be within +-2 days of the due date
UNCLEAR_AFTER = 3         # price-tied call with no usable price is closed this many days after due
SOFT_UNCLEAR_AFTER = 21   # unmatched soft call is closed this many days after due
MIN_ENOUGH = 20
SAFE_BYTES = 300_000      # stay well under learning_io.MAX_FILE_BYTES so JSON is never truncated
TEXT_MAX = 160

INSTRUMENT = {"INR": "USD/INR", "CAD": "USD/CAD", "USD": "US Dollar Index"}
_SIZE_RANK = {"big": 0, "medium": 1, "small": 2}


# ---------------------------------------------------------------- plumbing

def _safe(default):
    """Never raise: print one [LEARN] line and return default() (a callable) on any failure."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except _Corrupt:
                return default()  # already reported once by _quarantine
            except Exception as e:  # noqa: BLE001 - the digest must never break over learning
                try:
                    print(f"[LEARN] ledger {fn.__name__} failed: {type(e).__name__}: {e}")
                except Exception:
                    pass
                return default()
        return wrapper
    return deco


def _now(now):
    """Naive datetime in America/Toronto. Aware datetimes are converted; naive ones are taken as Toronto;
    a date or 'YYYY-MM-DD' string means midday that day; anything else means right now."""
    if isinstance(now, datetime):
        return now.astimezone(ET).replace(tzinfo=None) if now.tzinfo else now
    if isinstance(now, date):
        return datetime(now.year, now.month, now.day, 12)
    if isinstance(now, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", now.strip()):
        return _pdate(now) + timedelta(hours=12)
    return datetime.now(ET).replace(tzinfo=None)


def _day(d):
    return d.strftime("%Y-%m-%d")


def _pdate(s):
    return datetime.strptime(str(s)[:10], "%Y-%m-%d")


class _Corrupt(Exception):
    """calls.json exists but cannot be read as a ledger; it must not be overwritten this run."""


def _load_store(path, strict=False):
    """Missing file -> empty store. Unreadable/odd file -> empty store, or with strict=True a backup is made
    (learning_io.backup_corrupt when available), one [LEARN] line is printed and _Corrupt is raised so the
    caller skips writing."""
    d = learning_io.load_json(path, _MISSING)
    if d is _MISSING:
        if strict and os.path.exists(path):
            _quarantine(path)
        return {"calls": []}
    if not isinstance(d, dict) or not isinstance(d.get("calls"), list):
        if strict:
            _quarantine(path)
        return {"calls": []}
    d["calls"] = [c for c in d["calls"] if isinstance(c, dict) and c.get("id")]
    return d


def _quarantine(path):
    backed = False
    fn = getattr(learning_io, "backup_corrupt", None)
    if callable(fn):
        try:
            fn(path)
            backed = True
        except Exception as e:  # noqa: BLE001
            print(f"[LEARN] ledger could not back up {os.path.basename(path)}: {e}")
    print(f"[LEARN] ledger {os.path.basename(path)} is unreadable ({'backed up' if backed else 'no backup'}); "
          f"leaving it untouched and skipping ledger writes this run")
    raise _Corrupt(path)


def _fit(calls):
    """Return (kept, dropped) so the serialized store stays under SAFE_BYTES. Oldest graded go first."""
    size = len(json.dumps({"calls": calls}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size <= SAFE_BYTES:
        return calls, []
    order = sorted(range(len(calls)), key=lambda i: (calls[i].get("status") == "open",
                                                     str(calls[i].get("made", ""))))
    drop = set()
    for i in order:
        if size <= SAFE_BYTES:
            break
        size -= len(json.dumps(calls[i], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        drop.add(i)
    return ([c for i, c in enumerate(calls) if i not in drop],
            [c for i, c in enumerate(calls) if i in drop])


def _save_all(store, now):
    """Archive old graded calls, enforce size bounds, write both files via learning_io."""
    calls = store["calls"]
    cut = _day(now - timedelta(days=ARCHIVE_DAYS))
    old = [c for c in calls if c.get("status") != "open" and str(c.get("made", "9999"))[:10] < cut]
    keep = [c for c in calls if c not in old]
    keep, overflow = _fit(keep)
    to_archive = old + [c for c in overflow if c.get("status") != "open"]
    if to_archive:
        arch = _load_store(ARCHIVE_FILE, strict=True)
        have = {c["id"] for c in arch["calls"]}
        arch["calls"].extend(c for c in to_archive if c["id"] not in have)
        arch["calls"], _ = _fit(arch["calls"])
        learning_io.safe_write_json(ARCHIVE_FILE, arch)
    learning_io.safe_write_json(CALLS_FILE, {"calls": keep})
    store["calls"] = keep


# ---------------------------------------------------------------- thresholds and scoring

def flat_threshold(instrument, horizon_days=5):
    """Percent move below which a price call is 'flat' (not graded). Instrument-aware, x1.5 beyond 5 days."""
    i = str(instrument or "").lower()
    if "usd/inr" in i or "usd/cad" in i:
        t = 0.15
    elif "dollar index" in i or i in ("dxy", "usd index"):
        t = 0.25
    elif any(k in i for k in ("btc", "bitcoin", "ethereum", "crypto")):
        t = 2.0
    elif any(k in i for k in ("wti", "brent", "crude", "oil", "gold", "silver")):
        t = 0.8
    elif any(k in i for k in ("s&p", "nasdaq", "dow", "nifty", "sensex", "tsx", "ftse", "nikkei",
                              "hang seng", "dax", "index")):
        t = 0.5
    else:
        t = FLAT_PCT
    try:
        if int(horizon_days) > 5:
            t *= 1.5
    except Exception:
        pass
    return round(t, 4)


_SCORES = {"right": 1.0, "partial": 0.5, "wrong": 0.0}


def score_call(call):
    """THE scoring rule: right=1, partial=0.5, wrong=0 for a graded call; None (excluded) for flat,
    unclear, open or malformed calls. Reuse this everywhere a hit rate is computed."""
    try:
        if not isinstance(call, dict) or call.get("status") != "graded":
            return None
        g = call.get("grade")
        return _SCORES.get(g.get("result")) if isinstance(g, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------- parsing helpers

def _price(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            f = float(v)
        else:
            s = re.sub(r"[^0-9.\-]", "", str(v).replace(",", ""))
            f = float(s)
        return f if f > 0 and f == f and f != float("inf") else None
    except Exception:
        return None


def _horizon(when, default=5):
    w = str(when or "").lower()
    if w.startswith("day"):
        return 3
    if w.startswith("week"):
        return 10
    return default


def _conf(v):
    if isinstance(v, bool):
        return "high" if v else "low"
    if isinstance(v, (int, float)):
        x = float(v) / 100.0 if v > 1 else float(v)
        return "high" if x >= 0.7 else ("medium" if x >= 0.4 else "low")
    s = str(v or "").strip().lower()
    if not s:
        return "medium"
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s) or re.fullmatch(r"(\d+(?:\.\d+)?)", s)
    if m:
        return _conf(float(m.group(1)))
    if "low" in s or "unlikely" in s:
        return "low"
    if "high" in s or "likely" in s or "very" in s or "sure" in s:
        return "high"
    return "medium"


def _size(v):
    s = str(v or "").lower()
    return s if s in _SIZE_RANK else "medium"


def _clip(v, n=TEXT_MAX):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())[:14] or "x"


def _expect_sign(instrument, direction):
    """+1 if the instrument's price should rise for this call, -1 if it should fall, None if unknown."""
    d = str(direction)
    if d == "up":
        return 1
    if d == "down":
        return -1
    if d in ("stronger", "weaker"):
        usd_per_local = instrument in ("USD/INR", "USD/CAD")
        stronger = d == "stronger"
        return (-1 if stronger else 1) if usd_per_local else (1 if stronger else -1)
    return None


def _history_points(label):
    pts = []
    for h in market_history._load():
        try:
            p = _price((h.get("labels") or {}).get(label))
            if p is not None:
                pts.append((_pdate(h["date"]), p))
        except Exception:
            continue
    pts.sort(key=lambda t: t[0])
    return pts


def _market_prices(graph):
    out = {}
    for r in (graph.get("market") or []) if isinstance(graph, dict) else []:
        if isinstance(r, dict) and r.get("label"):
            p = _price(r.get("price"))
            if p is not None:
                out[str(r["label"])] = p
    return out


def _baseline(label, prices, now):
    if label in prices:
        return prices[label]
    pts = [p for d, p in _history_points(label) if d <= _pdate(_day(now))]
    return pts[-1] if pts else None


def _link_type(graph, nid, node_label, kind_word):
    """'<source label>-><kind>' from the node's strongest incoming edge, else ''."""
    try:
        nodes = {n.get("id"): n for n in graph.get("nodes") or [] if isinstance(n, dict)}
        ins = [e for e in graph.get("edges") or [] if isinstance(e, dict) and e.get("to") == nid]
        if not ins:
            return ""
        best = max(ins, key=lambda e: (e.get("strength") or 0) if isinstance(e.get("strength"), (int, float)) else 0)
        src = nodes.get(best.get("from")) or {}
        words = re.sub(r"[^a-z0-9 ]+", " ", str(src.get("label", "")).lower()).split()[:2]
        return ("%s->%s" % (" ".join(words), kind_word))[:40] if words else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- recording

class _Adder:
    def __init__(self, store, now):
        self.now = now
        self.day = _day(now)
        self.ymd = now.strftime("%Y%m%d")
        self.calls = store["calls"]
        self.ids = {c["id"] for c in self.calls}
        self.today_n = 0
        self.seen = {}   # (kind, subject, direction) -> [(made date, status)]
        for c in self.calls:
            try:
                made = _pdate(c.get("made"))
            except Exception:
                continue
            if str(c.get("made", ""))[:10] == self.day:
                self.today_n += 1
            self.seen.setdefault((c.get("kind"), c.get("subject"), c.get("direction")), []).append(
                (made, c.get("status")))
        self.new = []

    def _blocked(self, key, horizon):
        """Independence: same idea still open, or made inside the last `horizon` days, is not re-added."""
        today = _pdate(self.day)
        for made, status in self.seen.get(key, ()):
            if status == "open" or (today - made).days < int(horizon):
                return True
        return False

    def add(self, kind, subject, text, direction, horizon, confidence, size, topic, link_type,
            source, instrument, baseline, extra=None):
        subject = _clip(subject, 120)
        key = (kind, subject, direction)
        if not subject or self._blocked(key, horizon) or self.today_n >= MAX_NEW_PER_DAY:
            return
        prefix = "c-%s-%s%s-" % (self.ymd, kind[0], _slug(subject))
        n = 1 + sum(1 for i in self.ids if i.startswith(prefix))
        cid = "%s%d" % (prefix, n)
        rec = {"id": cid, "made": self.now.isoformat(timespec="seconds"), "kind": kind,
               "subject": subject, "text": _clip(text), "direction": direction,
               "horizon_days": int(horizon),
               "due": _day(self.now + timedelta(days=int(horizon))),
               "confidence": confidence, "size": size, "topic": _clip(topic, 40),
               "link_type": _clip(link_type, 40), "source": _clip(source, 60),
               "instrument": instrument, "baseline_price": baseline,
               "status": "open", "grade": None}
        if instrument:
            # for market calls `direction` is the node's market outlook (digest nodes: up|down|flat|none)
            rec["expect"] = "up" if _expect_sign(instrument, direction) == 1 else "down"
        if extra:
            rec.update(extra)
        self.seen.setdefault(key, []).append((_pdate(self.day), "open"))
        self.ids.add(cid)
        self.calls.append(rec)
        self.new.append(rec)
        self.today_n += 1


def _node_order(nodes):
    def k(n):
        imp = n.get("impact") if isinstance(n.get("impact"), dict) else {}
        top = imp.get("top5") if isinstance(imp.get("top5"), int) and imp.get("top5") else 99
        return (_SIZE_RANK.get(str(imp.get("size")), 3), top)
    return sorted(nodes, key=k)


@_safe(list)
def record_calls(graph, now=None):
    """Turn the graph's full cards into open calls. Returns the newly added call records."""
    now = _now(now)
    if not isinstance(graph, dict):
        return []
    nodes = [n for n in graph.get("nodes") or []
             if isinstance(n, dict) and isinstance(n.get("card"), dict) and not n["card"].get("light")]
    store = _load_store(CALLS_FILE, strict=True)
    adder = _Adder(store, now)
    prices = _market_prices(graph)
    ordered = _node_order(nodes)

    # price-tied calls first so the daily cap never crowds them out for soft ones
    for n in ordered:
        imp = n.get("impact") if isinstance(n.get("impact"), dict) else {}
        hor = _horizon(imp.get("when"))
        conf, size = _conf(imp.get("sure")), _size(imp.get("size"))
        label = _clip(n.get("label"), 60)
        topic = str(n.get("domain") or "")
        cur = n["card"].get("currency") if isinstance(n["card"].get("currency"), dict) else {}
        for ccy in ("INR", "CAD", "USD"):
            c = cur.get(ccy)
            d = str(c.get("dir", "")).lower() if isinstance(c, dict) else ""
            if d not in ("stronger", "weaker"):
                continue
            inst = INSTRUMENT[ccy]
            adder.add("currency", ccy, "%s %s: %s" % (ccy, d, _clip(c.get("why"), 100) or label), d, hor,
                      conf, size, topic, _link_type(graph, n.get("id"), label, "currency"),
                      "card:" + label, inst, _baseline(inst, prices, now))
        m = n.get("market") if isinstance(n.get("market"), dict) else None
        d = str(n.get("direction", "")).lower()
        if m and m.get("label") and d in ("up", "down"):
            inst = _clip(m["label"], 60)
            base = _price(m.get("price")) or _baseline(inst, prices, now)
            adder.add("market", inst, "%s to go %s" % (inst, d), d, hor, conf, size, topic,
                      _link_type(graph, n.get("id"), label, _slug(inst)), "market-node", inst, base,
                      {"basis": "moved_today"} if (str(n.get("type")) == "index"
                                                   and " moved " in str(n.get("detail", ""))) else None)

    for n in ordered:
        imp = n.get("impact") if isinstance(n.get("impact"), dict) else {}
        label = _clip(n.get("label"), 60)
        preds = n["card"].get("predictions")
        for p in (preds if isinstance(preds, list) else []):
            if not isinstance(p, dict) or not p.get("if") or not p.get("then"):
                continue
            ph = str(p.get("horizon") or "").lower()
            hor = _horizon(ph, _horizon(imp.get("when")))
            adder.add("prediction", "IF %s THEN %s" % (_clip(p["if"], 80), _clip(p["then"], 80)),
                      "If %s then %s" % (_clip(p["if"], 70), _clip(p["then"], 70)), "", hor,
                      _conf(p.get("odds")), _size(imp.get("size")), str(n.get("domain") or ""),
                      _link_type(graph, n.get("id"), label, "prediction"), "card:" + label, "", None,
                      {"if": _clip(p["if"], 200), "then": _clip(p["then"], 200)})
    if adder.new:
        _save_all(store, now)
    return adder.new


@_safe(list)
def record_scenarios(scenarios, now=None):
    """The 6PM intel scenarios as soft 14-day prediction calls."""
    now = _now(now)
    store = _load_store(CALLS_FILE, strict=True)
    adder = _Adder(store, now)
    for s in scenarios if isinstance(scenarios, list) else []:
        if not isinstance(s, dict) or not s.get("if") or not s.get("then"):
            continue
        adder.add("prediction", "IF %s THEN %s" % (_clip(s["if"], 80), _clip(s["then"], 80)),
                  "If %s then %s" % (_clip(s["if"], 70), _clip(s["then"], 70)), "", SCENARIO_HORIZON,
                  _conf(s.get("likelihood")), "medium", "", "", "scenario", "", None,
                  {"if": _clip(s["if"], 200), "then": _clip(s["then"], 200)})
    if adder.new:
        _save_all(store, now)
    return adder.new


# ---------------------------------------------------------------- grading

def _size_actual(pct):
    a = abs(pct)
    return "small" if a < 0.5 else ("medium" if a < 1.5 else "big")


def _price_near(pts, target, lo_date):
    """Point on/after lo_date, nearest target, within DUE_WINDOW days. None if there is none."""
    best = None
    for d, p in pts:
        if d < lo_date:
            continue
        gap = abs((d - target).days)
        if gap <= DUE_WINDOW and (best is None or gap < best[0] or (gap == best[0] and d < best[1])):
            best = (gap, d, p)
    return best


def _grade_price(c, today, cache):
    inst = c.get("instrument")
    made, due = _pdate(c["made"]), _pdate(c["due"])
    pts = cache.setdefault(inst, _history_points(inst))
    base = _price(c.get("baseline_price"))
    if base is None:  # fall back to the history price on/before the day the call was made
        prior = [p for d, p in pts if d <= made]
        base = prior[-1] if prior else None
    hit = _price_near(pts, due, made + timedelta(days=1))
    # wait until the +-2 day window has fully passed unless the exact due date has a price
    if hit is None or (hit[0] > 0 and today < due + timedelta(days=DUE_WINDOW)):
        if today > due + timedelta(days=UNCLEAR_AFTER):
            return "unclear", {"result": "unclear", "reason": "no price near the due date", "soft": False}
        return None, None
    if base is None:
        if today > due + timedelta(days=UNCLEAR_AFTER):
            return "unclear", {"result": "unclear", "reason": "no starting price", "soft": False}
        return None, None
    _, pdate, pdue = hit
    pct = (pdue / base - 1.0) * 100.0
    sign = _expect_sign(inst, c.get("direction"))
    if sign is None:
        return "unclear", {"result": "unclear", "reason": "unknown direction", "soft": False}
    flat = flat_threshold(inst, c.get("horizon_days") or (due - made).days)
    if abs(pct) < flat:
        result = "flat"
    else:
        result = "right" if (pct > 0) == (sign > 0) else "wrong"
    sa = _size_actual(pct)
    # persistence baseline: the move over the same-length window before the call continues
    length = max(1, (due - made).days)
    bright = None
    pb = _price_near(pts, made - timedelta(days=length), datetime.min)
    if pb is not None and pb[1] < made and result != "flat" and pb[2]:
        prev = (base / pb[2] - 1.0) * 100.0
        if abs(prev) >= flat:
            bright = (prev > 0) == (pct > 0)
    grade = {"result": result, "at": _day(today), "price_date": _day(pdate), "price_due": round(pdue, 4),
             "actual_change_pct": round(pct, 3), "size_actual": sa, "size_matched": sa == c.get("size"),
             "baseline_right": bright, "soft": False}
    return "graded", grade


_SOFT = {"happened": "right", "partly": "partial", "did_not_happen": "wrong", "unclear": "unclear"}


def _soft_matches():
    import patterns
    out = []
    for p in patterns._load_preds()["predictions"]:
        if isinstance(p, dict) and p.get("status") in _SOFT:
            out.append((patterns._ptoks(p), p))
    return out


def _grade_soft(c, today, resolved):
    import patterns
    if resolved is None:
        resolved = []
    toks = patterns._ptoks({"if": c.get("if", ""), "then": c.get("then", "")})
    for t, p in resolved:
        if patterns._similar(toks, t):
            res = _SOFT[p["status"]]
            return ("unclear" if res == "unclear" else "graded"), {
                "result": res, "at": _day(today), "soft": True, "evidence": _clip(p.get("evidence"), 200)}
    if today > _pdate(c["due"]) + timedelta(days=SOFT_UNCLEAR_AFTER):
        return "unclear", {"result": "unclear", "reason": "never resolved", "at": _day(today), "soft": True}
    return None, None


@_safe(list)
def grade_calls(now=None):
    """Grade open calls that are due. Returns the newly graded (or closed-unclear) calls."""
    now = _now(now)
    today = _pdate(_day(now))
    store = _load_store(CALLS_FILE, strict=True)
    cache, newly = {}, []
    resolved = None
    for c in store["calls"]:
        if c.get("status") != "open":
            continue
        try:
            if _pdate(c["due"]) > today:
                continue
            if c.get("instrument"):
                status, grade = _grade_price(c, today, cache)
            else:
                if resolved is None:
                    try:
                        resolved = _soft_matches()
                    except Exception as e:
                        print(f"[LEARN] ledger soft grading unavailable: {e}")
                        resolved = []
                status, grade = _grade_soft(c, today, resolved)
            if status:
                grade.setdefault("at", _day(today))
                c["status"], c["grade"] = status, grade
                newly.append(c)
        except Exception as e:
            print(f"[LEARN] ledger could not grade {c.get('id')}: {e}")
    if newly:
        _save_all(store, now)
    return newly


# ---------------------------------------------------------------- scorecard

def _bucket(d, key, s):
    b = d.setdefault(key, {"n": 0, "hits": 0.0})
    b["n"] += 1
    b["hits"] += s


def _finish(d):
    return {k: {"n": v["n"], "hits": v["hits"], "rate": round(v["hits"] / v["n"], 3)}
            for k, v in sorted(d.items()) if v["n"]}


def _rate(hits, n):
    return round(hits / n, 3) if n else None


def _empty_scorecard():
    return {"as_of": "", "n_graded": 0, "n_open": 0, "n_price_graded": 0, "n_scored": 0, "n_soft": 0,
            "n_flat": 0, "n_unclear": 0, "hit_rate": None,
            "hit_rate_baseline": None, "hit_rate_soft": None, "by_confidence": {}, "by_topic": {},
            "by_link_type": {}, "by_kind": {}, "by_instrument": {}, "calibrated": False,
            "enough_data": False, "recent_30d": {"n": 0, "hit_rate": None},
            "prev_30d": {"n": 0, "hit_rate": None}}


@_safe(_empty_scorecard)
def scorecard(now=None, write=True):
    """Scorecard as of the Toronto date of `now` (datetime, date or 'YYYY-MM-DD'). Counts:
    n_graded = calls graded (incl. flat); n_scored = price calls behind hit_rate; n_soft = soft calls behind
    hit_rate_soft; n_flat / n_unclear = excluded from every rate. Buckets and recent windows mix both kinds.
    The file is only rewritten when its content changes."""
    now = _now(now)
    today = _pdate(_day(now))
    calls = _load_store(CALLS_FILE)["calls"]
    sc = _empty_scorecard()
    sc["as_of"] = _day(now)
    conf, topic, link, kind, inst = {}, {}, {}, {}, {}
    hard_h = soft_h = base_h = base_n = 0
    rec = [0.0, 0]
    prev = [0.0, 0]
    for c in calls:
        st = c.get("status")
        if st == "open":
            sc["n_open"] += 1
            continue
        g = c.get("grade") if isinstance(c.get("grade"), dict) else {}
        if st == "graded":
            sc["n_graded"] += 1
            if not g.get("soft"):
                sc["n_price_graded"] += 1
            if g.get("result") == "flat":
                sc["n_flat"] += 1
        if st == "unclear" or g.get("result") == "unclear":
            sc["n_unclear"] += 1
        s = score_call(c)
        if s is None:
            continue
        if g.get("soft"):
            soft_h += s
            sc["n_soft"] += 1
        else:
            hard_h += s
            sc["n_scored"] += 1
            if g.get("baseline_right") is not None:
                base_h += 1.0 if g["baseline_right"] else 0.0
                base_n += 1
        _bucket(conf, c.get("confidence") or "medium", s)
        _bucket(topic, c.get("topic") or "other", s)
        if c.get("link_type"):
            _bucket(link, c["link_type"], s)
        _bucket(kind, c.get("kind") or "other", s)
        if c.get("instrument"):
            _bucket(inst, c["instrument"], s)
        try:
            age = (today - _pdate(g.get("at") or c.get("due"))).days
            if 0 <= age < 30:
                rec[0] += s
                rec[1] += 1
            elif 30 <= age < 60:
                prev[0] += s
                prev[1] += 1
        except Exception:
            pass
    sc["hit_rate"] = _rate(hard_h, sc["n_scored"])
    sc["hit_rate_baseline"] = _rate(base_h, base_n)
    sc["hit_rate_soft"] = _rate(soft_h, sc["n_soft"])
    sc["by_confidence"] = _finish(conf)
    sc["by_topic"] = _finish(topic)
    sc["by_link_type"] = _finish(link)
    sc["by_kind"] = _finish(kind)
    sc["by_instrument"] = _finish(inst)
    hi, lo = sc["by_confidence"].get("high"), sc["by_confidence"].get("low")
    sc["calibrated"] = bool(hi and lo and hi["n"] >= 10 and lo["n"] >= 10 and hi["rate"] - lo["rate"] >= 0.05)
    sc["enough_data"] = sc["n_scored"] + sc["n_soft"] >= MIN_ENOUGH
    sc["recent_30d"] = {"n": rec[1], "hit_rate": _rate(rec[0], rec[1])}
    sc["prev_30d"] = {"n": prev[1], "hit_rate": _rate(prev[0], prev[1])}
    if write:
        try:
            old = learning_io.load_json(SCORECARD_FILE, None)
            if old != json.loads(json.dumps(sc)):
                learning_io.safe_write_json(SCORECARD_FILE, sc)
        except Exception as e:
            print(f"[LEARN] ledger could not save scorecard: {e}")
    return sc


@_safe(_empty_scorecard)
def track_record():
    d = learning_io.load_json(SCORECARD_FILE, None)
    if isinstance(d, dict) and "n_graded" in d:
        out = _empty_scorecard()
        out.update(d)
        return out
    return _empty_scorecard()


@_safe(list)
def recent_graded(n=40):
    calls = [c for c in _load_store(CALLS_FILE)["calls"] if c.get("status") == "graded"]
    calls.sort(key=lambda c: (str((c.get("grade") or {}).get("at", "")), str(c.get("made", ""))), reverse=True)
    out = []
    for c in calls[:max(0, int(n))]:
        g = c.get("grade") or {}
        out.append({"id": c["id"], "kind": c.get("kind"), "subject": c.get("subject"),
                    "text": c.get("text"), "direction": c.get("direction"),
                    "confidence": c.get("confidence"), "size": c.get("size"), "topic": c.get("topic"),
                    "link_type": c.get("link_type"), "made": str(c.get("made", ""))[:10],
                    "due": c.get("due"), "result": g.get("result"), "soft": bool(g.get("soft")),
                    "actual_change_pct": g.get("actual_change_pct"), "size_actual": g.get("size_actual"),
                    "size_matched": g.get("size_matched"), "baseline_right": g.get("baseline_right")})
    return out
