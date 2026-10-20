#!/usr/bin/env python3
"""
Build static HTML page from saved digests.
Reads _data/digest_*.json, keeps latest 4, writes index.html.
"""

import json
import glob
import os
import html
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
    return html.escape(text or "")


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


def domain_html(domain, items):
    if not items:
        return ""
    emoji = EMOJI.get(domain, "•")
    items_html = []
    for it in items:
        items_html.append(f"""
        <div class="item">
          <a href="{esc(it['link'])}" class="title" target="_blank" rel="noopener">{esc(it['title'])}</a>
          <div class="meta">{esc(it['source'])} &bull; {esc(it['summary'])}</div>
        </div>
        """)
    return f"""
    <details class="domain">
      <summary>{emoji} {domain.upper()} <span class="count">({len(items)})</span></summary>
      <div class="domain-content">
        {''.join(items_html)}
      </div>
    </details>
    """


def digest_card(digest):
    timestamp = digest.get("datetime", "")
    time_label = format_time(timestamp) if timestamp else "Unknown time"
    breaking = digest.get("breaking", [])
    domains = digest.get("domains", {})

    breaking_html = ""
    if breaking:
        items_html = []
        for it in breaking[:5]:
            items_html.append(f"""
            <div class="item breaking-item">
              <a href="{esc(it['link'])}" class="title" target="_blank" rel="noopener">{esc(it['title'])}</a>
              <div class="meta">{esc(it['source'])} &bull; {esc(it['summary'])}</div>
            </div>
            """)
        breaking_html = f"""
        <div class="breaking">
          <div class="breaking-label">🔴 Breaking — Last 6 Hours</div>
          {''.join(items_html)}
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
        <p class="placeholder">Live market data coming next step (S&amp;P 500, Nifty 50, TSX, USD/INR, USD/CAD, Gold, WTI, BTC)</p>
      </div>
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
      <p>Free sources &bull; 4 runs daily &bull; Toronto time (ET)</p>
    </header>

    <div class="updated">Last updated: {esc(last_updated) if last_updated else '—'}</div>

    {cards}

    <footer>
      Auto-generated by News Digest Bot &bull; <a href="https://github.com" style="color: var(--accent); text-decoration: none;">GitHub</a>
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
