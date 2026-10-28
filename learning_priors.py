"""
Historical bootstrap for the self-learning loop: statistical priors computed from years of daily
closes, so the loop has knowledge on day 1.

Data only (no LLM, no keys): daily closes come from keyless public sources (FRED CSV first, Yahoo
chart API as a last resort), are cached compactly under _data/_learning/history/<slug>.json and turned
into priors (_data/_learning/priors.json): "when driver X moves >= 1 sigma over N days, how often does
target Y rise/fall over the next h days", plus base rates and a persistence baseline.

Fence: network GET only to the whitelisted hosts below (no redirects, 3MB response cap, numbers-only
parsing, nothing is ever eval'd or exec'd, no secret is ever sent); every write goes through
learning_io.safe_write_json. Every public function never raises: on failure it prints one [LEARN]
line and returns a safe default.

Statistics are walk-forward safe: an event on day t uses only data up to t (sigma = trailing 250-day
std of daily returns ending at t); the outcome is the target's move over the following h trading days.

Outcome definition: priors condition on NON-FLAT outcomes (flat band = learning_ledger.flat_threshold);
the ledger counts flat calls separately and never as hits. Each pattern therefore carries flat_share (the
share of its events that ended flat) and prior_prob pulls p toward 0.5 by that share.

Honesty rules: events overlap heavily, so n_ind (greedy non-overlapping count) is the honest sample size.
edge = p_up - base_p (the target's own unconditional rate over the same history, so drift is not an
"edge"). sig = the Wilson interval (n_ind, Bonferroni-style z for the number of patterns tested) excludes
base_p AND the edge keeps its sign in both halves of the history and in the last 2 years. unstable = the
last-2-year p_up differs from the full-history p_up by more than 10 points. prior_prob / priors_text shrink
the probability (k = 100 + 0.5 * patterns searched for the target) because the best of many is biased.
Series are never thinned: a cache over its size cap drops its oldest points and stays daily.

load_priors() shape:
  {"generated","since","history_days":{"min","max"} (calendar days of the shortest/longest series),
   "tests","z_sig","n_sig",
   "instruments":{label:{"slug","first","last","n","sigma_daily_pct"}},
   "base":{"<target>|<h>":{"target","horizon_days","n","p_up","flat_share","persist_acc","persist_n"}},
   "patterns":[{"id","driver","driver_rule","dir","window_days","target","horizon_days","n","n_ind",
                "p_up","p_down","up_lo","up_hi" (95% Wilson on n_ind),"base_p","edge","flat_share","sig",
                "recent_p_up","recent_n","unstable"}]}

Kill switch: refresh_priors does nothing when learning_memory.enabled() is False; read paths keep working.
Refresh failing for every source pauses network refresh for 6 hours (history/fetch_state.json).
"""

import csv
import io
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import learning_io

HISTORY_DIR = os.path.join(learning_io.LEARN_DIR, "history")
PRIORS_FILE = os.path.join(learning_io.LEARN_DIR, "priors.json")

ALLOWED_HOSTS = ("fred.stlouisfed.org", "query1.finance.yahoo.com", "query2.finance.yahoo.com")
MAX_RESPONSE_BYTES = 3_000_000
USER_AGENT = "news-digest-learning/1.0 (read-only daily market history; contact via repo)"
HTTP_TIMEOUT = 20
YEARS = 10
REFRESH_AFTER_S = 20 * 3600
MAX_HISTORY_BYTES = 190_000
MAX_PRIORS_BYTES = 300_000

MIN_EVENTS = 30
SIGMA_WINDOW = 250
MIN_SIGMA_OBS = 120
SIGMA_K = 1.0
WINDOWS = (1, 3, 7)
HORIZONS = (1, 3, 7)
SHRINK_K = 100.0
P_MIN, P_MAX = 0.2, 0.8
UNSTABLE_GAP = 0.10
RECENT_DAYS = 730

# label -> (slug, [(source kind, symbol), ...] in fallback order)
INSTRUMENTS = {
    "USD/INR": ("usd-inr", [("fred", "DEXINUS"), ("yahoo", "INR=X")]),
    "USD/CAD": ("usd-cad", [("fred", "DEXCAUS"), ("yahoo", "CAD=X")]),
    "US Dollar Index": ("us-dollar-index", [("fred", "DTWEXBGS"), ("yahoo", "DX-Y.NYB")]),
    "WTI Crude": ("wti-crude", [("fred", "DCOILWTICO"), ("yahoo", "CL=F")]),
    "Brent Crude": ("brent-crude", [("fred", "DCOILBRENTEU"), ("yahoo", "BZ=F")]),
    "US 10Y Yield": ("us-10y-yield", [("fred", "DGS10"), ("yahoo", "^TNX")]),
    "S&P 500": ("sp-500", [("fred", "SP500"), ("yahoo", "^GSPC")]),
    "Gold": ("gold", [("yahoo", "GC=F")]),
    "Nifty 50": ("nifty-50", [("yahoo", "^NSEI")]),
}

# (driver, target); a driver equal to its target is the instrument's own momentum
PAIRS = [
    ("WTI Crude", "USD/INR"), ("WTI Crude", "USD/CAD"), ("Brent Crude", "USD/INR"),
    ("US Dollar Index", "USD/INR"), ("US Dollar Index", "USD/CAD"),
    ("US 10Y Yield", "US Dollar Index"), ("S&P 500", "Nifty 50"),
    ("S&P 500", "USD/INR"), ("Nifty 50", "USD/INR"), ("Gold", "US Dollar Index"),
    ("WTI Crude", "S&P 500"),
] + [(k, k) for k in INSTRUMENTS]

_ALIASES = {
    "usdinr": "USD/INR", "inr": "USD/INR", "rupee": "USD/INR", "usdcad": "USD/CAD", "cad": "USD/CAD",
    "dxy": "US Dollar Index", "dollarindex": "US Dollar Index", "usdindex": "US Dollar Index",
    "usdollarindex": "US Dollar Index", "wti": "WTI Crude", "wticrude": "WTI Crude", "oil": "WTI Crude",
    "crude": "WTI Crude", "brent": "Brent Crude", "brentcrude": "Brent Crude", "gold": "Gold",
    "nifty": "Nifty 50", "nifty50": "Nifty 50", "sp500": "S&P 500", "spx": "S&P 500",
    "us10y": "US 10Y Yield", "us10yyield": "US 10Y Yield", "10y": "US 10Y Yield", "us10yearyield": "US 10Y Yield",
}


def _log(msg):
    try:
        print("[LEARN] " + learning_io.redact(str(msg))[:200])
    except Exception:
        pass


def _canon(name):
    """Canonical instrument label for a free-form name, or None."""
    if not isinstance(name, str):
        return None
    if name in INSTRUMENTS:
        return name
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for lab in INSTRUMENTS:
        if re.sub(r"[^a-z0-9]", "", lab.lower()) == key:
            return lab
    return _ALIASES.get(key)


# ------------------------------------------------------------------ statistics

def wilson(k, n, z=1.96):
    """Wilson score interval (lo, hi) for k successes in n trials."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    den = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def _flat_pct(label, h):
    if "yield" in str(label).lower():
        return 1.0 * (1.5 if h > 5 else 1.0)
    try:
        import learning_ledger
        return float(learning_ledger.flat_threshold(label, h))
    except Exception:
        pass
    i = str(label).lower()
    if "usd/inr" in i or "usd/cad" in i:
        t = 0.15
    elif "dollar index" in i:
        t = 0.25
    elif any(k in i for k in ("wti", "brent", "gold")):
        t = 0.8
    elif any(k in i for k in ("s&p", "nifty")):
        t = 0.5
    else:
        t = 0.3
    return t * (1.5 if h > 5 else 1.0)


def _direction(ret_pct, thr):
    if ret_pct >= thr:
        return 1
    if ret_pct <= -thr:
        return -1
    return 0


def _daily_sigma(closes):
    """sig[i] = std of daily returns over the (up to) 250 returns ending at i (inclusive), or None.
    Uses data up to i only (no look-ahead)."""
    n = len(closes)
    rets = [None] * n
    for i in range(1, n):
        rets[i] = closes[i] / closes[i - 1] - 1.0
    sig = [None] * n
    s1 = [0.0] * (n + 1)
    s2 = [0.0] * (n + 1)
    for i in range(1, n):
        s1[i + 1] = s1[i] + rets[i]
        s2[i + 1] = s2[i] + rets[i] * rets[i]
    for i in range(1, n):
        lo = max(1, i - SIGMA_WINDOW + 1)
        k = i - lo + 1
        if k < MIN_SIGMA_OBS:
            continue
        a = s1[i + 1] - s1[lo]
        b = s2[i + 1] - s2[lo]
        sig[i] = math.sqrt(max(0.0, (b - a * a / k) / (k - 1)))
    return sig


def _events(dates, closes, sig, n_days, direction):
    """Indices i where the driver's n_days return is >= SIGMA_K * sigma_i * sqrt(n_days) in `direction`."""
    out = []
    for i in range(n_days, len(closes)):
        s = sig[i]
        if s is None:
            continue
        r = closes[i] / closes[i - n_days] - 1.0
        thr = SIGMA_K * s * math.sqrt(n_days)
        if (direction > 0 and r >= thr) or (direction < 0 and r <= -thr):
            out.append(i)
    return out


def _outcomes(dates, tdates, tcloses, ev_idx, h, thr):
    """For driver event indices -> list of (event_date, outcome in {1,-1,0}); events whose target
    date is missing or whose outcome horizon is not yet observed are skipped."""
    pos = {d: j for j, d in enumerate(tdates)}
    out = []
    for i in ev_idx:
        d = dates[i]
        j = pos.get(d)
        if j is None or j + h >= len(tcloses):
            continue
        ret = (tcloses[j + h] / tcloses[j] - 1.0) * 100.0
        out.append((i, d, _direction(ret, thr)))
    return out


def _n_independent(idx_list, gap):
    last = None
    n = 0
    for i in idx_list:
        if last is None or i - last >= gap:
            n += 1
            last = i
    return n


def _pattern(drv, tgt, N, direction, h, series, sigs, base):
    dd, dc = series[drv]
    td, tc = series[tgt]
    ev = _events(dd, dc, sigs[drv], N, direction)
    res = _outcomes(dd, td, tc, ev, h, _flat_pct(tgt, h))
    nonflat = [(i, d, o) for (i, d, o) in res if o != 0]
    n = len(nonflat)
    if n < MIN_EVENTS:
        return None
    b = base.get(f"{tgt}|{h}")
    if not b:
        return None
    base_p = b["p_up"]
    ups = sum(1 for _, _, o in nonflat if o > 0)
    n_ind = max(1, _n_independent([i for i, _, _ in nonflat], max(h, N)))
    p_up = ups / n
    lo, hi = wilson(round(p_up * n_ind), n_ind)
    cutoff = (datetime.strptime(dd[-1], "%Y-%m-%d") - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    rec = [o for _, d, o in nonflat if d >= cutoff]
    rec_up = (sum(1 for o in rec if o > 0) / len(rec)) if len(rec) >= 15 else None
    unstable = rec_up is not None and abs(rec_up - p_up) > UNSTABLE_GAP
    edge = p_up - base_p
    sgn = (edge > 0) - (edge < 0)
    half = n // 2
    stable = bool(sgn) and half >= 10 and rec_up is not None
    if stable:
        for seg in (nonflat[:half], nonflat[half:]):
            pu = sum(1 for _, _, o in seg if o > 0) / len(seg)
            stable = stable and ((pu - base_p) > 0) - ((pu - base_p) < 0) == sgn
        stable = stable and ((rec_up - base_p) > 0) - ((rec_up - base_p) < 0) == sgn
    word = "up" if direction > 0 else "down"
    return {
        "id": f"{INSTRUMENTS[drv][0]}|{word}{N}|{INSTRUMENTS[tgt][0]}|{h}",
        "driver": drv,
        "driver_rule": f"{drv} {word} >={SIGMA_K:g} sigma over {N}d",
        "dir": word, "window_days": N,
        "target": tgt, "horizon_days": h,
        "n": n, "n_ind": n_ind,
        "p_up": round(p_up, 4), "p_down": round(1 - p_up, 4),
        "up_lo": round(lo, 4), "up_hi": round(hi, 4),
        "base_p": round(base_p, 4),
        "edge": round(edge, 4),
        "flat_share": round((len(res) - n) / len(res), 4) if res else 0.0,
        "sig": False,
        "stable": stable,
        "recent_p_up": None if rec_up is None else round(rec_up, 4),
        "recent_n": len(rec),
        "unstable": bool(unstable),
    }


def _base_rates(tgt, tdates, tcloses):
    out = {}
    for h in HORIZONS:
        thr = _flat_pct(tgt, h)
        ups = downs = flats = 0
        agree = tot = 0
        for i in range(len(tcloses)):
            fut = None
            if i + h < len(tcloses):
                fut = _direction((tcloses[i + h] / tcloses[i] - 1.0) * 100.0, thr)
                if fut > 0:
                    ups += 1
                elif fut < 0:
                    downs += 1
                else:
                    flats += 1
            if fut and i >= h:
                prev = _direction((tcloses[i] / tcloses[i - h] - 1.0) * 100.0, thr)
                if prev:
                    tot += 1
                    agree += 1 if prev == fut else 0
        n = ups + downs
        if n >= MIN_EVENTS:
            out[f"{tgt}|{h}"] = {
                "target": tgt, "horizon_days": h, "n": n, "p_up": round(ups / n, 4),
                "flat_share": round(flats / (n + flats), 4),
                "persist_acc": round(agree / tot, 4) if tot else None, "persist_n": tot,
            }
    return out


def _z_bonferroni(m):
    """Two-sided z for family-wise alpha 0.05 over m tests (about 3.8 for ~360 tests)."""
    from statistics import NormalDist
    return NormalDist().inv_cdf(1.0 - 0.025 / max(1, m))


def compute_priors(series):
    """series: {label: (dates, closes)} -> priors dict. Pure; no I/O."""
    series = {k: v for k, v in series.items() if k in INSTRUMENTS and len(v[1]) > SIGMA_WINDOW // 2}
    sigs = {k: _daily_sigma(v[1]) for k, v in series.items()}
    base = {}
    for tgt, (td, tc) in series.items():
        base.update(_base_rates(tgt, td, tc))
    patterns = []
    for drv, tgt in PAIRS:
        if drv not in series or tgt not in series:
            continue
        for N in WINDOWS:
            for direction in (1, -1):
                for h in HORIZONS:
                    p = _pattern(drv, tgt, N, direction, h, series, sigs, base)
                    if p:
                        patterns.append(p)
    # significance: the interval (n_ind, Bonferroni z) must exclude the target's own unconditional
    # rate AND the edge must keep its sign in both halves of history and in the last 2 years.
    z = _z_bonferroni(len(patterns))
    for p in patterns:
        lo, hi = wilson(round(p["p_up"] * p["n_ind"]), p["n_ind"], z)
        p["sig"] = bool(p.pop("stable") and (lo > p["base_p"] or hi < p["base_p"]))
    inst = {}
    for k, (d, c) in series.items():
        s = next((x for x in reversed(sigs[k]) if x is not None), None)
        inst[k] = {"slug": INSTRUMENTS[k][0], "first": d[0], "last": d[-1], "n": len(c),
                   "sigma_daily_pct": None if s is None else round(s * 100, 4)}
    spans = []
    for v in inst.values():
        try:
            spans.append((date.fromisoformat(v["last"]) - date.fromisoformat(v["first"])).days)
        except Exception:
            pass
    first = min((v["first"] for v in inst.values()), default=None)
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "since": first,
           "history_days": {"min": min(spans, default=0), "max": max(spans, default=0)},
           "tests": len(patterns), "z_sig": round(z, 3), "n_sig": sum(1 for p in patterns if p["sig"]),
           "instruments": inst, "base": base, "patterns": patterns}
    patterns.sort(key=lambda p: (not p["sig"], -(abs(p["edge"]) * math.sqrt(p["n_ind"])), p["id"]))
    while len(json.dumps(out, separators=(",", ":"))) > MAX_PRIORS_BYTES and out["patterns"]:
        del out["patterns"][-max(1, len(out["patterns"]) // 10):]
    return out


# ------------------------------------------------------------------ network (whitelisted, capped)

def _http_get(url, timeout):
    """GET with a TOTAL time budget of `timeout` seconds (connect + every chunk), not just per-read."""
    import requests
    stop = time.monotonic() + float(timeout)
    m = re.match(r"https://([a-z0-9.-]+)/", url)
    if not m or m.group(1) not in ALLOWED_HOSTS:
        raise ValueError("host not allowed")
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout,
                     allow_redirects=False, stream=True)
    try:
        if r.status_code != 200:
            raise ValueError(f"http {r.status_code}")
        buf = bytearray()
        for chunk in r.iter_content(65536):
            if time.monotonic() > stop:
                raise TimeoutError("request deadline passed")
            buf.extend(chunk)
            if len(buf) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
        return bytes(buf)
    finally:
        r.close()


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_fred_csv(raw):
    """Strict: two columns, ISO date + plain number; '.' (missing) and anything else is skipped."""
    text = raw.decode("utf-8", "strict")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) != 2 or not rows[0][0].lower().endswith("date"):
        raise ValueError("unexpected csv header")
    out = []
    for r in rows[1:]:
        if len(r) != 2 or not _DATE.fullmatch(r[0]) or not _NUM.fullmatch(r[1].strip()):
            continue
        v = float(r[1])
        if v > 0 and math.isfinite(v):
            out.append((r[0], v))
    return out


def _parse_yahoo(raw):
    obj = json.loads(raw.decode("utf-8", "strict"))
    res = obj["chart"]["result"][0]
    ts = res["timestamp"]
    cl = res["indicators"]["quote"][0]["close"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {}
    for t, c in zip(ts, cl):
        if not isinstance(t, (int, float)) or not isinstance(c, (int, float)) or isinstance(c, bool):
            continue
        d = datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d")
        if d >= today or not (c > 0 and math.isfinite(c)):  # skip today's partial bar
            continue
        out[d] = float(c)
    return sorted(out.items())


def _fetch_fred(sym, since, timeout):
    if not re.fullmatch(r"[A-Z0-9]{2,20}", sym):
        raise ValueError("bad symbol")
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sym}&cosd={since}"
    return _parse_fred_csv(_http_get(url, timeout))


def _fetch_yahoo(sym, since, timeout):
    if not re.fullmatch(r"[A-Z0-9=^.-]{1,16}", sym):
        raise ValueError("bad symbol")
    p1 = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    from urllib.parse import quote
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym, safe='')}"
           f"?period1={p1}&period2={p2}&interval=1d")
    return _parse_yahoo(_http_get(url, timeout))


FETCHERS = {"fred": _fetch_fred, "yahoo": _fetch_yahoo}


# ------------------------------------------------------------------ cache

def _sig6(x):
    return float(f"{x:.6g}")


def _to_disk(rec):
    """Dates are stored as YYYYMMDD integers (compact, and nothing for the string redactor to chew on)."""
    out = dict(rec)
    out["dates"] = [int(d.replace("-", "")) for d in rec["dates"]]
    return out


def _from_disk(c):
    d = c.get("dates")
    if isinstance(d, list) and d and all(isinstance(x, int) and not isinstance(x, bool) for x in d):
        c = dict(c)
        out = []
        for x in d:
            t = f"{x:08d}"
            out.append(f"{t[:4]}-{t[4:6]}-{t[6:]}")
        c["dates"] = out
    return c


def _history_path(label):
    return os.path.join(HISTORY_DIR, INSTRUMENTS[label][0] + ".json")


def _valid_cache(c):
    try:
        d, v = c["dates"], c["closes"]
        return (isinstance(d, list) and isinstance(v, list) and len(d) == len(v) and len(d) > 0
                and all(isinstance(x, str) and _DATE.fullmatch(x) for x in d)
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in v)
                and d == sorted(set(d)) and date.fromisoformat(d[0]) and date.fromisoformat(d[-1]))
    except Exception:
        return False


def _load_cache(label):
    """Cache dict or None. A corrupt file is backed up (inside the fence) and treated as missing."""
    path = _history_path(label)
    c = learning_io.load_json(path, None)
    if c is None:
        if os.path.exists(path):
            try:
                learning_io.backup_corrupt(path)
            except Exception:
                pass
        return None
    if isinstance(c, dict):
        c = _from_disk(c)
    if not isinstance(c, dict) or not _valid_cache(c):
        try:
            learning_io.backup_corrupt(path)
        except Exception:
            pass
        return None
    return c


def _save_cache(label, source, dates, closes, fetched):
    closes = [_sig6(x) for x in closes]
    rec = {"label": label, "source": source, "last": dates[-1], "fetched": fetched,
           "dates": dates, "closes": closes}
    # over the size cap: drop the OLDEST part (daily data is kept daily; never thin to weekly points)
    while len(json.dumps(_to_disk(rec), separators=(",", ":"))) > MAX_HISTORY_BYTES and len(rec["dates"]) > 300:
        cut = max(1, len(rec["dates"]) // 10)
        rec["dates"], rec["closes"] = rec["dates"][cut:], rec["closes"][cut:]
        rec["truncated"] = True
    learning_io.safe_write_json(_history_path(label), _to_disk(rec))


def _age_s(cache, now):
    try:
        t = datetime.fromisoformat(cache["fetched"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (now - t).total_seconds()
    except Exception:
        return float("inf")


def _refresh_one(label, cache, now, deadline):
    """Fetch (tail or full) through the fallback chain. Returns "ok" (cache written), "fail" (every source
    failed) or "budget" (ran out of time before trying)."""
    since_full = (now - timedelta(days=int(365.25 * YEARS))).strftime("%Y-%m-%d")
    tried = False
    for kind, sym in INSTRUMENTS[label][1]:
        remaining = deadline - time.monotonic()
        if remaining < 1.5:
            return "fail" if tried else "budget"
        fetch = FETCHERS.get(kind)
        if not fetch:
            continue
        tried = True
        tail = bool(cache) and cache.get("source") == kind and not cache.get("thinned")
        since = (date.fromisoformat(cache["dates"][-1]) - timedelta(days=10)).isoformat() if tail else since_full
        try:
            rows = fetch(sym, since, min(HTTP_TIMEOUT, remaining))
        except Exception as e:
            _log(f"priors: {label} via {kind} failed ({type(e).__name__})")
            continue
        if tail:
            merged = dict(zip(cache["dates"], cache["closes"]))
            merged.update(rows)
        else:
            if len(rows) < SIGMA_WINDOW:
                _log(f"priors: {label} via {kind} returned too little data")
                continue
            merged = dict(rows)
        ds = sorted(merged)
        try:
            _save_cache(label, kind, ds, [merged[d] for d in ds], now.isoformat())
        except Exception as e:
            _log(f"priors: {label} cache write failed ({type(e).__name__})")
            return "fail"
        return "ok"
    return "fail" if tried else "budget"


STATE_FILE = os.path.join(HISTORY_DIR, "fetch_state.json")
FAIL_BACKOFF_S = 6 * 3600


def _recently_failed(now):
    d = learning_io.load_json(STATE_FILE, None)
    try:
        t = datetime.fromisoformat(d["failed_at"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return 0 <= (now - t).total_seconds() < FAIL_BACKOFF_S
    except Exception:
        return False


def _set_failed(now, failed):
    try:
        learning_io.safe_write_json(STATE_FILE, {"failed_at": now.isoformat() if failed else None})
    except Exception:
        pass


def _learning_enabled():
    try:
        import learning_memory
        fn = getattr(learning_memory, "enabled", None)
        return bool(fn()) if callable(fn) else True
    except Exception:
        return True


# ------------------------------------------------------------------ public API

def refresh_priors(now=None, budget_s=90):
    """Refresh stale/missing history caches (bounded by budget_s overall), then recompute and write the
    priors. Returns True if priors.json was written. Does nothing at all (no network, no writes) when
    learning_memory.enabled() is False. After a refresh in which every fetch failed, network refresh is
    skipped for 6 hours (the existing caches are still used)."""
    try:
        if not _learning_enabled():
            _log("priors: learning is switched off; no refresh")
            return False
        now = now or datetime.now(timezone.utc)
        deadline = time.monotonic() + float(budget_s)
        skip_net = _recently_failed(now)
        series = {}
        ok = fail = 0
        for label in INSTRUMENTS:
            cache = _load_cache(label)
            if not skip_net and (cache is None or _age_s(cache, now) >= REFRESH_AFTER_S):
                r = _refresh_one(label, cache, now, deadline)
                if r == "ok":
                    ok += 1
                    cache = _load_cache(label)
                elif r == "fail":
                    fail += 1
            if cache and cache.get("thinned"):
                _log(f"priors: {label} cache is thinned (old format); excluded from patterns")
            elif cache:
                series[label] = (cache["dates"], cache["closes"])
            else:
                _log(f"priors: no history for {label}; skipped")
        if fail and not ok:
            _set_failed(now, True)
        elif ok and os.path.exists(STATE_FILE):
            _set_failed(now, False)
        if not series:
            return False
        pri = compute_priors(series)
        learning_io.safe_write_json(PRIORS_FILE, _pack(pri))
        return True
    except Exception as e:
        _log(f"priors: refresh failed ({type(e).__name__})")
        return False


_COLS = ["driver", "dir", "window_days", "target", "horizon_days", "n", "n_ind", "p_up", "up_lo", "up_hi",
         "sig", "recent_p_up", "recent_n", "unstable", "base_p", "flat_share"]


def _pack(pri):
    """Pattern rows as number-only arrays (instrument names by index): small, and no per-string redaction."""
    labs = list(INSTRUMENTS)
    out = {k: v for k, v in pri.items() if k != "patterns"}
    out["labels"] = labs
    out["cols"] = _COLS
    out["rows"] = [[labs.index(p["driver"]), 1 if p["dir"] == "up" else -1, p["window_days"],
                    labs.index(p["target"]), p["horizon_days"], p["n"], p["n_ind"], p["p_up"], p["up_lo"],
                    p["up_hi"], int(p["sig"]), p["recent_p_up"], p["recent_n"], int(p["unstable"]),
                    p["base_p"], p["flat_share"]]
                   for p in pri["patterns"]]
    return out


def _unpack(d):
    labs = d["labels"]
    cols = d.get("cols") or _COLS
    pats = []
    for r in d["rows"]:
        v = dict(zip(cols, r))
        drv, tgt = labs[v["driver"]], labs[v["target"]]
        word = "up" if v["dir"] > 0 else "down"
        p_up = v["p_up"]
        base_p = v.get("base_p", 0.5)
        pats.append({
            "id": f"{INSTRUMENTS[drv][0]}|{word}{v['window_days']}|{INSTRUMENTS[tgt][0]}|{v['horizon_days']}",
            "driver": drv, "driver_rule": f"{drv} {word} >={SIGMA_K:g} sigma over {v['window_days']}d",
            "dir": word, "window_days": v["window_days"], "target": tgt, "horizon_days": v["horizon_days"],
            "n": v["n"], "n_ind": v["n_ind"], "p_up": p_up, "p_down": round(1 - p_up, 4),
            "up_lo": v["up_lo"], "up_hi": v["up_hi"], "base_p": base_p, "edge": round(p_up - base_p, 4),
            "flat_share": v.get("flat_share", 0.0), "sig": bool(v["sig"]),
            "recent_p_up": v["recent_p_up"], "recent_n": v["recent_n"], "unstable": bool(v["unstable"])})
    out = {k: v for k, v in d.items() if k not in ("labels", "cols", "rows")}
    out["patterns"] = pats
    return out


def load_priors():
    try:
        p = learning_io.load_json(PRIORS_FILE, {})
        if isinstance(p, dict) and "rows" in p and "labels" in p:
            p = _unpack(p)
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _clamp(p):
    return max(P_MIN, min(P_MAX, p))


def _near_h(h):
    try:
        h = float(h)
    except Exception:
        h = 3.0
    return min(HORIZONS, key=lambda x: (abs(x - h), x))


def _flat_scale(p, flat_share):
    """Pull p toward 0.5 by the share of cases that end flat (flats are not hits in the ledger)."""
    try:
        fs = min(1.0, max(0.0, float(flat_share or 0.0)))
    except Exception:
        fs = 0.0
    return 0.5 + (p - 0.5) * (1.0 - fs)


def _pattern_p(p, up, n_target_patterns):
    """Probability the stated direction is right for a conditional pattern, after winner's-curse shrinkage:
    pulled toward the target's own base rate with k = 100 + 0.5 * (patterns searched for this target),
    then toward 0.5 by the flat share, then clamped."""
    raw = p["p_up"] if up else 1 - p["p_up"]
    base = p.get("base_p", 0.5)
    base = base if up else 1 - base
    k = SHRINK_K + 0.5 * max(0, n_target_patterns)
    w = p["n_ind"] / (p["n_ind"] + k)
    return _clamp(_flat_scale(base + (raw - base) * w, p.get("flat_share", 0.0)))


def _base_p(b, up):
    raw = b["p_up"] if up else 1 - b["p_up"]
    w = b["n"] / (b["n"] + SHRINK_K)
    return _clamp(_flat_scale(0.5 + (raw - 0.5) * w, b.get("flat_share", 0.0)))


def _per_target(pri):
    cnt = {}
    for p in pri.get("patterns") or []:
        cnt[p.get("target")] = cnt.get(p.get("target"), 0) + 1
    return cnt


def prior_prob(instrument, expected_direction, horizon_days, drivers=None):
    """(p, n, source): prior probability (0.2..0.8) that the stated direction ('up'|'down') is right for
    `instrument` over `horizon_days`. n is the number of INDEPENDENT samples behind it. `drivers` is an
    optional {label: recent % move}; the best matching conditional pattern wins (heavily shrunk toward the
    target's base rate for winner's curse and toward 0.5 by flat share), else the base rate. (0.5, 0,
    'none') when nothing is known."""
    try:
        up = str(expected_direction).strip().lower() == "up"
        tgt = _canon(instrument)
        pri = load_priors()
        if not tgt or not pri:
            return (0.5, 0, "none")
        h = _near_h(horizon_days)
        best = None
        if isinstance(drivers, dict):
            sig = {k: (v or {}).get("sigma_daily_pct") for k, v in (pri.get("instruments") or {}).items()}
            for lab, mv in drivers.items():
                dl = _canon(lab)
                try:
                    mv = float(mv)
                except Exception:
                    continue
                if not dl or not math.isfinite(mv) or not sig.get(dl):
                    continue
                for p in pri.get("patterns") or []:
                    if p.get("driver") != dl or p.get("target") != tgt or p.get("horizon_days") != h:
                        continue
                    need = sig[dl] * SIGMA_K * math.sqrt(p["window_days"])
                    if (p["dir"] == "up" and mv < need) or (p["dir"] == "down" and mv > -need):
                        continue
                    score = abs(p["edge"]) * math.sqrt(p["n_ind"]) * (0.5 if p.get("unstable") else 1.0)
                    if best is None or score > best[0]:
                        best = (score, p)
        if best:
            p = best[1]
            return (round(_pattern_p(p, up, _per_target(pri).get(tgt, 0)), 4), int(p["n_ind"]),
                    "pattern:" + p["id"])
        b = (pri.get("base") or {}).get(f"{tgt}|{h}")
        if b:
            return (round(_base_p(b, up), 4), int(b["n"]), f"base-rate:{tgt}|{h}")
        return (0.5, 0, "none")
    except Exception as e:
        _log(f"priors: prior_prob failed ({type(e).__name__})")
        return (0.5, 0, "none")


CAVEAT = "(history, not a forecast; selected from many patterns)"


def priors_text(instruments=None, max_chars=900):
    """Compact numeric lines for prompts. Whole lines only; total length <= max_chars. Shows the SHRUNK
    probability and the independent sample size, and ends with a caveat line."""
    try:
        pri = load_priors()
        if not pri:
            return ""
        want = None
        if instruments:
            want = {_canon(i) for i in instruments if _canon(i)}
            if not want:
                return ""
        since = str(pri.get("since") or "")[:4]
        hist = f" (history since {since})" if since else ""
        per_t = _per_target(pri)
        pats = [p for p in pri.get("patterns") or [] if p.get("sig") and not p.get("unstable")
                and (want is None or p["target"] in want)]
        pats.sort(key=lambda p: -(abs(p["edge"]) * math.sqrt(p["n_ind"])))
        lines = []
        for p in pats[:12]:
            pu = _pattern_p(p, True, per_t.get(p["target"], 0))
            rose = pu >= 0.5
            pct = round((pu if rose else 1 - pu) * 100)
            lines.append(f"{p['driver']} {p['dir']} >=1 sigma over {p['window_days']}d: {p['target']} "
                         f"{'rose' if rose else 'fell'} in ~{pct}% (shrunk) of {p['n_ind']} independent cases "
                         f"over the next {p['horizon_days']} day{'' if p['horizon_days'] == 1 else 's'}{hist}")
        for key, b in sorted((pri.get("base") or {}).items()):
            if want is not None and b["target"] not in want:
                continue
            if b["horizon_days"] != 3:
                continue
            pu = _base_p(b, True)
            ps = f"; 'last move continues' right {round(b['persist_acc'] * 100)}%" if b.get("persist_acc") is not None else ""
            lines.append(f"Base rate: {b['target']} rose in ~{round(pu * 100)}% (shrunk) of "
                         f"{max(1, b['n'] // 3)} independent 3-day windows{ps}")
        if not lines:
            return ""
        budget = max_chars - len(CAVEAT) - 1
        out, total = [], 0
        for ln in lines:
            add = len(ln) + (1 if out else 0)
            if total + add > budget:
                continue
            out.append(ln)
            total += add
        if not out:
            return ""
        return "\n".join(out + [CAVEAT])
    except Exception as e:
        _log(f"priors: priors_text failed ({type(e).__name__})")
        return ""


def history_summary():
    try:
        pri = load_priors()
        inst = pri.get("instruments") or {}
        spans = []
        per = {}
        for k, v in inst.items():
            try:
                spans.append((date.fromisoformat(v["last"]) - date.fromisoformat(v["first"])).days)
                per[k] = {"first": v["first"], "last": v["last"]}
            except Exception:
                pass
        years = max(spans, default=0) / 365.25
        return {"instruments": len(inst), "years": round(years, 1),
                "patterns": len(pri.get("patterns") or []), "generated": pri.get("generated"),
                "history_days": {"min": min(spans, default=0), "max": max(spans, default=0)},
                "n_sig": sum(1 for p in pri.get("patterns") or [] if p.get("sig")),
                "per_instrument": per}
    except Exception as e:
        _log(f"priors: history_summary failed ({type(e).__name__})")
        return {"instruments": 0, "years": 0.0, "patterns": 0, "generated": None,
                "history_days": {"min": 0, "max": 0}, "n_sig": 0, "per_instrument": {}}
