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
          <a href="{esc(it['link'])}" class="title" target="_blank" rel="noopener">{esc(it['title'])}</a>
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


def intel_html(intel):
    if not intel:
        return ""
    parts = ['<div class="intel"><h3>🔗 Intelligence Brief</h3>']
    if intel.get("bottom_line"):
        parts.append(f'<p class="bottom-line">{esc(intel["bottom_line"])}</p>')
    if intel.get("causal_chains"):
        parts.append("<h4>Causal chains</h4>")
        for c in intel["causal_chains"]:
            parts.append(f'<div class="intel-item"><b>{esc(c.get("title"))}</b><br>{esc(" → ".join(c.get("chain", [])))}</div>')
    if intel.get("second_order"):
        parts.append("<h4>Second-order effects</h4>")
        for e in intel["second_order"]:
            parts.append(f'<div class="intel-item">{esc(e.get("event"))} ⇒ <b>{esc(e.get("effect"))}</b></div>')
    if intel.get("scenarios"):
        parts.append("<h4>If-then scenarios (1-2 weeks)</h4>")
        for sc in intel["scenarios"]:
            watch = f'<br><span class="muted">Watch: {esc(sc["signpost"])}</span>' if sc.get("signpost") else ""
            parts.append(f'<div class="intel-item"><b>If</b> {esc(sc.get("if"))} <b>then</b> {esc(sc.get("then"))} '
                         f'<span class="badge">{esc(sc.get("likelihood", "?"))}</span>{watch}</div>')
    if intel.get("contrarian"):
        parts.append("<h4>Contrarian signals</h4>")
        for c in intel["contrarian"]:
            parts.append(f'<div class="intel-item">{esc(c.get("signal"))}<br><span class="muted">Consensus: '
                         f'{esc(c.get("mainstream_view"))}</span></div>')
    parts.append('<p class="muted small">AI-generated analysis from headlines; not investment advice.</p></div>')
    return "".join(parts)


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
      {intel_html(digest.get("intelligence"))}
    </section>
    """


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

    <div class="updated">Last updated: {esc(last_updated) if last_updated else '—'}</div>

    {cards}

    <footer>
      Auto-generated by News Digest Bot &bull; <a href="https://github.com/manangoesagain/news-pipeline" style="color: var(--accent); text-decoration: none;">GitHub</a>
    </footer>
  </div>
</body>
</html>"""


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    digests = load_digests()
    html = generate_html(digests)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated {OUTPUT_FILE} with {len(digests)} digests")
