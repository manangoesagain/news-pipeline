#!/usr/bin/env python3
"""
Daily News Digest — RSS Fetcher + Telegram Sender
Runs on GitHub Actions 4x daily (Toronto ET).
Free, non-paywalled sources only.
"""

import os
import sys
import html
import re
import hashlib
import json
import glob
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests

# --- Secrets from GitHub Actions env ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "PYRAMIDMANAN@gmail.com")

if not BOT_TOKEN or not CHAT_ID:
    print("FATAL: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as repository secrets.")
    sys.exit(1)

if not RESEND_API_KEY:
    print("WARN: RESEND_API_KEY not set — email delivery will be skipped.")

ET = ZoneInfo("America/Toronto")
NOW = datetime.now(ET)
LOOKBACK_HOURS = 24
MAX_PER_DOMAIN = 8
SAVE_DIR = "_data"
MAX_SAVED_DIGESTS = 4

# (domain_tag, source_name, rss_url)
# All free / non-paywalled. Broken feeds are skipped at runtime.
SOURCES = [
    # BBC
    ("Geopolitics & Diplomacy", "BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Finance & Banking", "BBC Business", "http://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Technology & Semiconductors", "BBC Tech", "http://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Wars & Conflicts", "BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    # The Guardian
    ("Geopolitics & Diplomacy", "The Guardian World", "https://www.theguardian.com/world/rss"),
    ("Finance & Banking", "The Guardian Business", "https://www.theguardian.com/business/rss"),
    ("Technology & Semiconductors", "The Guardian Tech", "https://www.theguardian.com/technology/rss"),
    ("Wars & Conflicts", "The Guardian World", "https://www.theguardian.com/world/rss"),
    # Al Jazeera
    ("Geopolitics & Diplomacy", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Wars & Conflicts", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    # NPR
    ("Geopolitics & Diplomacy", "NPR", "https://feeds.npr.org/1001/rss.xml"),
    # CBC
    ("Finance & Banking", "CBC News", "https://www.cbc.ca/cmlink/rss-canada"),
    ("Geopolitics & Diplomacy", "CBC News", "https://www.cbc.ca/cmlink/rss-canada"),
    # NDTV
    ("Geopolitics & Diplomacy", "NDTV", "https://feeds.feedburner.com/ndtvnews-latest"),
    ("Finance & Banking", "NDTV", "https://feeds.feedburner.com/ndtvnews-latest"),
    # The Hindu
    ("Geopolitics & Diplomacy", "The Hindu", "https://www.thehindu.com/feeder/default.rss"),
    ("Finance & Banking", "The Hindu Business", "https://www.thehindu.com/business/rss"),
    # Moneycontrol
    ("Finance & Banking", "Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("Stock Markets & Trading", "Moneycontrol Markets", "https://www.moneycontrol.com/rss/market.xml"),
    ("Commodities & Prices", "Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml"),
    # TechCrunch
    ("AI & ML", "TechCrunch", "https://techcrunch.com/feed/"),
    ("Technology & Semiconductors", "TechCrunch", "https://techcrunch.com/feed/"),
    # Ars Technica
    ("Technology & Semiconductors", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    ("AI & ML", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    # The Verge
    ("Technology & Semiconductors", "The Verge", "https://www.theverge.com/rss/index.xml"),
    ("AI & ML", "The Verge", "https://www.theverge.com/rss/index.xml"),
    # CNBC
    ("Finance & Banking", "CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("Stock Markets & Trading", "CNBC Markets", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    # MarketWatch
    ("Stock Markets & Trading", "MarketWatch", "https://feeds.marketwatch.com/marketwatch/topStories/"),
    ("Finance & Banking", "MarketWatch", "https://feeds.marketwatch.com/marketwatch/topStories/"),
]

DOMAIN_KEYWORDS = {
    "AI & ML": ["ai", "artificial intelligence", "machine learning", "llm", "gpt", "openai",
                "anthropic", "nvidia", "semiconductor", "chip", "neural", "generative"],
    "Technology & Semiconductors": ["tech", "technology", "semiconductor", "chip", "apple",
        "microsoft", "google", "meta", "amazon", "intel", "amd", "qualcomm", "tsmc",
        "gadget", "software", "hardware"],
    "Finance & Banking": ["bank", "banking", "federal reserve", "fed", "ecb", "rbi",
        "bank of canada", "loan", "credit", "fintech", "payment", "fiscal", "budget"],
    "Stock Markets & Trading": ["stock", "share", "market", "nasdaq", "s&p", "dow",
        "nifty", "sensex", "tsx", "etf", "ipo", "earnings", "dividend", "trading"],
    "Geopolitics & Diplomacy": ["diplomacy", "foreign", "minister", "summit", "treaty",
        "alliance", "embassy", "sanctions", "trade deal", "geopolitic"],
    "Wars & Conflicts": ["war", "conflict", "invasion", "missile", "drone", "casualty",
        "ceasefire", "troop", "military", "army", "navy", "air force", "frontline"],
    "Commodities & Prices": ["oil", "crude", "gold", "silver", "copper", "natural gas",
        "commodity", "inflation", "price", "wheat", "corn", "bitcoin", "crypto", "ethereum"],
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

EMOJI = {
    "AI & ML": "🤖",
    "Technology & Semiconductors": "💻",
    "Finance & Banking": "💰",
    "Stock Markets & Trading": "📈",
    "Geopolitics & Diplomacy": "🌍",
    "Wars & Conflicts": "⚔️",
    "Commodities & Prices": "📊",
}


def save_digest(domain_items: dict, breaking: list):
    os.makedirs(SAVE_DIR, exist_ok=True)
    timestamp = NOW.strftime("%Y%m%d_%H%M")
    data = {
        "timestamp": timestamp,
        "datetime": NOW.isoformat(),
        "breaking": breaking,
        "domains": {domain: domain_items[domain] for domain in DOMAIN_ORDER},
    }
    filepath = os.path.join(SAVE_DIR, f"digest_{timestamp}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Keep only the latest N digest files
    files = sorted(glob.glob(os.path.join(SAVE_DIR, "digest_*.json")), reverse=True)
    for old_file in files[MAX_SAVED_DIGESTS:]:
        try:
            os.remove(old_file)
            print(f"[CLEANUP] removed {old_file}")
        except Exception as e:
            print(f"[CLEANUP ERROR] {old_file}: {e}")

    print(f"[SAVE] digest written to {filepath}")


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl().lower().rstrip("/")


def title_hash(title: str) -> str:
    return hashlib.md5(title.lower().strip().encode()).hexdigest()


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(entry) -> datetime:
    for field in ("published_parsed", "updated_parsed"):
        if entry.get(field):
            try:
                return datetime(*entry[field][:6], tzinfo=__import__("datetime").timezone.utc)
            except Exception:
                pass
    return NOW


def classify_domain(title: str, summary: str, source_domain: str) -> str:
    text = f"{title} {summary}".lower()
    best = source_domain
    best_score = 0
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best = domain
    return best


def fetch_feed(name: str, url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (NewsDigestBot/1.0)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            print(f"[SKIP] {name}: feed parse failed ({feed.bozo_exception})")
            return None
        if not feed.entries:
            print(f"[SKIP] {name}: no entries")
            return None
        print(f"[OK] {name}: {len(feed.entries)} entries")
        return feed.entries
    except Exception as e:
        print(f"[SKIP] {name}: {e}")
        return None


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = [message[i:i + 3900] for i in range(0, len(message), 3900)]
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": CHAT_ID,
            "text": f"(continued)\n{chunk}" if i > 0 else chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code != 200:
            print(f"[TELEGRAM ERROR] {resp.status_code}: {resp.text}")
        else:
            print(f"[TELEGRAM] sent part {i + 1}/{len(chunks)}")


def send_email(subject: str, html_content: str):
    if not RESEND_API_KEY:
        print("[EMAIL] skipped — RESEND_API_KEY not set")
        return

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": "News Digest <onboarding@resend.dev>",
        "to": [RECIPIENT_EMAIL],
        "subject": subject,
        "html": html_content,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    if resp.status_code == 200:
        print("[EMAIL] sent successfully")
    else:
        print(f"[EMAIL ERROR] {resp.status_code}: {resp.text}")


def build_email_html(domain_items: dict, breaking: list) -> str:
    date_str = NOW.strftime("%b %d, %Y")
    time_str = NOW.strftime("%I:%M %p ET")

    def esc(text):
        return html.escape(text or "")

    rows = []

    if breaking:
        rows.append("""
        <tr>
          <td style="padding: 12px 0; border-bottom: 2px solid #e74c3c;">
            <div style="font-size: 14px; font-weight: bold; color: #e74c3c; margin-bottom: 6px;">🔴 BREAKING — Last 6 Hours</div>
        """)
        for it in breaking[:5]:
            rows.append(f"""
            <div style="margin-bottom: 8px;">
              <a href="{esc(it['link'])}" style="color: #1a73e8; text-decoration: none; font-weight: 600;">{esc(it['title'])}</a>
              <div style="font-size: 12px; color: #555; margin-top: 2px;">{esc(it['source'])} — {esc(it['summary'])}</div>
            </div>
            """)
        rows.append("</td></tr>")

    for domain in DOMAIN_ORDER:
        items = domain_items[domain]
        if not items:
            continue
        emoji = EMOJI.get(domain, "•")
        rows.append(f"""
        <tr>
          <td style="padding: 16px 0 10px 0; border-bottom: 1px solid #eee;">
            <div style="font-size: 16px; font-weight: bold; color: #222; margin-bottom: 8px;">{emoji} {domain.upper()}</div>
        """)
        for it in items:
            rows.append(f"""
            <div style="margin-bottom: 10px;">
              <a href="{esc(it['link'])}" style="color: #1a73e8; text-decoration: none; font-weight: 600; display: block; margin-bottom: 3px;">{esc(it['title'])}</a>
              <div style="font-size: 13px; color: #444; line-height: 1.4;">{esc(it['summary'])}</div>
              <div style="font-size: 11px; color: #888; margin-top: 2px;">Source: {esc(it['source'])}</div>
            </div>
            """)
        rows.append("</td></tr>")

    rows.append("""
    <tr>
      <td style="padding: 16px 0 0 0; border-top: 2px solid #333;">
        <div style="font-size: 16px; font-weight: bold; color: #222; margin-bottom: 8px;">📉 MARKET SNAPSHOT</div>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; color: #444;">
          <tr><td style="padding: 4px 0;">S&P 500</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">Nasdaq</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">Nifty 50</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">TSX</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">USD/INR</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">USD/CAD</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">Gold</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">WTI Crude</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
          <tr><td style="padding: 4px 0;">BTC</td><td style="padding: 4px 0; text-align: right;">— coming next step</td></tr>
        </table>
      </td>
    </tr>
    """)

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>News Brief — {date_str}</title>
</head>
<body style="margin: 0; padding: 0; background: #f5f5f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background: #f5f5f5;">
    <tr>
      <td align="center" style="padding: 20px 10px;">
        <table cellpadding="0" cellspacing="0" style="max-width: 600px; width: 100%; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
          
          <!-- Header -->
          <tr>
            <td style="padding: 20px 24px; background: #1a73e8; color: #ffffff;">
              <div style="font-size: 20px; font-weight: bold;">📰 News Brief</div>
              <div style="font-size: 13px; color: #cbd6f2; margin-top: 4px;">{date_str} &nbsp;•&nbsp; {time_str}</div>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding: 0 24px 24px 24px;">
              <table cellpadding="0" cellspacing="0" width="100%">
                {''.join(rows)}
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 16px 24px; background: #f8f9fa; color: #888; font-size: 11px; text-align: center; border-top: 1px solid #eee;">
              — Auto-generated by News Digest Bot • Free sources only • Sent via Resend
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    return html_body


def main():
    from datetime import timezone
    cutoff = NOW - timedelta(hours=LOOKBACK_HOURS)
    seen_hashes = set()
    seen_links = set()
    domain_items = {d: [] for d in DOMAIN_ORDER}

    for domain_tag, source_name, url in SOURCES:
        entries = fetch_feed(source_name, url)
        if not entries:
            continue
        for entry in entries:
            try:
                pub = parse_date(entry)
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    continue

                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", entry.get("description", "")))
                link = entry.get("link", "")

                if not title or not link:
                    continue

                h = title_hash(title)
                nl = normalize_url(link)
                if h in seen_hashes or nl in seen_links:
                    continue
                seen_hashes.add(h)
                seen_links.add(nl)

                final_domain = classify_domain(title, summary, domain_tag)

                domain_items[final_domain].append({
                    "title": title,
                    "summary": summary[:220] + ("…" if len(summary) > 220 else ""),
                    "link": link,
                    "source": source_name,
                    "published": pub.astimezone(ET),
                })
            except Exception as e:
                print(f"[PARSE ERROR] {source_name}: {e}")
                continue

    for domain in DOMAIN_ORDER:
        domain_items[domain].sort(key=lambda x: x["published"], reverse=True)
        domain_items[domain] = domain_items[domain][:MAX_PER_DOMAIN]

    lines = []
    lines.append(f"🕐 {NOW.strftime('%I:%M %p ET — %A, %b %d')}")
    lines.append("")

    breaking = []
    for domain in DOMAIN_ORDER:
        for it in domain_items[domain]:
            age = (NOW - it["published"]).total_seconds() / 3600
            if age <= 6:
                breaking.append(it)

    if breaking:
        lines.append("🔴 BREAKING (last 6h)")
        for it in breaking[:5]:
            lines.append(f"• [{it['source']}] {it['title']} — {it['summary']} — {it['link']}")
        lines.append("")

    for domain in DOMAIN_ORDER:
        items = domain_items[domain]
        if not items:
            continue
        lines.append(f"{EMOJI.get(domain, '•')} {domain.upper()}")
        for it in items:
            lines.append(f"• [{it['source']}] {it['title']} — {it['summary']} — {it['link']}")
        lines.append("")

    lines.append("📉 MARKET SNAPSHOT")
    lines.append("• S&P 500 / Nifty 50 / TSX — free market API coming next step")
    lines.append("• USD/INR | USD/CAD | Gold | WTI | BTC — coming next step")
    lines.append("")
    lines.append("— Auto-generated by News Digest Bot")

    message = "\n".join(lines)
    print(message[:800] + ("…" if len(message) > 800 else ""))

    # Save digest data for the web page
    save_digest(domain_items, breaking)

    send_telegram(message)

    # Send email with same content
    email_html = build_email_html(domain_items, breaking)
    email_subject = f"📰 News Brief — {NOW.strftime('%b %d')} {NOW.strftime('%I:%M %p ET')}"
    send_email(email_subject, email_html)


if __name__ == "__main__":
    main()