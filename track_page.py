"""
Track record strip for the digest page (fast recursive loop edition).

track_record_html(scorecard, changelog_text="", lessons_text="", priors=None, settings=None,
                  experiments=None, state=None) -> str      (never empty)
telegram_line(scorecard, state=None, priors=None) -> str     (plain text, one line)
CSS                                                          (include once in the page <style>)

Every extra input is optional: a missing or malformed one just drops its block.
Everything is escaped, no scripts. A percentage is never shown for fewer than
MIN_BUCKET calls, and the strip says "not enough data" instead of guessing.
"""

import html
import re

MIN_BUCKET = 5        # fewest calls behind any percentage we print
ENOUGH_TARGET = 30    # price-checked calls before the overall number is trusted
LESSONS_CAP = 6000
LINE_CAP = 260
CHANGELOG_LINES = 5
TOPIC_LIMIT = 8
INSTRUMENT_LIMIT = 8
PRIOR_LIMIT = 5
NOTE_LINES = 3
NO_MOVE_HIGH = 0.4    # share of "no move" calls that earns a warning chip
SKILL_SCALE = 25.0    # points that fill half of the diverging skill bar
EXP_MIN = 40          # graded calls per arm before an experiment can be called either way
EXP_SOLID = 100


def _e(x):
    return html.escape(str(x if x is not None else ""))


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _n(v):
    v = _num(v)
    return int(v) if v is not None and v >= 0 else 0


def _rate(v):
    v = _num(v)
    return min(1.0, max(0.0, float(v))) if v is not None else None


def _pct(r):
    return f"{round(r * 100)}%"


def _d(v):
    return v if isinstance(v, dict) else {}


def _bucket(b):
    """-> (n, rate or None). rate is None when the sample is too small to show."""
    if not isinstance(b, dict):
        return 0, None
    n = _n(b.get("n"))
    r = _rate(b.get("rate"))
    if r is None:
        r = _rate(b.get("hit_rate"))  # the ledger's 30-day windows use this key
    if r is None and n:
        hits = _num(b.get("hits"))
        if hits is not None:
            r = _rate(hits / n)
    return n, (r if n >= MIN_BUCKET else None)


def _sc(scorecard):
    sc = scorecard if isinstance(scorecard, dict) else {}
    # n is the count that actually backs hit_rate: price-graded calls when the scorecard says so,
    # otherwise n_graded. The two are never mixed.
    # n_scored is the count of calls with a clear up/down outcome: the number behind hit_rate and the baseline.
    n = (_n(sc.get("n_scored")) if "n_scored" in sc else
         _n(sc.get("n_price_graded")) if "n_price_graded" in sc else _n(sc.get("n_graded")))
    hr = _rate(sc.get("hit_rate")) if n >= MIN_BUCKET else None
    base = _rate(sc.get("hit_rate_baseline")) if n >= MIN_BUCKET else None
    flag = sc.get("enough_price_data", sc.get("enough_data", True))
    enough = n >= ENOUGH_TARGET and hr is not None and flag is not False
    return sc, n, hr, base, enough


def _unit(sc):
    return "price-checked calls" if ("n_price_graded" in sc or "n_scored" in sc) else "graded calls"


def _skill_points(sc, hr, base):
    """Skill in percentage points, or None. The scorecard's own number wins; else rate minus baseline."""
    if hr is not None and base is not None:
        return (hr - base) * 100          # the real signed gap; never clamped or hidden
    s = _num(sc.get("skill"))
    if s is not None:
        return s * 100 if abs(s) <= 1 else s
    return None


def _signed(p):
    r = round(p)
    return f"+{r}" if r > 0 else ("0" if r == 0 else f"−{abs(r)}")


def _skill_words(p):
    return "about level with the baseline" if abs(p) < 1 else f"skill {_signed(p)} points"


def _span_days(priors):
    """Days between the earliest first and latest last date over the instruments, or None."""
    import datetime
    firsts, lasts = [], []
    for v in _d(priors.get("instruments")).values():
        for key, bucket in (("first", firsts), ("last", lasts)):
            try:
                bucket.append(datetime.date.fromisoformat(str(_d(v).get(key))[:10]))
            except Exception:
                pass
    if firsts and lasts:
        d = (max(lasts) - min(firsts)).days
        return d if d > 0 else None
    return None


def _history_phrase(priors, sc=None, state=None):
    """'8 years of market history' (or None). Looks in priors, then state, then scorecard."""
    days = None
    for src in (priors, state, sc):
        if isinstance(src, dict):
            v = _num(src.get("history_days"))
            if v is not None and v > 0:
                days = v
                break
    if days is None and isinstance(priors, dict):
        days = _span_days(priors)
    if days is None:
        return None
    if days >= 365:
        y = round(days / 365.25, 1)
        y = int(y) if y == int(y) else y
        return f"{y} year{'s' if y != 1 else ''} of market history"
    d = int(days)
    return f"{d} day{'s' if d != 1 else ''} of market history"


def _patterns(priors):
    pats = _d(priors).get("patterns")
    out = []
    if isinstance(pats, list):
        for p in pats:
            if not isinstance(p, dict):
                continue
            n = _n(p.get("n_ind") if "n_ind" in p else p.get("n"))
            pu = _rate(p.get("p_up"))
            if n < MIN_BUCKET or pu is None:
                continue
            out.append((p, n, pu))
    return out


def telegram_line(scorecard, state=None, priors=None):
    sc, n, hr, base, enough = _sc(scorecard)
    hist = _history_phrase(priors, sc, state)
    if not enough:
        line = f"Learning: building ({n} of {ENOUGH_TARGET} {_unit(sc)}"
        if hist:
            line += f"; started from {hist.replace(' of market history', ' of market history')}"
        return line + ")"
    line = f"Learning: {_pct(hr)} right on {n} {_unit(sc)}"
    skill = _skill_points(sc, hr, base)
    bits = []
    if base is not None:
        bits.append(f"momentum baseline {_pct(base)}")
    if skill is not None:
        bits.append("about level" if abs(skill) < 1 else f"skill {_signed(skill)}")
    if bits:
        line += f" ({', '.join(bits)})"
    return line


# ---------- pieces ----------

def _bar(label, n, rate, colour, base=None, sub="", show_n=True):
    """One labelled horizontal bar. colour: 'sys' or 'base'."""
    if rate is None:
        track = '<span class="tr-track"></span>'
        val = f'<span class="tr-val tr-dim">n too small <span class="tr-n">({n})</span></span>'
    else:
        tick = ""
        if base is not None:
            tick = f'<i class="tr-tick" style="left:{base * 100:.1f}%" title="Momentum baseline {_pct(base)}"></i>'
        track = (f'<span class="tr-track"><b class="tr-fill {colour}" style="width:{max(rate * 100, 0.8):.1f}%"></b>{tick}</span>')
        val = (f'<span class="tr-val">{_pct(rate)} <span class="tr-n">n={n}</span></span>' if show_n
               else f'<span class="tr-val">{_pct(rate)}</span>')
    subtxt = f'<span class="tr-sub">{_e(sub)}</span>' if sub else ""
    return f'<div class="tr-row"><span class="tr-lab">{_e(label)}{subtxt}</span>{track}{val}</div>'


def _skill_bar(points, n):
    half = max(-1.0, min(1.0, points / SKILL_SCALE)) * 50
    if half >= 0:
        fill = f'<b class="tr-fill pos" style="left:50%;width:{max(half, 0.8):.1f}%"></b>'
    else:
        fill = f'<b class="tr-fill neg" style="right:50%;width:{max(-half, 0.8):.1f}%"></b>'
    track = f'<span class="tr-track tr-div">{fill}<i class="tr-zero"></i></span>'
    txt = "level" if abs(points) < 1 else f"{_signed(points)} pts"
    val = f'<span class="tr-val">{txt} <span class="tr-n">n={n}</span></span>'
    return f'<div class="tr-row"><span class="tr-lab">Skill vs baseline</span>{track}{val}</div>'


def _progress(n, small=False):
    pct = min(100.0, 100.0 * n / ENOUGH_TARGET)
    cls = "tr-prog tr-prog-s" if small else "tr-prog"
    return (f'<span class="{cls}" role="progressbar" aria-valuemin="0" aria-valuemax="{ENOUGH_TARGET}" '
            f'aria-valuenow="{min(n, ENOUGH_TARGET)}" aria-label="Price-checked calls so far">'
            f'<b style="width:{pct:.1f}%"></b></span>')


def _summary(sc, n, hr, base, enough, priors, state):
    hist = _history_phrase(priors, sc, state)
    pats = _patterns(priors)
    if not enough:
        head = (f'<span class="tr-title">Learning</span>'
                f'<span class="tr-line">{min(n, 9999)} of {ENOUGH_TARGET} {_e(_unit(sc))}</span>'
                f'{_progress(n, True)}')
    else:
        bit = f"{_pct(hr)} right on {n} {_unit(sc)}"
        if base is not None:
            bit += f" vs momentum baseline {_pct(base)}"
            skill = _skill_points(sc, hr, base)
            if skill is not None:
                bit += f" ({_skill_words(skill)})"
        head = f'<span class="tr-title">Track record</span><span class="tr-line">{_e(bit)}</span>'
    if hist:
        txt = f"Started with {hist}"
        if pats:
            txt += f" ({len(pats)} pattern{'s' if len(pats) != 1 else ''})"
        head += f'<span class="tr-hist">{_e(txt)}</span>'
    return head


def _note_block(state):
    t = _d(state).get("weekly_note")
    if not isinstance(t, str) or not t.strip():
        return ""
    lines = [c for c in (_clean_line(x) for x in t.splitlines()) if c][:NOTE_LINES]
    if not lines:
        return ""
    return ('<div class="tr-weekly"><h4>This week</h4>'
            + "".join(f"<p>{_e(x)}</p>" for x in lines) + "</div>")


def _stat(num, label):
    return f'<div class="tr-stat"><b>{_e(num)}</b><span>{_e(label)}</span></div>'


def _nextday(sc):
    keys = ("n_price_calls_today", "n_price_graded_7d", "n_open_price")
    if not any(k in sc for k in keys):
        return ""
    return ('<div class="tr-block"><h4>Next-day calls</h4><div class="tr-stats">'
            + _stat(_n(sc.get("n_price_calls_today")), "recorded today")
            + _stat(_n(sc.get("n_price_graded_7d")), "graded, last 7 days")
            + _stat(_n(sc.get("n_open_price")), "still waiting")
            + '</div></div>')


def _overall(sc, n, hr, base, enough):
    out = ['<div class="tr-block"><h4>Overall</h4>']
    if not enough:
        out.append(f'<p class="tr-note">Not enough {_e(_unit(sc))} yet: {n} of {ENOUGH_TARGET}. '
                   'Results are shown once there are enough to mean something.</p>')
        out.append(_progress(n))
    else:
        out.append(_bar("The system", n, hr, "sys"))
        if base is not None:
            out.append(_bar("Momentum baseline", n, base, "base", show_n=False))
        else:
            out.append('<p class="tr-note">No baseline available yet.</p>')
        skill = _skill_points(sc, hr, base)
        if skill is not None:
            out.append(_skill_bar(skill, n))
        nf, nu = _n(sc.get("n_flat")), _n(sc.get("n_unclear"))
        if "n_scored" in sc and (nf or nu):
            parts = ([f"{nf} flat (no move)"] if nf else []) + ([f"{nu} unclear"] if nu else [])
            out.append(f'<p class="tr-note">{_e(" and ".join(parts))} are not counted.</p>')
        nm = _rate(sc.get("no_move_share"))
        if nm is not None and n >= MIN_BUCKET:
            if nm >= NO_MOVE_HIGH:
                out.append(f'<p class="tr-chip">Predicting no-move too often ({_pct(nm)} of calls)</p>')
            else:
                out.append(f'<p class="tr-note">{_pct(nm)} of calls said "no move".</p>')
            out.append('<p class="tr-note">No credit for flat outcomes: a call of "no move" earns nothing when the price stays put.</p>')
        r30, p30 = sc.get("recent_30d"), sc.get("prev_30d")
        rn, rr = _bucket(r30)
        pn, pr = _bucket(p30)
        if rr is not None and pr is not None:
            d = round((rr - pr) * 100)
            way = "up" if d > 0 else "down" if d < 0 else "level"
            out.append(f'<p class="tr-note">Last 30 days {_pct(rr)} (n={rn}) vs the 30 before {_pct(pr)} (n={pn}): '
                       f'{way}{" " + str(abs(d)) + " points" if d else ""}.</p>')
    n_open = _n(sc.get("n_open"))
    if n_open and "n_open_price" not in sc:
        out.append(f'<p class="tr-note">{n_open} more call{"s" if n_open != 1 else ""} still waiting to be checked.</p>')
    out.append("</div>")
    return "".join(out)


_H_ORDER = {"1d": 1, "3d": 3, "10d": 10}


def _hkey(k):
    s = str(k).strip().lower()
    if s.isdigit():
        s += "d"
    return s


def _horizons(sc, base, enough):
    bh = sc.get("by_horizon")
    if not isinstance(bh, dict) or not bh:
        return ""
    items = sorted(((_hkey(k), b) for k, b in bh.items()),
                   key=lambda t: (_H_ORDER.get(t[0], 99), t[0]))
    rows = []
    for k, b in items[:6]:
        n, r = _bucket(b)
        rows.append(_bar(k, n, r, "sys", base if enough else None))
    return '<div class="tr-block"><h4>By horizon</h4>' + "".join(rows) + "</div>"


def _ranked(title, d, base, enough, limit, empty_msg, unit="items"):
    d = d if isinstance(d, dict) else {}
    items = []
    for name, b in d.items():
        n, r = _bucket(b)
        if r is not None:
            items.append((str(name), n, r))
    if not items:
        return f'<div class="tr-block"><h4>{_e(title)}</h4><p class="tr-note">{_e(empty_msg)}</p></div>'
    items.sort(key=lambda t: (-t[2], -t[1]))
    rows = [_bar(nm, n, r, "sys", base if enough else None) for nm, n, r in items[:limit]]
    more = len(items) - limit
    tail = f'<p class="tr-note">+{more} more {unit} with {MIN_BUCKET}+ calls.</p>' if more > 0 else ""
    return f'<div class="tr-block"><h4>{_e(title)}</h4>' + "".join(rows) + tail + "</div>"


def _instruments(sc, base, enough):
    src = sc.get("by_instrument") if isinstance(sc.get("by_instrument"), dict) else None
    if src is None:
        return ""
    return _ranked("By instrument", src, base, enough, INSTRUMENT_LIMIT,
                   f"No instrument has {MIN_BUCKET} checked calls yet.", "instruments")


def _arms(sc, base, enough):
    out = ""
    bb = sc.get("by_basis")
    if isinstance(bb, dict) and bb:
        out += _ranked("By method", bb, base, enough, 6, f"No method has {MIN_BUCKET} checked calls yet.", "methods")
    ba = sc.get("by_arm")
    if isinstance(ba, dict) and ba:
        out += _ranked("Test groups", ba, base, enough, 6, f"No test group has {MIN_BUCKET} checked calls yet.", "groups")
    return out


def _confidence(sc, base, enough):
    bc = sc.get("by_confidence") if isinstance(sc.get("by_confidence"), dict) else {}
    rows, got = [], {}
    for k in ("low", "medium", "high"):
        n, r = _bucket(bc.get(k))
        got[k] = (n, r)
        rows.append(_bar(k.capitalize(), n, r, "sys", base if enough else None))
    if got["high"][1] is not None and got["low"][1] is not None:
        d = round((got["high"][1] - got["low"][1]) * 100)
        if d > 0:
            verdict = f"Yes so far: high-confidence calls are right {d} points more often than low."
        elif d < 0:
            verdict = f"No so far: high-confidence calls are right {abs(d)} points less often than low."
        else:
            verdict = "No difference so far between high and low confidence."
    else:
        verdict = "Too early to say."
    calmsg = ("High-confidence calls beat low-confidence calls by at least 5 points"
              if sc.get("calibrated") is True else
              "Not yet (confidence labels are not separating hits from misses)")
    return ('<div class="tr-block"><h4>Does high confidence beat low?</h4>'
            + "".join(rows) + f'<p class="tr-note">{_e(verdict)}</p>'
            f'<p class="tr-note">{_e(calmsg)}</p></div>')


def _topics(sc, base, enough):
    return _ranked("By topic", sc.get("by_topic"), base, enough, TOPIC_LIMIT,
                   f"No topic has {MIN_BUCKET} checked calls yet.", "topics")


def _clean_line(s):
    s = str(s).strip()
    while s[:1] in "-*+>#• \t":
        s = s[1:]
        if not s:
            break
    s = " ".join(s.split())
    return s if len(s) <= LINE_CAP else s[:LINE_CAP - 1].rstrip() + "…"


def _arm_row(label, n, hits, colour):
    """Experiment arm: always show the count; a rate only once there are MIN_BUCKET calls."""
    if n >= MIN_BUCKET:
        return _bar(label, n, _rate(hits / n), colour)
    return (f'<div class="tr-row"><span class="tr-lab">{_e(label)}</span><span class="tr-track"></span>'
            f'<span class="tr-val tr-dim">{n} graded</span></div>')


def _experiments(experiments, sc=None):
    ex = _d(experiments)
    run = ex.get("running")
    hist = ex.get("history") if isinstance(ex.get("history"), list) else []
    if not isinstance(run, dict) and not hist:
        return ""
    out = ['<div class="tr-block"><h4>What it is trying</h4>']
    if isinstance(run, dict):
        txt = _clean_line(run.get("text") or run.get("id") or "A test")
        share = _rate(run.get("share"))
        sh = f" on {_pct(share)} of calls" if share is not None else ""
        out.append(f'<p class="tr-try">{_e(txt)}<span class="tr-n">{_e(sh)}</span></p>')
        nc, hc = _n(run.get("n_control")), _n(run.get("hits_control"))
        nv, hv = _n(run.get("n_variant")), _n(run.get("hits_variant"))
        if not (nc or nv):  # fall back to the scorecard's per-arm numbers
            arms = _d(_d(sc).get("by_arm"))
            c, v = _d(arms.get("control")), _d(arms.get("variant"))
            nc, hc = _n(c.get("n")), _n(_num(c.get("hits")))
            nv, hv = _n(v.get("n")), _n(_num(v.get("hits")))
        hc, hv = min(hc, nc), min(hv, nv)
        out.append(_arm_row("Current way", nc, hc, "base"))
        out.append(_arm_row("New way", nv, hv, "sys"))
        if nc < EXP_MIN or nv < EXP_MIN:
            verdict = f"Too early (needs {EXP_MIN} graded calls per arm)."
        else:
            diff = round((hv / nv - hc / nc) * 100)
            if diff == 0:
                verdict = "Level so far."
            else:
                who = "New way" if diff > 0 else "Current way"
                verdict = f"{who} is ahead by {abs(diff)} points"
                if min(nc, nv) < EXP_SOLID:
                    verdict += ", but samples are still small"
                verdict += "."
        out.append(f'<p class="tr-note">{_e(verdict)}</p>')
    else:
        out.append('<p class="tr-note">Nothing running right now.</p>')
    fin = [h for h in hist if isinstance(h, dict)][-3:][::-1]
    if fin:
        out.append('<ul class="tr-exp">')
        for h in fin:
            res = _clean_line(h.get("result") or "finished")
            out.append(f'<li><span class="tr-res">{_e(res)}</span> {_e(_clean_line(h.get("text") or h.get("id") or ""))}</li>')
        out.append("</ul>")
    out.append("</div>")
    return "".join(out)


def _settings(settings):
    st = _d(settings)
    if not st:
        return ""
    bits = []
    k = _num(st.get("shrinkage_k"))
    lb = _num(st.get("lookback_days"))
    mb = _num(st.get("min_bucket"))
    if k is not None:
        bits.append(f"shrinkage k={k:g}")
    if lb is not None:
        bits.append(f"lookback {int(lb)} days")
    if mb is not None:
        bits.append(f"min sample {int(mb)}")
    hist = st.get("history") if isinstance(st.get("history"), list) else []
    last = ""
    if hist:
        h = hist[-1]
        if isinstance(h, dict):
            t = h.get("text") or h.get("change") or h.get("what")
            if not t:
                t = ", ".join(f"{a}: {b}" for a, b in h.items()
                              if isinstance(b, (str, int, float)) and not isinstance(b, bool))
        else:
            t = h
        last = _clean_line(t) if t else ""
    if not bits and not last:
        return ""
    out = ['<div class="tr-block"><h4>Its own settings</h4>']
    if bits:
        out.append(f'<p class="tr-note tr-tight">{_e(", ".join(bits))}</p>')
    if last:
        out.append(f'<p class="tr-note">Last change: {_e(last)}</p>')
    out.append("</div>")
    return "".join(out)


def _lohi(p):
    lo = _rate(p.get("up_lo") if "up_lo" in p else p.get("lo"))
    hi = _rate(p.get("up_hi") if "up_hi" in p else p.get("hi"))
    return lo, hi


def _rule_text(p):
    drv, dr, w = p.get("driver"), p.get("dir"), _num(p.get("window_days"))
    if drv and dr in ("up", "down") and w:
        return _clean_line(f"{drv} {dr} sharply over {int(w)}d")
    return _clean_line(p.get("driver_rule") or p.get("driver") or "pattern")


def _priors_block(priors):
    pats = _patterns(priors)
    if not pats:
        return ""

    def rank(t):
        p, n, pu = t
        lo, hi = _lohi(p)
        solid = lo is not None and hi is not None and (lo > 0.5 or hi < 0.5)
        return (0 if solid else 1, -abs(pu - 0.5), -n)
    pats.sort(key=rank)
    rows = []
    for p, n, pu in pats[:PRIOR_LIMIT]:
        rule = _rule_text(p)
        tgt = _clean_line(p.get("target") or "")
        hz = _num(p.get("horizon_days"))
        hz_t = f", {int(hz)}d" if hz is not None and hz > 0 else ""
        lo, hi = _lohi(p)
        iv = f" ({_pct(lo)}–{_pct(hi)})" if lo is not None and hi is not None else ""
        flag = ""
        if p.get("unstable") is True or p.get("stable") is False:
            flag = '<span class="tr-warn">unstable</span>'
        rows.append(f'<li><span class="tr-pr">{_e(rule)} → {_e(tgt)}{_e(hz_t)}{flag}</span>'
                    f'<span class="tr-pv">went up {_pct(pu)}{_e(iv)} <span class="tr-n">{n} independent cases</span></span></li>')
    hist = _history_phrase(priors)
    inst = _d(_d(priors).get("instruments"))
    meta = f"From {hist}" if hist else "From past market data"
    if inst:
        meta += f", {len(inst)} instrument{'s' if len(inst) != 1 else ''}"
    return ('<div class="tr-block"><h4>History, not a forecast</h4>'
            f'<p class="tr-note tr-tight">{_e(meta)}. Many patterns were searched, and most show little.</p>'
            '<ul class="tr-pri">' + "".join(rows) + "</ul></div>")


_KIND = {"cards": "card", "map": "map", "story": "story", "stories": "story"}


def _plain(line):
    """Swap internal jargon in a changelog line for plain words (display only; escaped later)."""
    def started(m):
        kind = _KIND.get(m.group(1).lower(), m.group(1))
        return f"Started a test: a different wording on {m.group(2)}% of the {kind} prompts"
    line = re.sub(r"experiment \S+ started on (\w+) prompts for (\d+)% of calls", started, line, flags=re.I)
    line = re.sub(r"experiment \S+ (adopted|cancelled|canceled|ended)", lambda m: f"A test was {m.group(1)}", line, flags=re.I)
    line = re.sub(r"meta: settings kept \([^)]*\)(?:;[^\[]*)?", "Checked its own learning settings: no change ", line, flags=re.I)
    line = re.sub(r"meta: settings changed", "Changed its own learning settings", line, flags=re.I)
    line = re.sub(r"^(\S+(?: \d\d:\d\d)?\s+)?meta:\s*", lambda m: m.group(1) or "", line)
    return line


def _learned(changelog_text, lessons_text):
    lines = []
    if isinstance(changelog_text, str):
        for raw in changelog_text.splitlines():
            if raw.lstrip().startswith("#"):
                continue
            c = _clean_line(_plain(raw.strip()))
            if c:
                lines.append(c)
            if len(lines) >= CHANGELOG_LINES:
                break
    out = ['<div class="tr-block"><h4>What it learned recently</h4>']
    if lines:
        out.append('<ul class="tr-log">' + "".join(f"<li>{_e(x)}</li>" for x in lines) + "</ul>")
    else:
        out.append('<p class="tr-note">Nothing yet. Lessons appear here once calls have been checked.</p>')
    if isinstance(lessons_text, str) and lessons_text.strip():
        t = lessons_text.strip()
        capped = len(t) > LESSONS_CAP
        t = t[:LESSONS_CAP] + ("\n…" if capped else "")
        out.append(f'<details class="tr-less"><summary>Lessons</summary><pre>{_e(t)}</pre></details>')
    out.append("</div>")
    return "".join(out)


def track_record_html(scorecard, changelog_text="", lessons_text="", priors=None, settings=None,
                      experiments=None, state=None):
    sc, n, hr, base, enough = _sc(scorecard)
    priors = priors if isinstance(priors, dict) else None
    state = state if isinstance(state, dict) else None
    as_of = sc.get("as_of")
    asof = f'<p class="tr-asof">Checked up to {_e(as_of)}.</p>' if isinstance(as_of, str) and as_of else ""
    legend = ""
    if enough:
        legend = ('<p class="tr-key"><i class="k sys"></i>System <i class="k base"></i>Momentum baseline '
                  '<i class="k mk"></i>baseline mark</p>'
                  '<p class="tr-note">Momentum baseline: how often "the last move keeps going" would have been right.</p>')
    body = [_nextday(sc), _overall(sc, n, hr, base, enough)]
    if enough:
        body += [_horizons(sc, base, enough), _instruments(sc, base, enough), _arms(sc, base, enough),
                 _confidence(sc, base, enough), _topics(sc, base, enough)]
    body += [_experiments(experiments, sc), _settings(settings), _priors_block(priors),
             _learned(changelog_text, lessons_text)]
    return (f'<section class="track" aria-label="Track record">'
            f'<details class="tr-det"><summary>{_summary(sc, n, hr, base, enough, priors, state)}</summary>'
            f'<div class="tr-body">{_note_block(state)}{legend}<div class="tr-grid">{"".join(body)}</div>{asof}</div></details></section>')


CSS = """
    .track { --tr-sys:#2a78d6; --tr-base:#94a3b8; --tr-trk:rgba(100,116,139,.16); --tr-tick:#475569;
             --tr-neg:#c0392b; --tr-warn-bg:#fdf0d5; --tr-warn-bd:#d9a21b;
             background: var(--card); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 24px;
             box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
    @media (prefers-color-scheme: dark) {
      .track { --tr-sys:#3987e5; --tr-base:#64748b; --tr-trk:rgba(148,163,184,.2); --tr-tick:#cbd5e1;
               --tr-neg:#e5776b; --tr-warn-bg:rgba(217,162,27,.16); --tr-warn-bd:#b8861a; }
    }
    .tr-det > summary { list-style: none; cursor: pointer; padding: 14px 18px; display: flex; flex-wrap: wrap;
             align-items: center; gap: 2px 12px; position: relative; padding-right: 40px; }
    .tr-det > summary::-webkit-details-marker { display: none; }
    .tr-det > summary::after { content: ""; position: absolute; right: 18px; top: 50%; width: 7px; height: 7px;
             border: solid var(--muted); border-width: 0 2px 2px 0; transform: translateY(-70%) rotate(45deg);
             transition: transform .15s; }
    .tr-det[open] > summary::after { transform: translateY(-30%) rotate(-135deg); }
    .tr-det > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: 12px; }
    .tr-title { font-weight: 700; font-size: 15px; }
    .tr-line { font-size: 14px; color: var(--muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; min-width: 0; }
    .tr-hist { flex: 1 0 100%; font-size: 12.5px; color: var(--muted); }
    .tr-body { padding: 0 18px 16px; border-top: 1px solid var(--border); }
    .tr-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 4px 28px; }
    .tr-block { padding-top: 14px; min-width: 0; }
    .tr-block h4 { font-size: 13px; font-weight: 700; margin: 0 0 8px; }
    .tr-weekly { padding-top: 14px; }
    .tr-weekly h4 { font-size: 13px; font-weight: 700; margin: 0 0 4px; }
    .tr-weekly p { font-size: 13.5px; margin: 0 0 3px; line-height: 1.45; overflow-wrap: anywhere; }
    .tr-row { display: grid; grid-template-columns: minmax(0, 8.5rem) 1fr 6.6rem; align-items: center;
             gap: 10px; min-height: 26px; font-size: 13px; }
    .tr-lab { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .tr-track { position: relative; height: 8px; border-radius: 4px; background: var(--tr-trk); }
    .tr-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; }
    .tr-fill.sys, .tr-fill.pos { background: var(--tr-sys); }
    .tr-fill.base { background: var(--tr-base); }
    .tr-fill.neg { background: var(--tr-neg); left: auto; }
    .tr-fill.pos { border-radius: 0 4px 4px 0; }
    .tr-fill.neg { border-radius: 4px 0 0 4px; }
    .tr-zero { position: absolute; left: 50%; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; background: var(--tr-tick); }
    .tr-tick { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; background: var(--tr-tick); opacity: .85; }
    .tr-val { text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .tr-n { font-weight: 400; color: var(--muted); font-size: 12px; }
    .tr-dim { font-weight: 400; color: var(--muted); font-size: 12px; }
    .tr-note { font-size: 12.5px; color: var(--muted); margin: 6px 0 0; line-height: 1.45; overflow-wrap: anywhere; }
    .tr-tight { margin-top: 0; }
    .tr-chip { display: inline-block; margin: 8px 0 0; padding: 3px 10px; font-size: 12.5px; font-weight: 600;
             border: 1px solid var(--tr-warn-bd); background: var(--tr-warn-bg); color: var(--text); border-radius: 999px; }
    .tr-warn { display: inline-block; margin-left: 6px; padding: 0 7px; font-size: 11.5px; font-weight: 600;
             border: 1px solid var(--tr-warn-bd); background: var(--tr-warn-bg); color: var(--text); border-radius: 999px; }
    .tr-asof { font-size: 12px; color: var(--muted); margin: 14px 0 0; }
    .tr-key { font-size: 12px; color: var(--muted); margin: 12px 0 0; }
    .tr-key .k { display: inline-block; width: 14px; height: 6px; border-radius: 3px; margin: 0 4px 1px 10px; vertical-align: middle; }
    .tr-key .k:first-child { margin-left: 0; }
    .tr-key .k.sys { background: var(--tr-sys); } .tr-key .k.base { background: var(--tr-base); }
    .tr-key .k.mk { width: 2px; height: 11px; border-radius: 0; background: var(--tr-tick); margin-left: 10px; }
    .tr-prog { display: block; height: 8px; border-radius: 4px; background: var(--tr-trk); overflow: hidden; margin-top: 10px; }
    .tr-prog-s { margin-top: 0; width: 96px; flex: 0 0 auto; }
    .tr-prog b { display: block; height: 100%; background: var(--tr-sys); border-radius: 4px; }
    .tr-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .tr-stat { background: var(--code-bg); border-radius: 8px; padding: 8px 10px; min-width: 0; }
    .tr-stat b { display: block; font-size: 20px; font-variant-numeric: tabular-nums; line-height: 1.2; }
    .tr-stat span { display: block; font-size: 12px; color: var(--muted); line-height: 1.3; }
    .tr-try { font-size: 13px; margin: 0 0 8px; overflow-wrap: anywhere; }
    .tr-try .tr-n { margin-left: 6px; }
    .tr-exp, .tr-pri { list-style: none; margin: 8px 0 0; padding: 0; font-size: 13px; }
    .tr-exp li { margin-bottom: 4px; overflow-wrap: anywhere; color: var(--muted); }
    .tr-res { font-weight: 600; color: var(--text); margin-right: 4px; }
    .tr-pri li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0 12px; padding: 5px 0;
             border-top: 1px solid var(--border); }
    .tr-pri li:first-child { border-top: 0; }
    .tr-pr { min-width: 0; overflow-wrap: anywhere; flex: 1 1 14rem; }
    .tr-pv { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }
    .tr-log { margin: 0; padding-left: 18px; font-size: 13px; }
    .tr-log li { margin-bottom: 5px; overflow-wrap: anywhere; }
    .tr-less { margin-top: 8px; font-size: 13px; }
    .tr-less summary { cursor: pointer; color: var(--accent); }
    .tr-less pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 13px; line-height: 1.5; color: var(--text);
             background: var(--code-bg); border-radius: 6px; padding: 10px 12px; margin-top: 6px; max-height: 280px; overflow-y: auto; overflow-x: hidden; }
    @media (max-width: 480px) {
      .tr-det > summary { padding: 12px 14px; padding-right: 36px; }
      .tr-body { padding: 0 14px 14px; }
      .tr-grid { grid-template-columns: minmax(0, 1fr); }
      .tr-row { grid-template-columns: 1fr auto; row-gap: 3px; padding: 3px 0; }
      .tr-lab { grid-column: 1; grid-row: 1; }
      .tr-val { grid-column: 2; grid-row: 1; }
      .tr-track { grid-column: 1 / -1; grid-row: 2; }
      .tr-pv { white-space: normal; }
    }
"""
