"""
Ledger of the digest's own calls, graded against later prices, for the self-learning loop.

Files (all written through learning_io, so nothing leaves LEARN_DIR):
  calls.json          {"calls": [...]}   open and recently graded calls
  calls_archive.json  {"calls": [...]}   graded calls older than ARCHIVE_DAYS (bounded)
  outcomes.json       compact rows of every graded price call (see "compact outcomes store" below)
  scorecard.json      the latest scorecard

Dates: "made", "due" and every "as of" are calendar dates in America/Toronto. A timezone-aware `now`
is converted to Toronto; a naive `now` is taken to already be Toronto time.

Independence rule (keeps samples honest): one idea counts once per horizon window. A new call is skipped
when a call with the same (kind, subject, direction) is still open, or was made less than its own
horizon_days ago (graded or not), so daily re-statements of one idea with overlapping windows never
inflate the sample. Price-tied calls: `direction` is the digest's market-node outlook (digest.py nodes:
"direction: up|down|flat|none" = where the node's market price is expected to go; only up/down are
recorded) and is stored as `expect` ("up"/"down" for the instrument price).

Fast feedback: record_next_day_calls() derives 1-day and 3-day price calls for every tracked instrument the
graph implies a direction for (basis node | edge | currency); grade_calls() may run every run and grades a 1-day
call as soon as the next trading day's price is in market_history. A price idea is (instrument, direction of the
price, horizon_days): record_calls and record_next_day_calls share that key, so one idea never lands twice.
Fair scoring: each graded price call carries `difficulty` (|move| / trailing daily vol, capped at 3) and `edge`
(vs the persistence baseline); flat outcomes are never hits and are reported as no_move_share.

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
OUTCOMES_FILE = os.path.join(learning_io.LEARN_DIR, "outcomes.json")

MAX_NEW_PER_DAY = 60      # cap on soft (non price-tied) calls per day
MAX_PRICE_PER_DAY = 60    # separate cap on price-checkable calls per day
ARCHIVE_DAYS = 60
SCENARIO_HORIZON = 14
FLAT_PCT = 0.3            # default flat band (percent); see flat_threshold() for per-instrument values
ET = ZoneInfo("America/Toronto")
_MISSING = object()
DUE_WINDOW = 2            # price must be within +-2 days of the due date
UNCLEAR_AFTER = 3         # price-tied call with no usable price is closed this many days after due
SOFT_UNCLEAR_AFTER = 21   # unmatched soft call is closed this many days after due
MIN_ENOUGH = 20
MIN_ENOUGH_PRICE = 30
MAX_OUTCOME_ROWS = 5000
OUTCOME_BYTES = 370_000     # outcomes.json stays under this (learning_io limit is 400KB)
RECENT_IDS = 600
KEEP_GRADED_DAYS = 3      # calls.json keeps graded PRICE calls only this long (they live on in outcomes.json)
SAFE_BYTES = 300_000      # stay well under learning_io.MAX_FILE_BYTES so JSON is never truncated
TEXT_MAX = 80
VOL_MIN_POINTS = 20       # trailing-vol difficulty needs this many history points
DIFF_CAP = 3.0
WEEKEND_SLACK = 3         # a due date with no price may use the next price within +3 days
_CONF_P = {"low": 0.55, "medium": 0.65, "high": 0.75}
_CONF_RANK = {"low": 0, "medium": 1, "high": 2}
_BASIS_RANK = {"node": 2, "currency": 1, "edge": 0}

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
    synced = False
    try:   # graded price calls move into the compact outcomes store before calls.json forgets them
        ob = _load_outcomes()
        fresh = ob is None
        if fresh:
            ob = _seed_outcomes(calls)
        n = 0 if fresh else _sync_outcomes(ob, calls)
        if n or (fresh and ob["rows"]):
            _write_outcomes(ob)
        synced = True
    except _Corrupt:
        pass   # damaged outcomes.json: already backed up and left alone; keep price calls in calls.json
    except Exception:  # noqa: BLE001 - calls.json keeps the price calls; its own write reports real disk trouble
        pass
    cut = _day(now - timedelta(days=ARCHIVE_DAYS))
    old = [c for c in calls if c.get("status") != "open" and str(c.get("made", "9999"))[:10] < cut]
    oldids = {id(c) for c in old}
    keep = [c for c in calls if id(c) not in oldids]
    if synced:
        tcut = _day(now - timedelta(days=KEEP_GRADED_DAYS))
        keep = [c for c in keep if not (c.get("instrument") and c.get("status") in ("graded", "unclear")
                                        and isinstance(c.get("grade"), dict) and not c["grade"].get("soft")
                                        and str(c["grade"].get("at") or c.get("made", ""))[:10] < tcut)]
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


def _history_points(label, rows=None):
    pts = []
    for h in (market_history._load() if rows is None else rows):
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

def _prior(inst, exp_dir, horizon, cache):
    """prior_p from learning_priors when available (lazy import, any failure = None)."""
    k = (inst, exp_dir, horizon)
    if k in cache:
        return cache[k]
    v = None
    try:
        import learning_priors
        r = learning_priors.prior_prob(inst, exp_dir, horizon)
        p = r[0] if isinstance(r, (tuple, list)) and r else r
        if isinstance(p, (int, float)) and not isinstance(p, bool) and 0.0 < float(p) < 1.0:
            v = round(float(p), 4)
    except Exception:
        v = None
    cache[k] = v
    return v


def _call_sign(c):
    e = c.get("expect")
    if e in ("up", "down"):
        return 1 if e == "up" else -1
    return _expect_sign(c.get("instrument"), c.get("direction"))


class _Adder:
    def __init__(self, store, now, arm=None):
        self.now = now
        self.day = _day(now)
        self.ymd = now.strftime("%Y%m%d")
        self.arm = arm if arm is None or isinstance(arm, (str, int)) else str(arm)
        self.calls = store["calls"]
        self.ids = {c["id"] for c in self.calls}
        self.soft_n = 0
        self.price_n = 0
        self.seen = {}   # soft: (kind, subject, direction); price: (instrument, sign) -> [(made, status, horizon)]
        self._priors = {}
        for c in self.calls:
            try:
                made = _pdate(c.get("made"))
            except Exception:
                continue
            today = str(c.get("made", ""))[:10] == self.day
            if c.get("instrument"):
                self.price_n += today
                key = (c.get("instrument"), _call_sign(c))
            else:
                self.soft_n += today
                key = (c.get("kind"), c.get("subject"), c.get("direction"))
            self.seen.setdefault(key, []).append((made, c.get("status"), c.get("horizon_days")))
        self.new = []

    def _blocked(self, key, horizon, price):
        """Independence: same idea still open, or made inside the last `horizon` days, is not re-added.
        Price ideas only compare with the same horizon; a 1-day idea may be re-recorded once per day."""
        today = _pdate(self.day)
        for made, status, h in self.seen.get(key, ()):
            if price:
                if h != int(horizon):
                    continue
                if int(horizon) <= 1:
                    if made >= today:
                        return True
                    continue
            if status == "open" or (today - made).days < int(horizon):
                return True
        return False

    def add(self, kind, subject, text, direction, horizon, confidence, size, topic, link_type,
            source, instrument, baseline, extra=None):
        subject = _clip(subject, 120)
        price = bool(instrument)
        if price:
            sign = _expect_sign(instrument, direction)
            if sign is None:
                return
            key = (instrument, sign)
            if self.price_n >= MAX_PRICE_PER_DAY:
                return
        else:
            key = (kind, subject, direction)
            if self.soft_n >= MAX_NEW_PER_DAY:
                return
        if not subject or self._blocked(key, horizon, price):
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
        if price:
            # for market calls `direction` is the node's market outlook (digest nodes: up|down|flat|none)
            rec["expect"] = "up" if sign == 1 else "down"
            rec["prior_p"] = _prior(instrument, rec["expect"], int(horizon), self._priors)
            rec["arm"] = self.arm
        elif self.arm is not None:
            rec["arm"] = self.arm
        if extra:
            rec.update(extra)
        self.seen.setdefault(key, []).append((_pdate(self.day), "open", int(horizon)))
        self.ids.add(cid)
        self.calls.append(rec)
        self.new.append(rec)
        if price:
            self.price_n += 1
        else:
            self.soft_n += 1


def _node_order(nodes):
    def k(n):
        imp = n.get("impact") if isinstance(n.get("impact"), dict) else {}
        top = imp.get("top5") if isinstance(imp.get("top5"), int) and imp.get("top5") else 99
        return (_SIZE_RANK.get(str(imp.get("size")), 3), top)
    return sorted(nodes, key=k)


def _node_arm(n, default=None):
    """Experiment arm of a hub node (digest.py sets node['arm']); falls back to `default`."""
    a = n.get("arm") if isinstance(n, dict) else None
    return a if isinstance(a, (str, int)) and not isinstance(a, bool) and a != "" else default


@_safe(list)
def record_calls(graph, now=None, arm=None):
    """Turn the graph's full cards into open calls. Returns the newly added call records.
    `arm` (optional experiment arm id) is copied onto every new call."""
    now = _now(now)
    if not isinstance(graph, dict):
        return []
    nodes = [n for n in graph.get("nodes") or []
             if isinstance(n, dict) and isinstance(n.get("card"), dict) and not n["card"].get("light")]
    store = _load_store(CALLS_FILE, strict=True)
    adder = _Adder(store, now, arm)
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
                      "card:" + label, inst, _baseline(inst, prices, now),
                      {"basis": "currency", "arm": _node_arm(n, adder.arm)})
        m = n.get("market") if isinstance(n.get("market"), dict) else None
        d = str(n.get("direction", "")).lower()
        if m and m.get("label") and d in ("up", "down"):
            inst = _clip(m["label"], 60)
            base = _price(m.get("price")) or _baseline(inst, prices, now)
            adder.add("market", inst, "%s to go %s" % (inst, d), d, hor, conf, size, topic,
                      _link_type(graph, n.get("id"), label, _slug(inst)), "market-node", inst, base,
                      {"basis": "moved_today" if (str(n.get("type")) == "index"
                                                  and " moved " in str(n.get("detail", ""))) else "node",
                       "arm": _node_arm(n, adder.arm)})

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
                      {"if": _clip(p["if"], 200), "then": _clip(p["then"], 200),
                       "arm": _node_arm(n, adder.arm)})
    if adder.new:
        _save_all(store, now)
    return adder.new


def _market_rows(snapshot):
    if isinstance(snapshot, dict):
        snapshot = snapshot.get("market") or snapshot.get("rows")
    out = {}
    for r in snapshot if isinstance(snapshot, list) else []:
        if isinstance(r, dict) and r.get("label"):
            p = _price(r.get("price"))
            if p is not None:
                out[str(r["label"])] = p
    return out


def _next_day_ideas(graph):
    """{(instrument, sign): [candidate, ...]} for every price idea the graph implies."""
    nodes = {n.get("id"): n for n in graph.get("nodes") or [] if isinstance(n, dict)}
    ideas = {}

    def put(inst, sign, cand):
        ideas.setdefault((inst, sign), []).append(cand)

    for n in nodes.values():
        imp = n.get("impact") if isinstance(n.get("impact"), dict) else {}
        label = _clip(n.get("label"), 60)
        topic = str(n.get("domain") or "")
        conf, size = _conf(imp.get("sure")), _size(imp.get("size"))
        card = n.get("card") if isinstance(n.get("card"), dict) else {}
        cur = card.get("currency") if isinstance(card.get("currency"), dict) else {}
        for ccy in ("INR", "CAD", "USD"):
            c = cur.get(ccy)
            d = str(c.get("dir", "")).lower() if isinstance(c, dict) else ""
            if d not in ("stronger", "weaker"):
                continue
            inst = INSTRUMENT[ccy]
            put(inst, _expect_sign(inst, d), {
                "basis": "currency", "kind": "currency", "subject": ccy, "direction": d, "conf": conf,
                "size": size, "topic": topic, "source": "card:" + label, "node": n.get("id"), "arm": _node_arm(n),
                "text": "%s %s: %s" % (ccy, d, _clip(c.get("why"), 60) or label)})
        m = n.get("market") if isinstance(n.get("market"), dict) else None
        d = str(n.get("direction", "")).lower()
        if m and m.get("label") and d in ("up", "down"):
            inst = _clip(m["label"], 60)
            put(inst, 1 if d == "up" else -1, {
                "basis": "node", "kind": "market", "subject": inst, "direction": d, "conf": conf,
                "size": size, "topic": topic, "source": "market-node", "node": n.get("id"), "arm": _node_arm(n),
                "price": _price(m.get("price")), "text": "%s to go %s" % (inst, d)})
    for e in graph.get("edges") or []:
        if not isinstance(e, dict):
            continue
        t = nodes.get(e.get("to"))
        m = t.get("market") if isinstance(t, dict) and isinstance(t.get("market"), dict) else None
        d = str(e.get("effect", "")).lower()
        if not m or not m.get("label") or d not in ("up", "down"):
            continue
        conf = _conf(e.get("confidence"))
        if str(e.get("status", "")).lower() != "reported" and _CONF_RANK[conf] < 1:
            continue
        imp = t.get("impact") if isinstance(t.get("impact"), dict) else {}
        st = e.get("strength")
        size = _size(imp.get("size")) if imp.get("size") else (
            "big" if st == 3 else ("medium" if st == 2 else "small"))
        inst = _clip(m["label"], 60)
        put(inst, 1 if d == "up" else -1, {
            "basis": "edge", "kind": "market", "subject": inst, "direction": d, "conf": conf, "size": size,
            "topic": str(t.get("domain") or ""), "source": "edge:" + _clip(e.get("label"), 50),
            "node": t.get("id"), "price": _price(m.get("price")),
            # edge arm = arm of the edge's SOURCE node if it has one, else the target's
            "arm": _node_arm(nodes.get(e.get("from")), _node_arm(t)),
            "text": _clip(e.get("label"), 70) or "%s to go %s" % (inst, d)})
    return ideas


@_safe(list)
def record_next_day_calls(graph, snapshot=None, now=None, arm=None):
    """Record 1-day and 3-day price calls for EVERY instrument the graph implies a direction for, from currency
    arrows, market nodes (node['direction']) and effect edges into market nodes (reported, or confidence >=
    medium; mixed/none/flat ignored). One call per (instrument, direction, horizon): the best-supported basis
    wins. Returns the newly added calls. Soft predictions are not touched (see record_calls)."""
    now = _now(now)
    if not isinstance(graph, dict):
        return []
    ideas = _next_day_ideas(graph)
    if not ideas:
        return []
    store = _load_store(CALLS_FILE, strict=True)
    adder = _Adder(store, now, arm)
    prices = _market_prices(graph)
    prices.update(_market_rows(snapshot))
    ranked = []
    for (inst, sign), cands in ideas.items():
        best = max(cands, key=lambda c: (_CONF_RANK[c["conf"]], _BASIS_RANK[c["basis"]]))
        ranked.append(((-_CONF_RANK[best["conf"]], -len(cands), _SIZE_RANK.get(best["size"], 3), inst, sign),
                       inst, sign, best, (inst, -sign) in ideas))
    ranked.sort(key=lambda r: r[0])
    for _, inst, sign, c, conflict in ranked:
        base = prices.get(inst) or c.get("price") or _baseline(inst, prices, now)
        for hor in (1, 3):
            extra = {"basis": c["basis"], "arm": c["arm"] if c.get("arm") is not None else adder.arm}
            if conflict:
                extra["conflict"] = True
            adder.add(c["kind"], c["subject"], c["text"], c["direction"], hor, c["conf"], c["size"],
                      c["topic"], _link_type(graph, c.get("node"), "", "currency" if c["kind"] == "currency"
                                             else _slug(inst)), c["source"], inst, base, extra)
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


def _trailing_vol(pts, made):
    """Std dev (percent) of daily moves in the history up to the call date; None under VOL_MIN_POINTS points."""
    seg = [p for d, p in pts if d <= made][-31:]
    if len(seg) < VOL_MIN_POINTS:
        return None
    rets = [(b / a - 1.0) * 100.0 for a, b in zip(seg, seg[1:]) if a]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    vol = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    return vol if vol > 1e-9 else None


def _difficulty(pct, inst, horizon, pts, made):
    """|move| over the instrument's trailing daily vol (or the flat threshold scale when history is short), cap 3."""
    scale = _trailing_vol(pts, made) or flat_threshold(inst, horizon)
    return round(min(DIFF_CAP, abs(pct) / scale), 3) if scale else None


def _due_point(pts, made, due, horizon, today):
    """(gap_days, date, price) of the price that settles a call, or None if none exists (yet).
    1-day calls: the first price on a later date than the call (the next trading day), within due+3 days.
    Longer calls: the nearest price within +-DUE_WINDOW days; a due date on a weekend takes the first price
    on/after it within +3 days at once."""
    if horizon <= 1:
        for d, p in pts:
            if d > made:
                return (abs((d - due).days), d, p) if d <= due + timedelta(days=WEEKEND_SLACK) else None
        return None
    if due.weekday() >= 5:
        for d, p in pts:
            if due <= d <= due + timedelta(days=WEEKEND_SLACK) and d > made:
                return (abs((d - due).days), d, p)
        if today <= due + timedelta(days=WEEKEND_SLACK):
            return None  # Monday's price is not in yet
    return _price_near(pts, due, made + timedelta(days=1))


def _grade_price(c, today, cache):
    inst = c.get("instrument")
    made, due = _pdate(c["made"]), _pdate(c["due"])
    made_day = _pdate(_day(made))
    if inst not in cache:
        if "__rows__" not in cache:
            cache["__rows__"] = market_history._load()
        cache[inst] = _history_points(inst, cache["__rows__"])
    pts = cache[inst]
    base = _price(c.get("baseline_price"))
    if base is None:  # fall back to the history price on/before the day the call was made
        prior = [p for d, p in pts if d <= made_day]
        base = prior[-1] if prior else None
    horizon = int(c.get("horizon_days") or (due - made_day).days or 1)
    hit = _due_point(pts, made_day, due, horizon, today)
    if hit is not None and hit[1] > today:
        hit = None
    # longer calls wait until the +-2 day window has fully passed unless the exact due date has a price
    wait = hit is not None and horizon > 1 and hit[0] > 0 and today < due + timedelta(days=DUE_WINDOW) \
        and due.weekday() < 5
    if hit is None or wait:
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
    flat = flat_threshold(inst, horizon)
    if abs(pct) < flat:
        result = "flat"
    else:
        result = "right" if (pct > 0) == (sign > 0) else "wrong"
    sa = _size_actual(pct)
    # persistence baseline: the move over the same-length window before the call continues
    length = max(1, (due - made_day).days)
    bright = None
    pb = _price_near(pts, made_day - timedelta(days=length), datetime.min)
    if pb is not None and pb[1] < made_day and result != "flat" and pb[2]:
        prev = (base / pb[2] - 1.0) * 100.0
        if abs(prev) >= flat:
            bright = (prev > 0) == (pct > 0)
    edge = None
    if bright is not None and result in ("right", "wrong"):
        edge = (0 if bright else 1) if result == "right" else (-1 if bright else 0)
    diff = _difficulty(pct, inst, horizon, pts, made_day)
    grade = {"result": result, "at": _day(today), "price_date": _day(pdate), "price_due": round(pdue, 4),
             "actual_change_pct": round(pct, 3), "size_actual": sa, "size_matched": sa == c.get("size"),
             "baseline_right": bright, "difficulty": diff, "edge": edge, "soft": False}
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
                if status == "graded" and not grade.get("soft"):
                    c["difficulty"], c["edge"] = grade.get("difficulty"), grade.get("edge")
                newly.append(c)
        except Exception as e:
            print(f"[LEARN] ledger could not grade {c.get('id')}: {e}")
    if newly:
        _save_all(store, now)
    return newly


@_safe(list)
def open_soft_calls(min_age_days=2, limit=40, now=None):
    """Open soft prediction calls at least min_age_days old, oldest first, compact:
    [{'id','if','then','made'}] (texts <= 200 chars). For an optional external grader."""
    today = _pdate(_day(_now(now)))
    out = []
    for c in _load_store(CALLS_FILE)["calls"]:
        if c.get("kind") != "prediction" or c.get("status") != "open" or c.get("instrument"):
            continue
        try:
            if (today - _pdate(c["made"])).days < int(min_age_days):
                continue
        except Exception:
            continue
        out.append({"id": str(c["id"]), "if": _clip(c.get("if"), 200), "then": _clip(c.get("then"), 200),
                    "made": str(c.get("made", ""))[:10]})
    out.sort(key=lambda r: (r["made"], r["id"]))
    return out[:max(0, int(limit))]


@_safe(list)
def apply_external_soft_grades(grades, now=None, source="jev"):
    """Apply {call_id: 'right'|'wrong'|'unclear'} (already thresholded by the caller) to OPEN soft prediction
    calls. right/wrong -> status graded, grade {'result','soft':True,'source','at'}; 'unclear' leaves the call
    open until its due date has passed, then closes it as unclear. Idempotent (only open calls change); soft
    grades never reach hit_rate (score_call + the soft flag route them to hit_rate_soft). Returns the changed calls."""
    if not isinstance(grades, dict) or not grades:
        return []
    now = _now(now)
    today = _pdate(_day(now))
    store = _load_store(CALLS_FILE, strict=True)
    src = _clip(source, 20) or "external"
    newly = []
    for c in store["calls"]:
        res = grades.get(c.get("id"))
        if res not in ("right", "wrong", "unclear"):
            continue
        if c.get("status") != "open" or c.get("kind") != "prediction" or c.get("instrument"):
            continue
        if res == "unclear":
            try:
                if today <= _pdate(c["due"]):
                    continue
            except Exception:
                continue
            c["status"] = "unclear"
        else:
            c["status"] = "graded"
        c["grade"] = {"result": res, "soft": True, "source": src, "at": _day(today)}
        newly.append(c)
    if newly:
        _save_all(store, now)
    return newly


# ---------------------------------------------------------------- compact outcomes store
# outcomes.json keeps one short row per graded PRICE call (flat included) so windows of 30-60 days survive at
# ~120 calls/day while calls.json only holds open calls and the last few days. Row layout:
#   [made, graded, instrument_code, dir(+1/-1 expected move), result(1 right,0 wrong,2 partial,3 flat),
#    confidence(0 low,1 med,2 high), horizon_days, basis_code, difficulty(1dp), edge(-1/0/1|null),
#    prior_p(2dp|null), arm_code]   with codes {"instrument": [...], "basis": [...], "arm": [...]}.
# "recent_ids" (last RECENT_IDS call ids) makes appending idempotent. Soft/unclear calls never enter it.

_RESULT_CODE = {"right": 1, "wrong": 0, "partial": 2, "flat": 3}
_CODE_RESULT = {v: k for k, v in _RESULT_CODE.items()}
_CONF_NAME = {0: "low", 1: "medium", 2: "high"}


def _ob_empty():
    return {"rows": [], "codes": {"instrument": [], "basis": [], "arm": []}, "recent_ids": []}


def _ob_valid(d):
    try:
        if not isinstance(d, dict) or not isinstance(d.get("rows"), list):
            return False
        codes = d.get("codes")
        if not isinstance(codes, dict) or not all(isinstance(codes.get(k), list)
                                                  for k in ("instrument", "basis", "arm")):
            return False
        if not isinstance(d.get("recent_ids", []), list):
            return False
        return all(isinstance(r, list) and len(r) >= 12 for r in d["rows"])
    except Exception:
        return False


def _load_outcomes():
    """None when the file is missing; the store when valid; raises _Corrupt (after a backup) when damaged."""
    d = learning_io.load_json(OUTCOMES_FILE, _MISSING)
    if d is _MISSING:
        if os.path.exists(OUTCOMES_FILE):
            _quarantine(OUTCOMES_FILE)
        return None
    if not _ob_valid(d):
        _quarantine(OUTCOMES_FILE)
    d.setdefault("recent_ids", [])
    return d


def _is_price_graded(c):
    g = c.get("grade") if isinstance(c, dict) else None
    return (isinstance(c, dict) and c.get("status") == "graded" and bool(c.get("instrument"))
            and isinstance(g, dict) and not g.get("soft") and g.get("result") in _RESULT_CODE)


def _code(lst, v):
    if v in (None, ""):
        return None
    v = str(v)
    if v not in lst:
        lst.append(v)
    return lst.index(v)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _row_of(c, codes):
    g = c["grade"]
    sign = _call_sign(c)
    if sign is None:
        return None
    edge = g.get("edge")
    if edge is None and g.get("baseline_right") is not None and g["result"] in ("right", "wrong"):
        b = bool(g["baseline_right"])   # legacy calls: derive the edge from the baseline
        edge = (0 if b else 1) if g["result"] == "right" else (-1 if b else 0)
    diff = _num(g.get("difficulty", c.get("difficulty")))
    pr = _num(c.get("prior_p"))
    try:
        hz = int(c.get("horizon_days")) if c.get("horizon_days") is not None else None
    except Exception:
        hz = None
    return [str(c.get("made", ""))[:10], str(g.get("at") or c.get("due") or "")[:10],
            _code(codes["instrument"], c.get("instrument")), sign, _RESULT_CODE[g["result"]],
            _CONF_RANK.get(_conf(c.get("confidence")), 1), hz, _code(codes["basis"], c.get("basis")),
            round(diff, 1) if diff is not None else None, edge if edge in (-1, 0, 1) else None,
            round(pr, 2) if pr is not None else None, _code(codes["arm"], c.get("arm"))]


def _sync_outcomes(ob, calls):
    """Append rows for graded price calls not seen yet (idempotent by call id). Returns rows added."""
    known = set(ob["recent_ids"])
    fresh = [c for c in calls if _is_price_graded(c) and c.get("id") not in known]
    fresh.sort(key=lambda c: (str(c["grade"].get("at", "")), str(c.get("made", ""))))
    n = 0
    for c in fresh:
        try:
            row = _row_of(c, ob["codes"])
        except Exception:
            row = None
        if row is None:
            continue
        ob["rows"].append(row)
        ob["recent_ids"].append(c["id"])
        n += 1
    if n:
        _bound_outcomes(ob)
    return n


def _bound_outcomes(ob):
    ob["rows"] = ob["rows"][-MAX_OUTCOME_ROWS:]
    ob["recent_ids"] = ob["recent_ids"][-RECENT_IDS:]
    while len(ob["rows"]) > 100 and len(json.dumps(ob, separators=(",", ":")).encode("utf-8")) > OUTCOME_BYTES:
        ob["rows"] = ob["rows"][max(1, len(ob["rows"]) // 20):]


def _seed_outcomes(calls):
    """Migration: build the store from graded price calls in calls_archive.json and calls.json."""
    ob = _ob_empty()
    try:
        arch = _load_store(ARCHIVE_FILE)["calls"]
    except Exception:
        arch = []
    seen, merged = set(), []
    for c in list(arch) + list(calls):
        if c.get("id") not in seen:
            seen.add(c.get("id"))
            merged.append(c)
    _sync_outcomes(ob, merged)
    return ob


def _write_outcomes(ob):
    learning_io.safe_write_json(OUTCOMES_FILE, ob)


def _row_to_call(r, codes, i):
    """Expand an outcomes row into a call-shaped dict (flagged _row) for the scorecard code."""
    def look(key, idx):
        try:
            return codes[key][idx] if idx is not None else None
        except Exception:
            return None
    res = _CODE_RESULT.get(r[4])
    edge = r[9]
    base = None
    if res == "right" and edge in (0, 1):
        base = edge == 0
    elif res == "wrong" and edge in (-1, 0):
        base = edge == -1
    inst = look("instrument", r[2])
    basis = look("basis", r[7])
    return {"id": "o%d" % i, "_row": True, "kind": "currency" if basis == "currency" else "market",
            "subject": inst, "instrument": inst, "text": "", "direction": "up" if r[3] == 1 else "down",
            "expect": "up" if r[3] == 1 else "down", "confidence": _CONF_NAME.get(r[5], "medium"),
            "horizon_days": r[6], "basis": basis, "arm": look("arm", r[11]), "prior_p": r[10],
            "made": r[0], "due": r[1], "status": "graded", "topic": "", "link_type": "",
            "grade": {"result": res, "at": r[1], "soft": False, "baseline_right": base,
                      "difficulty": r[8], "edge": edge}}


def _outcome_view(calls, write):
    """(ob, persisted_ok): the outcomes store synced with `calls` in memory, written when `write` and the file
    is healthy. A damaged file is backed up, left untouched, and replaced in memory by a view of `calls`."""
    try:
        ob = _load_outcomes()
        healthy = True
    except _Corrupt:
        ob, healthy = _ob_empty(), False
    fresh = False
    if ob is None:
        ob, fresh = _seed_outcomes(calls), True
    n = _sync_outcomes(ob, calls) if not fresh else 0
    if healthy and write and (n or (fresh and ob["rows"])):
        try:
            _write_outcomes(ob)
        except Exception as e:  # noqa: BLE001
            print(f"[LEARN] ledger could not save outcomes: {e}")
    if not healthy:
        _sync_outcomes(ob, calls)
    return ob


def _score_items(now, write):
    calls = _load_store(CALLS_FILE)["calls"]
    ob = _outcome_view(calls, write)
    items = [dict(c, _dup=True) if _is_price_graded(c) else c for c in calls]
    items += [_row_to_call(r, ob["codes"], i) for i, r in enumerate(ob["rows"])]
    return items


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
            "enough_data": False, "enough_price_data": False, "skill": None, "edge_rate": None,
            "hit_rate_weighted": None, "no_move_share": None, "no_move_flag": False, "brier": None,
            "by_horizon": {}, "by_basis": {}, "by_arm": {}, "n_price_calls_today": 0,
            "n_price_graded_7d": 0, "n_open_price": 0, "recent_30d": {"n": 0, "hit_rate": None},
            "prev_30d": {"n": 0, "hit_rate": None}}


@_safe(_empty_scorecard)
def scorecard(now=None, write=True):
    """Scorecard as of the Toronto date of `now` (datetime, date or 'YYYY-MM-DD'). Counts:
    n_graded = calls graded (incl. flat); n_scored = price calls behind hit_rate; n_soft = soft calls behind
    hit_rate_soft; n_flat / n_unclear = excluded from every rate. Buckets and recent windows mix both kinds.
    The file is only rewritten when its content changes."""
    now = _now(now)
    today = _pdate(_day(now))
    calls = _score_items(now, write)
    sc = _empty_scorecard()
    sc["as_of"] = _day(now)
    conf, topic, link, kind, inst = {}, {}, {}, {}, {}
    hard_h = soft_h = base_h = base_n = 0
    skill_mine = 0.0      # our hits on the calls whose baseline is defined (same calls as the baseline rate)
    edges = []
    wsum = whit = 0.0
    briers = []
    n_move = 0            # price-graded outcomes with a usable result: right, wrong or flat
    horizon, basis, arm = {}, {}, {}
    arm_base = {}
    rec = [0.0, 0]
    prev = [0.0, 0]
    for c in calls:
        st = c.get("status")
        if c.get("instrument") and not c.get("_row") and str(c.get("made", ""))[:10] == sc["as_of"]:
            sc["n_price_calls_today"] += 1
        if c.get("_dup"):   # graded price call still in calls.json: counted via its outcomes row; topic buckets only
            s = score_call(c)
            if s is not None:
                _bucket(topic, c.get("topic") or "other", s)
                if c.get("link_type"):
                    _bucket(link, c["link_type"], s)
            continue
        if st == "open":
            sc["n_open"] += 1
            if c.get("instrument"):
                sc["n_open_price"] += 1
            continue
        g = c.get("grade") if isinstance(c.get("grade"), dict) else {}
        if st == "graded":
            sc["n_graded"] += 1
            if not g.get("soft"):
                sc["n_price_graded"] += 1
            if g.get("result") == "flat":
                sc["n_flat"] += 1
            if not g.get("soft"):
                if g.get("result") in ("right", "wrong", "flat"):
                    n_move += 1
                try:
                    if 0 <= (today - _pdate(g.get("at"))).days < 7:
                        sc["n_price_graded_7d"] += 1
                except Exception:
                    pass
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
                skill_mine += s
            e = g.get("edge")
            if isinstance(e, (int, float)) and not isinstance(e, bool):
                edges.append(e)
            d = g.get("difficulty")
            w = float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0 else 1.0
            wsum += w
            whit += w * s
            for key, store_ in ((c.get("horizon_days"), horizon), (c.get("basis"), basis), (c.get("arm"), arm)):
                if key not in (None, ""):
                    _bucket(store_, str(key), s)
            if c.get("arm") not in (None, "") and g.get("baseline_right") is not None:
                ab = arm_base.setdefault(str(c["arm"]), [0.0, 0.0, 0])
                ab[0] += s
                ab[1] += 1.0 if g["baseline_right"] else 0.0
                ab[2] += 1
        if not g.get("soft"):   # soft (externally graded) calls never feed the price-skill statistics
            pr = c.get("prior_p")
            if isinstance(pr, (int, float)) and not isinstance(pr, bool) and 0.0 < pr < 1.0:
                p_ = float(pr)
            else:
                p_ = _CONF_P.get(c.get("confidence") or "medium", 0.65)
            briers.append((p_ - s) ** 2)
        _bucket(conf, c.get("confidence") or "medium", s)
        if not c.get("_row"):
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
    if base_n:   # skill: our hit rate minus the persistence baseline's, on the SAME calls
        sc["skill"] = round(skill_mine / base_n - base_h / base_n, 3)
    sc["edge_rate"] = round(sum(edges) / len(edges), 3) if edges else None
    sc["hit_rate_weighted"] = round(whit / wsum, 3) if wsum else None
    sc["no_move_share"] = round(sc["n_flat"] / n_move, 3) if n_move else None
    sc["no_move_flag"] = bool(n_move >= 10 and sc["n_flat"] / n_move >= 0.5)
    sc["brier"] = round(sum(briers) / len(briers), 4) if briers else None
    sc["by_horizon"] = _finish(horizon)
    sc["by_basis"] = _finish(basis)
    by_arm = _finish(arm)
    for k, v in by_arm.items():
        ab = arm_base.get(k)
        v["skill"] = round((ab[0] - ab[1]) / ab[2], 3) if ab and ab[2] else None
    sc["by_arm"] = by_arm
    sc["hit_rate_soft"] = _rate(soft_h, sc["n_soft"])
    sc["by_confidence"] = _finish(conf)
    sc["by_topic"] = _finish(topic)
    sc["by_link_type"] = _finish(link)
    sc["by_kind"] = _finish(kind)
    sc["by_instrument"] = _finish(inst)
    hi, lo = sc["by_confidence"].get("high"), sc["by_confidence"].get("low")
    sc["calibrated"] = bool(hi and lo and hi["n"] >= 10 and lo["n"] >= 10 and hi["rate"] - lo["rate"] >= 0.05)
    sc["enough_data"] = sc["n_scored"] + sc["n_soft"] >= MIN_ENOUGH
    sc["enough_price_data"] = sc["n_scored"] >= MIN_ENOUGH_PRICE
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
    """Newest graded calls: full records from calls.json, older price outcomes expanded from outcomes.json
    (synthetic id 'o<index>', empty text)."""
    calls = _load_store(CALLS_FILE)["calls"]
    graded = [c for c in calls if c.get("status") == "graded"]
    recs = []
    for c in graded:
        g = c.get("grade") or {}
        recs.append(({"id": c["id"], "kind": c.get("kind"), "subject": c.get("subject"),
                      "text": c.get("text"), "direction": c.get("direction"),
                      "confidence": c.get("confidence"), "size": c.get("size"), "topic": c.get("topic"),
                      "link_type": c.get("link_type"), "made": str(c.get("made", ""))[:10],
                      "due": c.get("due"), "result": g.get("result"), "soft": bool(g.get("soft")),
                      "actual_change_pct": g.get("actual_change_pct"), "size_actual": g.get("size_actual"),
                      "size_matched": g.get("size_matched"), "baseline_right": g.get("baseline_right"),
                      "difficulty": g.get("difficulty"), "edge": g.get("edge"), "arm": c.get("arm"),
                      "horizon_days": c.get("horizon_days"), "basis": c.get("basis"),
                      "prior_p": c.get("prior_p"), "instrument": c.get("instrument"),
                      "graded": g.get("at")}, str(g.get("at", ""))))
    try:
        ob = _load_outcomes()
    except _Corrupt:
        ob = None
    if ob:
        mins = [str(c["grade"].get("at", "")) for c in graded if _is_price_graded(c)]
        cutoff = min(mins) if mins else "9999"
        nrows = 0
        for i in range(len(ob["rows"]) - 1, -1, -1):
            r = ob["rows"][i]
            if str(r[1]) >= cutoff:
                continue   # covered by the fuller records in calls.json
            c = _row_to_call(r, ob["codes"], i)
            g = c["grade"]
            recs.append(({"id": c["id"], "kind": c["kind"], "subject": c["subject"], "text": "",
                          "direction": c["direction"], "confidence": c["confidence"], "size": None,
                          "topic": None, "link_type": None, "made": c["made"], "due": c["due"],
                          "result": g["result"], "soft": False, "actual_change_pct": None,
                          "size_actual": None, "size_matched": None, "baseline_right": g["baseline_right"],
                          "difficulty": g["difficulty"], "edge": g["edge"], "arm": c["arm"],
                          "horizon_days": c["horizon_days"], "basis": c["basis"], "prior_p": c["prior_p"],
                          "instrument": c["instrument"], "graded": g["at"]}, str(g["at"])))
            nrows += 1
            if nrows >= max(0, int(n)):
                break
    recs.sort(key=lambda t: (t[1], t[0]["made"]), reverse=True)
    return [r for r, _ in recs[:max(0, int(n))]]


@_safe(lambda: {"recorded_today": 0, "graded_today": 0, "open_price": 0})
def price_call_stats(now=None):
    """Fast-feedback counters for the run log: price calls recorded today, graded today, still open."""
    now = _now(now)
    day = _day(now)
    out = {"recorded_today": 0, "graded_today": 0, "open_price": 0}
    for c in _load_store(CALLS_FILE)["calls"]:
        if not c.get("instrument"):
            continue
        if str(c.get("made", ""))[:10] == day:
            out["recorded_today"] += 1
        if c.get("status") == "open":
            out["open_price"] += 1
        elif c.get("status") == "graded" and isinstance(c.get("grade"), dict) and c["grade"].get("at") == day:
            out["graded_today"] += 1
    return out
