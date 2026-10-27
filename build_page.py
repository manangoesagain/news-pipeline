#!/usr/bin/env python3
"""
Build static HTML page from saved digests.
Reads _data/digest_*.json, keeps latest 4, writes index.html.
"""

import json
import glob
import os
import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from track_page import CSS as TRACK_CSS, track_record_html
from graph_page import CSS as GRAPH_CSS, SCRIPT as GRAPH_SCRIPT, connection_map, scenario_tree, causal_flows

ET = ZoneInfo("America/Toronto")
DATA_DIR = "_data"
OUTPUT_FILE = "index.html"

EMOJI = {
    "AI & ML": "🤖",
    "Technology & Semiconductors": "💻",
    "Finance & Banking": "💰",
    "Stock Markets & Trading": "📈",
    "Geopolitics & Diplomacy": "🌍",
    "Wars & Conflicts": "⚔️",
    "Commodities & Prices": "📊",
}

DOMAIN_ORDER = [
    "AI & ML",
    "Technology & Semiconductors",
    "Finance & Banking",
    "Stock Markets & Trading",
    "Geopolitics & Diplomacy",
    "Wars & Conflicts",
    "Commodities & Prices",
]


def esc(text):
    # Also drops "(ids 6, 33)" citations the LLM sometimes leaves in the brief
    text = re.sub(r"\s*\((?:ids?|per [^()]*?id)\s*[\d,\s]+(?:and\s*\d+)?\)", "", str(text or ""))
    return html.escape(text)


def load_digests():
    if not os.path.isdir(DATA_DIR):
        return []
    files = sorted(glob.glob(os.path.join(DATA_DIR, "digest_*.json")), reverse=True)
    digests = []
    for f in files[:4]:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                digests.append(data)
        except Exception as e:
            print(f"[SKIP] {f}: {e}")
    return digests


def format_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str).astimezone(ET)
        return dt.strftime("%I:%M %p ET — %b %d, %Y")
    except Exception:
        return iso_str


def item_html(it, extra_class=""):
    sources = it.get("sources") or [it.get("source", "")]
    src = ", ".join(sources[:3]) + (f" +{len(sources) - 3}" if len(sources) > 3 else "")
    why = f'<div class="why">↳ {esc(it["why"])}</div>' if it.get("why") else ""
    summary = f" &bull; {esc(it['summary'])}" if it.get("summary") else ""
    return f"""
        <div class="item {extra_class}">
          <a href="{esc(it['link'] if str(it.get('link', '')).lower().startswith(('http://', 'https://')) else '#')}" class="title" target="_blank" rel="noopener">{esc(it['title'])}</a>
          <div class="meta">{esc(src)}{summary}</div>
          {why}
        </div>
        """


def domain_html(domain, items):
    if not items:
        return ""
    emoji = EMOJI.get(domain, "•")
    return f"""
    <details class="domain">
      <summary>{emoji} {domain.upper()} <span class="count">({len(items)})</span></summary>
      <div class="domain-content">
        {''.join(item_html(it) for it in items)}
      </div>
    </details>
    """


def fmt_price(m):
    if m.get("price") is None:
        return "n/a"
    return f"{m['price']:,.{m.get('decimals', 2)}f}"


def market_html(rows):
    if not rows:
        return '<p class="placeholder">Market data unavailable for this run.</p>'
    cells = []
    for m in rows:
        c = m.get("change_pct")
        cls = "up" if (c or 0) > 0 else ("down" if (c or 0) < 0 else "")
        change = f"{'▲' if (c or 0) > 0 else '▼' if (c or 0) < 0 else '•'} {c:+.2f}%" if c is not None else ""
        cells.append(f"""
        <div class="tick">
          <div class="tick-label">{esc(m['label'])}</div>
          <div class="tick-price">{esc(fmt_price(m))}</div>
          <div class="tick-change {cls}">{esc(change)}</div>
        </div>""")
    return f'<div class="ticks">{"".join(cells)}</div>'


def _safe(fn, *args):
    """Run one renderer; malformed LLM output must never take the whole page build down."""
    try:
        out = fn(*args)
        return out if isinstance(out, str) else ""
    except Exception as e:
        print(f"[WARN] {getattr(fn, '__name__', 'renderer')} skipped: {type(e).__name__}: {e}")
        return ""


def _dicts(v):
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _intel_text_html(intel):
    """Plain text sections of the brief (shown collapsed under 'Details')."""
    parts = []
    chains = _dicts(intel.get("causal_chains"))
    if chains:
        parts.append("<h4>Causal chains</h4>")
        for c in chains:
            steps = c.get("chain") if isinstance(c.get("chain"), list) else []
            parts.append(f'<div class="intel-item"><b>{esc(c.get("title"))}</b><br>{esc(" → ".join(str(x) for x in steps if isinstance(x, (str, int, float))))}</div>')
    second = _dicts(intel.get("second_order"))
    if second:
        parts.append("<h4>Second-order effects</h4>")
        for e in second:
            who = f' <span class="muted">(affects: {esc(e["who_is_affected"])})</span>' if e.get("who_is_affected") else ""
            parts.append(f'<div class="intel-item">{esc(e.get("event"))} ⇒ <b>{esc(e.get("effect"))}</b>{who}</div>')
    scenarios = _dicts(intel.get("scenarios"))
    if scenarios:
        parts.append("<h4>If-then scenarios (1-2 weeks)</h4>")
        for sc in scenarios:
            watch = f'<br><span class="muted">Watch: {esc(sc["signpost"])}</span>' if sc.get("signpost") else ""
            parts.append(f'<div class="intel-item"><b>If</b> {esc(sc.get("if"))} <b>then</b> {esc(sc.get("then"))} '
                         f'<span class="badge">{esc(sc.get("likelihood", "?"))}</span>{watch}</div>')
    contrarian = _dicts(intel.get("contrarian"))
    if contrarian:
        parts.append("<h4>Contrarian signals</h4>")
        for c in contrarian:
            why = f'<br><span class="muted">Why it matters: {esc(c["why_it_matters"])}</span>' if c.get("why_it_matters") else ""
            parts.append(f'<div class="intel-item">{esc(c.get("signal"))}<br><span class="muted">Consensus: '
                         f'{esc(c.get("mainstream_view"))}</span>{why}</div>')
    return "".join(parts)


def intel_text_html(intel):
    return _safe(_intel_text_html, intel if isinstance(intel, dict) else {})


def intel_html(intel, intel_lite=None):
    try:
        return _intel_html(intel, intel_lite)
    except Exception as e:
        print(f"[WARN] intel_html skipped: {type(e).__name__}: {e}")
        return ""


def _intel_html(intel, intel_lite=None):
    intel = intel if isinstance(intel, dict) else {}
    intel_lite = intel_lite if isinstance(intel_lite, dict) else {}
    graph = intel.get("graph") if isinstance(intel.get("graph"), dict) else None
    lite = False
    if not graph and isinstance(intel_lite.get("graph"), dict) and intel_lite["graph"]:
        graph, lite = intel_lite["graph"], True
    if not intel and not graph:
        return ""
    parts = ['<div class="intel viz"><h3>🔗 Intelligence Brief</h3>']
    if intel.get("bottom_line"):
        parts.append(f'<p class="bottom-line">{esc(intel["bottom_line"])}</p>')
    note = "Built from story threads, no AI brief this run." if lite else ""
    parts.append(_safe(connection_map, graph, note))
    parts.append(_safe(scenario_tree, intel.get("scenarios")))
    parts.append(_safe(causal_flows, intel.get("causal_chains")))
    text = intel_text_html(intel)
    if text:
        parts.append(f'<details class="idetails"><summary>Details</summary><div>{text}</div></details>')
    if intel:
        parts.append('<p class="muted small">AI-generated analysis from headlines; not investment advice.</p>')
    parts.append("</div>")
    return "".join(parts)


def _patterns_html(digest):
    pat = digest.get("patterns") if isinstance(digest.get("patterns"), dict) else {}
    acc = digest.get("prediction_accuracy") if isinstance(digest.get("prediction_accuracy"), dict) else {}
    heating_all = _dicts(pat.get("heating"))
    heating = [h for h in heating_all if h.get("thread_id")][:5] or heating_all[:5]
    cooling = _dicts(pat.get("cooling"))[:4]
    cross = _dicts(pat.get("cross_domain"))[:4]
    if not (heating or cooling or cross or acc.get("resolved")):
        return ""
    parts = ['<div class="intel"><h3>Patterns</h3>']
    if heating:
        parts.append('<div class="intel-item"><b>Heating up:</b> ' + ", ".join(esc(h.get("term")) for h in heating) + "</div>")
    if cooling:
        parts.append('<div class="intel-item"><b>Cooling off:</b> ' + ", ".join(esc(h.get("term")) for h in cooling) + "</div>")
    if cross:
        def _doms(c):
            d = c.get("domains")
            return ", ".join(str(x) for x in d) if isinstance(d, list) else ""
        parts.append('<div class="intel-item"><b>Active across several topics:</b><br>' +
                     "<br>".join(f"{esc(c.get('title'))} <span class=\"muted\">({esc(_doms(c))})</span>" for c in cross) + "</div>")
    if acc.get("resolved"):
        parts.append(f'<div class="intel-item"><b>Prediction track record:</b> {esc(str(acc.get("happened", 0)))} happened, '
                     f'{esc(str(acc.get("partly", 0)))} partly, {esc(str(acc.get("missed", 0)))} missed of {esc(str(acc.get("resolved")))} scored</div>')
    parts.append("</div>")
    return "".join(parts)


def patterns_html(digest):
    return _safe(_patterns_html, digest if isinstance(digest, dict) else {})


def digest_card(digest):
    timestamp = digest.get("datetime", "")
    time_label = format_time(timestamp) if timestamp else "Unknown time"
    breaking = digest.get("breaking", [])
    domains = digest.get("domains", {})

    breaking_html = ""
    if breaking:
        breaking_html = f"""
        <div class="breaking">
          <div class="breaking-label">🔴 Breaking</div>
          {''.join(item_html(it, "breaking-item") for it in breaking[:5])}
        </div>
        """

    domains_html = ""
    for domain in DOMAIN_ORDER:
        domains_html += domain_html(domain, domains.get(domain, []))

    return f"""
    <section class="digest-card">
      <div class="card-header">
        <h2>{time_label}</h2>
      </div>
      {breaking_html}
      <div class="domains">
        {domains_html}
      </div>
      <div class="market-snapshot">
        <h3>📉 Market Snapshot</h3>
        {market_html(digest.get("market"))}
      </div>
      {intel_html(digest.get("intelligence"), digest.get("intel_lite"))}
      {patterns_html(digest)}
    </section>
    """


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def track_strip():
    """Track record strip from _data/_learning; missing files give the honest empty state."""
    try:
        sc = None
        try:
            sc = json.loads(_read_text(os.path.join(DATA_DIR, "_learning", "scorecard.json")) or "null")
        except Exception:
            sc = None
        return track_record_html(sc if isinstance(sc, dict) else {},
                                 _read_text(os.path.join(DATA_DIR, "_learning", "changelog.md")),
                                 _read_text(os.path.join(DATA_DIR, "_learning", "lessons.md")))
    except Exception as e:
        print(f"[WARN] track record skipped: {type(e).__name__}: {e}")
        return ""


def generate_html(digests):
    cards = ""
    for d in digests:
        cards += digest_card(d)

    if not cards:
        cards = '<p class="empty">No digests available yet. The first run will appear here.</p>'

    last_updated = ""
    if digests:
        last_updated = format_time(digests[0].get("datetime", ""))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>News Digest</title>
  <style>
    :root {{
      --bg: #f5f5f7;
      --card: #ffffff;
      --text: #1a1a1a;
      --muted: #666666;
      --border: #e0e0e0;
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --breaking: #dc2626;
      --code-bg: #f3f4f6;
    }}

    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0f172a;
        --card: #1e293b;
        --text: #f1f5f9;
        --muted: #94a3b8;
        --border: #334155;
        --accent: #3b82f6;
        --accent-hover: #60a5fa;
        --breaking: #ef4444;
        --code-bg: #1e293b;
      }}
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 20px;
    }}

    .container {{
      max-width: 720px;
      margin: 0 auto;
    }}

    header {{
      text-align: center;
      padding: 30px 0 20px;
      border-bottom: 2px solid var(--border);
      margin-bottom: 30px;
    }}

    header h1 {{
      font-size: 28px;
      margin-bottom: 8px;
    }}

    header p {{
      color: var(--muted);
      font-size: 14px;
    }}

    .updated {{
      text-align: center;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 20px;
    }}

    .digest-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }}

    .card-header h2 {{
      font-size: 20px;
      margin-bottom: 16px;
      color: var(--accent);
    }}

    .breaking {{
      background: #fef2f2;
      border: 1px solid var(--breaking);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 20px;
    }}

    @media (prefers-color-scheme: dark) {{
      .breaking {{
        background: rgba(239, 68, 68, 0.15);
        border-color: var(--breaking);
      }}
    }}

    .breaking-label {{
      color: var(--breaking);
      font-weight: 700;
      font-size: 14px;
      margin-bottom: 8px;
      display: block;
    }}

    .domains {{
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}

    details.domain {{
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}

    details.domain summary {{
      padding: 12px 16px;
      font-weight: 600;
      font-size: 15px;
      cursor: pointer;
      list-style: none;
      background: var(--code-bg);
      color: var(--text);
    }}

    details.domain summary .count {{
      float: right;
      font-weight: 400;
      opacity: 0.8;
    }}

    details.domain[open] summary {{
      border-bottom: 1px solid var(--border);
    }}

    .domain-content {{
      padding: 0 16px 16px;
    }}

    .item {{
      margin-bottom: 12px;
    }}

    .item.breaking-item {{
      background: var(--code-bg);
      padding: 10px 14px;
      border-radius: 6px;
      margin-bottom: 8px;
    }}

    .title {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
      font-size: 15px;
      display: block;
      margin-bottom: 4px;
    }}

    .title:hover {{
      text-decoration: underline;
    }}

    .meta {{
      font-size: 13px;
      color: var(--muted);
    }}

    .market-snapshot {{
      margin-top: 24px;
      padding-top: 16px;
      border-top: 1px solid var(--border);
    }}

    .market-snapshot h3 {{
      font-size: 16px;
      margin-bottom: 8px;
    }}

    .placeholder {{
      color: var(--muted);
      font-size: 13px;
    }}

    .why {{
      font-size: 13px;
      color: var(--accent);
      margin-top: 2px;
    }}

    .ticks {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 8px;
    }}

    .tick {{
      background: var(--code-bg);
      border-radius: 8px;
      padding: 8px 10px;
    }}

    .tick-label {{ font-size: 12px; color: var(--muted); }}
    .tick-price {{ font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }}
    .tick-change {{ font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }}
    .tick-change.up {{ color: #16a34a; }}
    .tick-change.down {{ color: var(--breaking); }}

    .intel {{
      margin-top: 24px;
      padding-top: 16px;
      border-top: 2px solid #7c3aed;
    }}

    .intel h3 {{ font-size: 16px; color: #7c3aed; margin-bottom: 8px; }}
    .intel h4 {{ font-size: 14px; margin: 14px 0 6px; }}
    .intel-item {{ font-size: 14px; margin-bottom: 8px; }}
    .bottom-line {{ font-style: italic; background: var(--code-bg); padding: 10px 12px; border-radius: 6px; }}
    .badge {{ font-size: 11px; color: #7c3aed; border: 1px solid #7c3aed; border-radius: 10px; padding: 0 6px; }}
    .muted {{ color: var(--muted); }}
    .small {{ font-size: 12px; margin-top: 8px; }}

    .empty {{
      text-align: center;
      color: var(--muted);
      padding: 40px 0;
    }}

    footer {{
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      margin-top: 40px;
      padding-top: 20px;
      border-top: 1px solid var(--border);
    }}

    {GRAPH_CSS}

    {TRACK_CSS}

    @media (max-width: 480px) {{
      body {{ padding: 12px; }}
      .digest-card {{ padding: 16px; }}
      header h1 {{ font-size: 22px; }}
      .card-header h2 {{ font-size: 17px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>📰 News Digest</h1>
      <p>Free sources &bull; 4 runs daily (8, 11, 3, 6) &bull; Toronto time (ET)</p>
    </header>

    <p style="text-align:center;margin-bottom:14px"><a href="archive.html" style="color: var(--accent); text-decoration: none;">Archive, search &amp; story threads &rarr;</a></p>

    <div class="updated">Last updated: {esc(last_updated) if last_updated else '—'}</div>

    {track_strip()}

    {cards}

    <footer>
      Auto-generated by News Digest Bot &bull; <a href="https://github.com/manangoesagain/news-pipeline" style="color: var(--accent); text-decoration: none;">GitHub</a>
    </footer>
  </div>
  <script>{GRAPH_SCRIPT}</script>
</body>
</html>"""


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    digests = load_digests()
    html = generate_html(digests)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated {OUTPUT_FILE} with {len(digests)} digests")
    from archive_page import PAGE
    with open("archive.html", "w", encoding="utf-8") as f:
        f.write(PAGE)
