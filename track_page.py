"""
Track record strip for the digest page.

track_record_html(scorecard, changelog_text, lessons_text="") -> str   (never empty)
telegram_line(scorecard) -> str                                         (plain text)
CSS                                                                     (include once in the page <style>)

Everything is escaped, no scripts. A percentage is never shown for fewer than
MIN_BUCKET calls, and the strip says "not enough data" instead of guessing.
"""

import html

MIN_BUCKET = 5        # fewest calls behind any percentage we print
ENOUGH_TARGET = 20    # checked calls before the overall number is trusted
LESSONS_CAP = 6000
LINE_CAP = 260
CHANGELOG_LINES = 5
TOPIC_LIMIT = 8


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
    n_graded = _n(sc.get("n_price_graded")) if "n_price_graded" in sc else _n(sc.get("n_graded"))
    hr = _rate(sc.get("hit_rate")) if n_graded >= MIN_BUCKET else None
    base = _rate(sc.get("hit_rate_baseline")) if n_graded >= MIN_BUCKET else None
    enough = bool(sc.get("enough_data")) and hr is not None
    return sc, n_graded, hr, base, enough


def _unit(sc):
    return "checked calls" if "n_price_graded" in sc else "graded calls"


def telegram_line(scorecard):
    sc, n, hr, base, enough = _sc(scorecard)
    if not enough:
        return f"Track record: building ({n} of {ENOUGH_TARGET} {_unit(sc)} so far)"
    line = f"Track record: {_pct(hr)} of {n} {_unit(sc)} right"
    if base is not None:
        line += f" (momentum baseline {_pct(base)})"
    hn, hrate = _bucket((sc.get("by_confidence") or {}).get("high") if isinstance(sc.get("by_confidence"), dict) else None)
    if hrate is not None:
        line += f"; high-confidence calls {_pct(hrate)}"
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


def _progress(n):
    pct = min(100.0, 100.0 * n / ENOUGH_TARGET)
    return (f'<div class="tr-prog" role="progressbar" aria-valuemin="0" aria-valuemax="{ENOUGH_TARGET}" '
            f'aria-valuenow="{min(n, ENOUGH_TARGET)}" aria-label="Calls counted so far">'
            f'<b style="width:{pct:.1f}%"></b></div>')


def _summary(sc, n, hr, base, enough):
    if not enough:
        return (f'<span class="tr-title">Track record</span>'
                f'<span class="tr-line">Not enough {_unit(sc)} yet: {min(n, 9999)} of {ENOUGH_TARGET}</span>')
    bit = f"{_pct(hr)} right across {n} {_unit(sc)}"
    if base is not None:
        bit += f" (momentum baseline {_pct(base)})"
    return f'<span class="tr-title">Track record</span><span class="tr-line">{_e(bit)}</span>'


def _overall(sc, n, hr, base, enough):
    out = ['<div class="tr-block"><h4>Overall</h4>']
    if not enough:
        out.append(f'<p class="tr-note">Not enough {_unit(sc)} yet: {n} of {ENOUGH_TARGET}. '
                   'Results are shown once there are enough to mean something.</p>')
        out.append(_progress(n))
    else:
        out.append(_bar("The system", n, hr, "sys"))
        if base is not None:
            out.append(_bar("Momentum baseline", n, base, "base", show_n=False))
        else:
            out.append('<p class="tr-note">No baseline available yet.</p>')
        r30, p30 = sc.get("recent_30d"), sc.get("prev_30d")
        rn, rr = _bucket(r30)
        pn, pr = _bucket(p30)
        if rr is not None and pr is not None:
            d = round((rr - pr) * 100)
            way = "up" if d > 0 else "down" if d < 0 else "level"
            out.append(f'<p class="tr-note">Last 30 days {_pct(rr)} (n={rn}) vs the 30 before {_pct(pr)} (n={pn}): '
                       f'{way}{" " + str(abs(d)) + " points" if d else ""}.</p>')
    n_open = _n(sc.get("n_open"))
    if n_open:
        out.append(f'<p class="tr-note">{n_open} more call{"s" if n_open != 1 else ""} still waiting to be checked.</p>')
    out.append("</div>")
    return "".join(out)


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
    bt = sc.get("by_topic") if isinstance(sc.get("by_topic"), dict) else {}
    items = []
    for name, b in bt.items():
        n, r = _bucket(b)
        if r is not None:
            items.append((str(name), n, r))
    if not items:
        return ('<div class="tr-block"><h4>By topic</h4>'
                f'<p class="tr-note">No topic has {MIN_BUCKET} checked calls yet.</p></div>')
    items.sort(key=lambda t: (-t[2], -t[1]))
    rows = [_bar(nm, n, r, "sys", base if enough else None) for nm, n, r in items[:TOPIC_LIMIT]]
    more = len(items) - TOPIC_LIMIT
    tail = f'<p class="tr-note">+{more} more topics with {MIN_BUCKET}+ calls.</p>' if more > 0 else ""
    return '<div class="tr-block"><h4>By topic</h4>' + "".join(rows) + tail + "</div>"


def _clean_line(s):
    s = str(s).strip()
    while s[:1] in "-*+>#• \t":
        s = s[1:]
        if not s:
            break
    s = " ".join(s.split())
    return s if len(s) <= LINE_CAP else s[:LINE_CAP - 1].rstrip() + "…"


def _learned(changelog_text, lessons_text):
    lines = []
    if isinstance(changelog_text, str):
        for raw in changelog_text.splitlines():
            if raw.lstrip().startswith("#"):
                continue
            c = _clean_line(raw)
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


def track_record_html(scorecard, changelog_text="", lessons_text=""):
    sc, n, hr, base, enough = _sc(scorecard)
    as_of = sc.get("as_of")
    asof = f'<p class="tr-asof">Checked up to {_e(as_of)}.</p>' if isinstance(as_of, str) and as_of else ""
    legend = ""
    if enough:
        legend = ('<p class="tr-key"><i class="k sys"></i>System <i class="k base"></i>Momentum baseline '
                  '<i class="k mk"></i>baseline mark</p>'
                  '<p class="tr-note">Momentum baseline: how often "the last move keeps going" would have been right.</p>')
    body = [_overall(sc, n, hr, base, enough)]
    if enough:
        body.append(_confidence(sc, base, enough))
        body.append(_topics(sc, base, enough))
    body.append(_learned(changelog_text, lessons_text))
    return (f'<section class="track" aria-label="Track record">'
            f'<details class="tr-det"><summary>{_summary(sc, n, hr, base, enough)}</summary>'
            f'<div class="tr-body">{legend}<div class="tr-grid">{"".join(body)}</div>{asof}</div></details></section>')


CSS = """
    .track { --tr-sys:#2a78d6; --tr-base:#94a3b8; --tr-trk:rgba(100,116,139,.16); --tr-tick:#475569;
             background: var(--card); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 24px;
             box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
    @media (prefers-color-scheme: dark) {
      .track { --tr-sys:#3987e5; --tr-base:#64748b; --tr-trk:rgba(148,163,184,.2); --tr-tick:#cbd5e1; }
    }
    .tr-det > summary { list-style: none; cursor: pointer; padding: 14px 18px; display: flex; flex-wrap: wrap;
             align-items: baseline; gap: 2px 12px; position: relative; padding-right: 40px; }
    .tr-det > summary::-webkit-details-marker { display: none; }
    .tr-det > summary::after { content: ""; position: absolute; right: 18px; top: 50%; width: 7px; height: 7px;
             border: solid var(--muted); border-width: 0 2px 2px 0; transform: translateY(-70%) rotate(45deg);
             transition: transform .15s; }
    .tr-det[open] > summary::after { transform: translateY(-30%) rotate(-135deg); }
    .tr-det > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: 12px; }
    .tr-title { font-weight: 700; font-size: 15px; }
    .tr-line { font-size: 14px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .tr-body { padding: 0 18px 16px; border-top: 1px solid var(--border); }
    .tr-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 4px 28px; }
    .tr-block { padding-top: 14px; min-width: 0; }
    .tr-block h4 { font-size: 13px; font-weight: 700; margin: 0 0 8px; }
    .tr-row { display: grid; grid-template-columns: minmax(0, 8.5rem) 1fr 6.6rem; align-items: center;
             gap: 10px; min-height: 26px; font-size: 13px; }
    .tr-lab { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .tr-track { position: relative; height: 8px; border-radius: 4px; background: var(--tr-trk); }
    .tr-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; }
    .tr-fill.sys { background: var(--tr-sys); }
    .tr-fill.base { background: var(--tr-base); }
    .tr-tick { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; background: var(--tr-tick); opacity: .85; }
    .tr-val { text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .tr-n { font-weight: 400; color: var(--muted); font-size: 12px; }
    .tr-dim { font-weight: 400; color: var(--muted); font-size: 12px; }
    .tr-note { font-size: 12.5px; color: var(--muted); margin: 6px 0 0; line-height: 1.45; }
    .tr-asof { font-size: 12px; color: var(--muted); margin: 14px 0 0; }
    .tr-key { font-size: 12px; color: var(--muted); margin: 12px 0 0; }
    .tr-key .k { display: inline-block; width: 14px; height: 6px; border-radius: 3px; margin: 0 4px 1px 10px; vertical-align: middle; }
    .tr-key .k:first-child { margin-left: 0; }
    .tr-key .k.sys { background: var(--tr-sys); } .tr-key .k.base { background: var(--tr-base); }
    .tr-key .k.mk { width: 2px; height: 11px; border-radius: 0; background: var(--tr-tick); margin-left: 10px; }
    .tr-prog { height: 8px; border-radius: 4px; background: var(--tr-trk); overflow: hidden; margin-top: 10px; }
    .tr-prog b { display: block; height: 100%; background: var(--tr-sys); border-radius: 4px; }
    .tr-log { margin: 0; padding-left: 18px; font-size: 13px; }
    .tr-log li { margin-bottom: 5px; overflow-wrap: anywhere; }
    .tr-less { margin-top: 8px; font-size: 13px; }
    .tr-less summary { cursor: pointer; color: var(--accent); }
    .tr-less pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.5 ui-monospace, Menlo, Consolas, monospace;
             background: var(--code-bg); border-radius: 6px; padding: 10px 12px; margin-top: 6px; max-height: 280px; overflow: auto; }
    @media (max-width: 480px) {
      .tr-det > summary { padding: 12px 14px; padding-right: 36px; }
      .tr-body { padding: 0 14px 14px; }
      .tr-row { grid-template-columns: 1fr auto; row-gap: 3px; padding: 3px 0; }
      .tr-lab { grid-column: 1; grid-row: 1; }
      .tr-val { grid-column: 2; grid-row: 1; }
      .tr-track { grid-column: 1 / -1; grid-row: 2; }
    }
"""
