#!/usr/bin/env python3
"""
Daily News Digest — fetch, dedupe, rank, deliver.
Runs on GitHub Actions hourly; acts only on the 8/11/15/18 Toronto-time slots.

Sources: free RSS feeds, Google News RSS (incl. Reuters/AP via site: search),
GNews and NewsData.io APIs. An LLM (Gemini or NVIDIA NIM) ranks and merges stories when available;
otherwise a keyword/heuristic fallback is used. Delivery: Telegram + email,
plus JSON files in _data/ for the web page and the /deepdive bot.
"""

import os
import sys
import html
import re
import hashlib
import json
import glob
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

import llm
import market

# --- Secrets from GitHub Actions env ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "PYRAMIDMANAN@gmail.com")
GNEWS_API_KEY = os.environ.get("GNEWS_API_KEY")
NEWSDATA_API_KEY = os.environ.get("NEWSDATA_API_KEY")

# --- Run controls (workflow_dispatch inputs) ---
FORCE_RUN = os.environ.get("FORCE_RUN", "").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "").lower() == "true"
FORCE_INTEL = os.environ.get("FORCE_INTEL", "").lower() == "true"

ET = ZoneInfo("America/Toronto")
NOW = datetime.now(ET)
LOOKBACK_HOURS = 24
BREAKING_HOURS = 6
MAX_PER_DOMAIN = 5
MAX_BREAKING = 5
SAVE_DIR = "_data"
MAX_SAVED_DIGESTS = 4
ARTICLE_STORE = os.path.join(SAVE_DIR, "articles.json")
ARTICLE_STORE_DAYS = 7
STATE_FILE = os.path.join(SAVE_DIR, "state.json")

# Allowed run hours in America/Toronto time; the 18:00 run carries the intelligence brief
ALLOWED_HOURS = [8, 11, 15, 18]
INTEL_HOUR = 18
# A slot can still be delivered this many hours late (GitHub cron can lag up to ~1h)
SLOT_GRACE_HOURS = 3

# Some feeds (CBC) stall on anything but a plain browser user agent
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

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

# (domain hint, source name, rss url). The same feed URL may be listed once only;
# its domain hint is just a tie-breaker for the classifier.
RSS_SOURCES = [
    ("Geopolitics & Diplomacy", "BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Finance & Banking", "BBC Business", "http://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Technology & Semiconductors", "BBC Tech", "http://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Geopolitics & Diplomacy", "The Guardian", "https://www.theguardian.com/world/rss"),
    ("Finance & Banking", "The Guardian", "https://www.theguardian.com/business/rss"),
    ("Technology & Semiconductors", "The Guardian", "https://www.theguardian.com/technology/rss"),
    ("Geopolitics & Diplomacy", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Geopolitics & Diplomacy", "NPR", "https://feeds.npr.org/1001/rss.xml"),
    ("Finance & Banking", "CBC News", "https://www.cbc.ca/webfeed/rss/rss-canada"),
    ("Finance & Banking", "CBC Business", "https://www.cbc.ca/webfeed/rss/rss-business"),
    ("Geopolitics & Diplomacy", "NDTV", "https://feeds.feedburner.com/ndtvnews-latest"),
    ("Geopolitics & Diplomacy", "The Hindu", "https://www.thehindu.com/feeder/default.rss"),
    ("Finance & Banking", "The Hindu Business", "https://www.thehindu.com/business/feeder/default.rss"),
    # Moneycontrol RSS returns 403 to all clients; Economic Times and Mint cover Indian markets
    ("Stock Markets & Trading", "Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Stock Markets & Trading", "Mint Markets", "https://www.livemint.com/rss/markets"),
    ("AI & ML", "TechCrunch", "https://techcrunch.com/feed/"),
    ("Technology & Semiconductors", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    ("Technology & Semiconductors", "The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Stock Markets & Trading", "CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("Finance & Banking", "CNBC Economy", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Stock Markets & Trading", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
]

# Google News RSS searches: (domain hint, query, edition). Reuters and AP have no
# public RSS any more, so they come in via site: searches.
GOOGLE_NEWS_QUERIES = [
    ("Geopolitics & Diplomacy", "site:reuters.com world", "US"),
    ("Finance & Banking", "site:reuters.com business", "US"),
    ("Geopolitics & Diplomacy", "site:apnews.com", "US"),
    ("AI & ML", "artificial intelligence OR OpenAI OR Anthropic OR Gemini model", "US"),
    ("Technology & Semiconductors", "semiconductor OR chipmaker OR TSMC OR Nvidia", "US"),
    ("Stock Markets & Trading", "Sensex OR Nifty stock market", "IN"),
    ("Finance & Banking", "RBI OR \"Bank of Canada\" OR \"Federal Reserve\" rates", "US"),
    ("Commodities & Prices", "oil prices OR gold prices OR commodities", "US"),
    ("Wars & Conflicts", "war OR ceasefire OR military strike", "US"),
    ("Geopolitics & Diplomacy", "India news", "IN"),
    ("Geopolitics & Diplomacy", "Canada news", "CA"),
    ("Geopolitics & Diplomacy", "China economy OR Beijing policy", "US"),
]
GN_EDITIONS = {"US": ("en-US", "US", "US:en"), "IN": ("en-IN", "IN", "IN:en"), "CA": ("en-CA", "CA", "CA:en")}

# GNews free plan: 100 requests/day, 10 articles each. 6 calls x 4 runs = 24/day.
GNEWS_CALLS = [
    ("Geopolitics & Diplomacy", "world", "us"),
    ("Finance & Banking", "business", "us"),
    ("Technology & Semiconductors", "technology", "us"),
    ("Geopolitics & Diplomacy", "nation", "in"),
    ("Finance & Banking", "business", "in"),
    ("Geopolitics & Diplomacy", "nation", "ca"),
]

# NewsData.io free plan: 200 credits/day, 1 credit per call. 3 calls x 4 runs = 12/day.
NEWSDATA_CALLS = [
    ("Finance & Banking", "business"),
    ("Technology & Semiconductors", "technology,science"),
    ("Geopolitics & Diplomacy", "politics,world"),
]

DOMAIN_KEYWORDS = {
    "AI & ML": ["ai", "a.i.", "artificial intelligence", "machine learning", "llm", "llms",
                "chatgpt", "gpt", "openai", "anthropic", "claude", "gemini", "deepmind",
                "neural network", "generative", "chatbot", "deepfake", "copilot"],
    "Technology & Semiconductors": ["tech", "technology", "semiconductor", "semiconductors",
        "chip", "chips", "chipmaker", "nvidia", "apple", "microsoft", "google", "meta",
        "amazon", "intel", "amd", "qualcomm", "tsmc", "smartphone", "software", "hardware",
        "cybersecurity", "hack", "data centre", "data center", "startup"],
    "Finance & Banking": ["bank", "banks", "banking", "federal reserve", "the fed", "ecb", "rbi",
        "bank of canada", "interest rate", "interest rates", "rate cut", "loan", "loans",
        "credit", "fintech", "payments", "fiscal", "budget", "tariff", "tariffs", "gdp",
        "economy", "recession", "tax"],
    "Stock Markets & Trading": ["stock", "stocks", "shares", "market", "markets", "nasdaq",
        "s&p", "s&p 500", "dow", "nifty", "sensex", "tsx", "etf", "ipo", "earnings",
        "dividend", "trading", "investors", "wall street", "bond", "bonds", "yields"],
    "Geopolitics & Diplomacy": ["diplomacy", "diplomatic", "foreign minister", "summit",
        "treaty", "alliance", "embassy", "sanctions", "trade deal", "geopolitical",
        "election", "prime minister", "president", "parliament", "g7", "g20", "nato", "un"],
    "Wars & Conflicts": ["war", "wars", "conflict", "invasion", "missile", "missiles", "drone",
        "drones", "airstrike", "airstrikes", "casualties", "ceasefire", "troops", "military",
        "army", "navy", "frontline", "hamas", "hezbollah", "shelling", "killed"],
    "Commodities & Prices": ["oil", "crude", "brent", "wti", "opec", "gold", "silver", "copper",
        "natural gas", "lng", "commodity", "commodities", "inflation", "wheat", "corn",
        "bitcoin", "crypto", "ethereum", "fuel prices"],
}

# Whole-word patterns: (?<!\w) / (?!\w) instead of \b so "s&p" and "a.i." work too
DOMAIN_PATTERNS = {
    d: [re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", re.IGNORECASE) for kw in kws]
    for d, kws in DOMAIN_KEYWORDS.items()
}

# Geography priority: India, US, Canada first, then China
REGION_KEYWORDS = {
    "India": ["india", "indian", "delhi", "new delhi", "mumbai", "bengaluru", "modi", "rbi",
              "sensex", "nifty", "rupee", "bjp", "adani", "reliance", "tata"],
    "US": ["US", "U.S.", "united states", "america", "american", "washington", "trump",
           "white house", "congress", "federal reserve", "wall street", "pentagon"],
    "Canada": ["canada", "canadian", "ottawa", "toronto", "carney", "bank of canada", "tsx",
               "ontario", "quebec", "alberta", "british columbia"],
    "China": ["china", "chinese", "beijing", "xi jinping", "shanghai", "yuan", "hong kong", "taiwan"],
}
# All-caps keywords ("US", "U.S.") are case-sensitive so the pronoun "us" doesn't match
REGION_PATTERNS = {
    r: [re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", 0 if kw.isupper() else re.IGNORECASE)
        for kw in kws]
    for r, kws in REGION_KEYWORDS.items()
}
REGION_WEIGHT = {"India": 1.5, "US": 1.3, "Canada": 1.2, "China": 0.7, "Global": 0.5, "Other": 0.0}


# ---------------------------------------------------------------- helpers

def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl().lower().rstrip("/")


def title_hash(title: str) -> str:
    return hashlib.md5(title.lower().strip().encode()).hexdigest()


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def parse_date(entry):
    for field in ("published_parsed", "updated_parsed"):
        if entry.get(field):
            try:
                return datetime(*entry[field][:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def classify_domain(title: str, summary: str, hint: str) -> str:
    """Whole-word keyword scoring; title hits count double, source hint breaks ties."""
    best, best_score = hint, 0.0
    for domain, patterns in DOMAIN_PATTERNS.items():
        score = sum(2 for p in patterns if p.search(title)) + sum(1 for p in patterns if p.search(summary))
        if domain == hint:
            score += 0.5
        if score > best_score:
            best, best_score = domain, score
    return best


def detect_region(text: str) -> str:
    scores = {r: sum(1 for p in pats if p.search(text)) for r, pats in REGION_PATTERNS.items()}
    region, score = max(scores.items(), key=lambda kv: kv[1])
    return region if score > 0 else "Global"


def title_tokens(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", title.lower())
    stop = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "as",
            "at", "by", "with", "from", "after", "over", "says", "say", "new", "its", "it", "be"}
    return {w for w in words if w not in stop and len(w) > 2}


# ---------------------------------------------------------------- fetchers

def make_item(title, summary, link, source, published, hint, origin):
    return {
        "title": clean_text(title),
        "summary": truncate(clean_text(summary), 300),
        "link": link or "",
        "source": source,
        "published": published.astimezone(ET),
        "hint": hint,
        "origin": origin,
    }


def fetch_feed(name: str, url: str):
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            print(f"[SKIP] {name}: feed parse failed ({feed.bozo_exception})")
            return []
        print(f"[OK] {name}: {len(feed.entries)} entries")
        return feed.entries
    except Exception as e:
        print(f"[SKIP] {name}: {e}")
        return []


def fetch_rss():
    items = []
    for hint, name, url in RSS_SOURCES:
        for e in fetch_feed(name, url):
            pub = parse_date(e)
            if pub:
                items.append(make_item(e.get("title", ""), e.get("summary", e.get("description", "")),
                                       e.get("link", ""), name, pub, hint, "rss"))
    return items


def fetch_google_news():
    items = []
    for hint, query, edition in GOOGLE_NEWS_QUERIES:
        hl, gl, ceid = GN_EDITIONS[edition]
        url = (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:1d')}"
               f"&hl={hl}&gl={gl}&ceid={ceid}")
        for e in fetch_feed(f"Google News: {query}", url)[:20]:
            pub = parse_date(e)
            if not pub:
                continue
            title = e.get("title", "")
            source = (e.get("source") or {}).get("title") or "Google News"
            # Google News titles end with " - Source"
            if title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            items.append(make_item(title, "", e.get("link", ""), source, pub, hint, "google"))
    return items


def fetch_gnews():
    if not GNEWS_API_KEY:
        print("[GNEWS] skipped — GNEWS_API_KEY not set")
        return []
    items = []
    for n, (hint, category, country) in enumerate(GNEWS_CALLS):
        if n:
            time.sleep(1.5)  # free plan rejects bursts (HTTP 429)
        try:
            resp = requests.get("https://gnews.io/api/v4/top-headlines", timeout=15, params={
                "category": category, "lang": "en", "country": country, "max": 10,
                "apikey": GNEWS_API_KEY,
            })
            if resp.status_code != 200:
                print(f"[GNEWS] {category}/{country}: HTTP {resp.status_code} {resp.text[:150]}")
                continue
            arts = resp.json().get("articles", [])
            print(f"[OK] GNews {category}/{country}: {len(arts)} articles")
            for a in arts:
                pub = parse_iso(a.get("publishedAt"))
                if pub:
                    items.append(make_item(a.get("title", ""), a.get("description", ""), a.get("url", ""),
                                           (a.get("source") or {}).get("name", "GNews"), pub, hint, "gnews"))
        except Exception as e:
            print(f"[GNEWS] {category}/{country}: {e}")
    return items


def fetch_newsdata():
    if not NEWSDATA_API_KEY:
        print("[NEWSDATA] skipped — NEWSDATA_API_KEY not set")
        return []
    items = []
    for hint, category in NEWSDATA_CALLS:
        try:
            resp = requests.get("https://newsdata.io/api/1/latest", timeout=15, params={
                "apikey": NEWSDATA_API_KEY, "country": "in,us,ca", "language": "en",
                "category": category, "removeduplicate": 1,
            })
            if resp.status_code != 200:
                print(f"[NEWSDATA] {category}: HTTP {resp.status_code} {resp.text[:150]}")
                continue
            arts = resp.json().get("results", []) or []
            print(f"[OK] NewsData {category}: {len(arts)} articles")
            for a in arts:
                pub = parse_iso(a.get("pubDate"))  # UTC "YYYY-MM-DD HH:MM:SS"
                if pub:
                    source = a.get("source_name") or a.get("source_id") or "NewsData"
                    items.append(make_item(a.get("title", ""), a.get("description") or "", a.get("link", ""),
                                           source, pub, hint, "newsdata"))
        except Exception as e:
            print(f"[NEWSDATA] {category}: {e}")
    return items


# ---------------------------------------------------------------- dedupe + rank

def build_clusters(items):
    """Drop stale/empty items, then group same-story-different-outlet by title overlap."""
    cutoff = NOW - timedelta(hours=LOOKBACK_HOURS)
    seen_links, seen_titles = set(), set()
    fresh = []
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        if not it["title"] or not it["link"] or it["published"] < cutoff:
            continue
        nl, th = normalize_url(it["link"]), title_hash(it["title"])
        if nl in seen_links or th in seen_titles:
            continue
        seen_links.add(nl)
        seen_titles.add(th)
        it["domain"] = classify_domain(it["title"], it["summary"], it["hint"])
        it["region"] = detect_region(f"{it['title']} {it['summary']}")
        it["tokens"] = title_tokens(it["title"])
        fresh.append(it)

    clusters = []
    for it in fresh:
        for c in clusters:
            lead = c[0]["tokens"]
            if lead and it["tokens"]:
                overlap = len(lead & it["tokens"]) / min(len(lead), len(it["tokens"]))
                if overlap >= 0.6 and len(lead & it["tokens"]) >= 3:
                    c.append(it)
                    break
        else:
            clusters.append([it])

    # Prefer an item with a summary as the cluster lead
    for c in clusters:
        c.sort(key=lambda x: (not x["summary"], -x["published"].timestamp()))
    print(f"[DEDUPE] {len(items)} fetched → {len(fresh)} fresh unique → {len(clusters)} stories")
    return clusters


def story_from_cluster(c, domain=None, region=None, score=0.0, why=""):
    lead = c[0]
    sources = []
    for it in c:
        if it["source"] not in sources:
            sources.append(it["source"])
    summary = next((it["summary"] for it in c if it["summary"]), "")
    return {
        "title": lead["title"],
        "summary": truncate(summary, 220),
        "link": lead["link"],
        "source": lead["source"],
        "sources": sources,
        "published": max(it["published"] for it in c),
        "domain": domain or lead["domain"],
        "region": region or lead["region"],
        "score": round(score, 2),
        "why": why,
    }


def rank_heuristic(clusters):
    """Fallback ranking: coverage breadth + recency + geography."""
    stories = []
    for c in clusters:
        n_sources = len({it["source"] for it in c})
        age_h = (NOW - max(it["published"] for it in c)).total_seconds() / 3600
        recency = max(0.0, 3.0 * (1 - age_h / LOOKBACK_HOURS))
        region = c[0]["region"]
        score = 2.0 * min(n_sources, 5) + recency + REGION_WEIGHT.get(region, 0)
        stories.append(story_from_cluster(c, score=score))
    breaking = [s for s in stories
                if (NOW - s["published"]).total_seconds() <= BREAKING_HOURS * 3600 and len(s["sources"]) >= 3]
    return stories, sorted(breaking, key=lambda s: -s["score"])[:MAX_BREAKING]


RANK_PROMPT = """You are the editor of a news briefing for a reader in Toronto who follows India, the US and Canada most closely, then China.

Below are {n} candidate stories from the last 24 hours (id | age in hours | sources | current topic guess | headline | snippet). Some stories may still be duplicates of each other.

Tasks:
1. Merge any remaining duplicates (same event, different outlets) by listing all their ids together.
2. Assign each selected story exactly one domain from: {domains}.
3. Rate importance 1-10 for an informed professional: real-world consequences, market impact, policy change, scale. Celebrity, lifestyle, sports, opinion pieces, product deals and minor local crime are 1-3 and should normally be left out.
4. Tag the main region: India, US, Canada, China, Global or Other.
5. Mark breaking=true only for major developments that are under {breaking_h} hours old.
6. Give a one-line "why" (max 15 words) explaining why it matters.

Select up to 7 stories per domain (fewer if the domain has nothing important). Return JSON only:
{{"stories": [{{"ids": [12, 40], "domain": "...", "importance": 8, "region": "India", "breaking": false, "why": "..."}}]}}

Candidates:
{lines}
"""


def rank_with_llm(clusters):
    candidates = clusters[:300]
    lines = []
    for i, c in enumerate(candidates):
        lead = c[0]
        age = (NOW - max(it["published"] for it in c)).total_seconds() / 3600
        srcs = ", ".join(sorted({it["source"] for it in c})[:4])
        snippet = truncate(next((it["summary"] for it in c if it["summary"]), ""), 140)
        lines.append(f"{i} | {age:.1f}h | {srcs} | {lead['domain']} | {lead['title']} | {snippet}")

    prompt = RANK_PROMPT.format(n=len(candidates), domains="; ".join(DOMAIN_ORDER),
                                breaking_h=BREAKING_HOURS, lines="\n".join(lines))
    result = llm.generate_json(prompt, label="RANK")
    if not result or not isinstance(result.get("stories"), list):
        return None

    stories, breaking, used = [], [], set()
    for s in result["stories"]:
        try:
            ids = [int(i) for i in s.get("ids", []) if 0 <= int(i) < len(candidates) and int(i) not in used]
        except (TypeError, ValueError):
            continue
        domain = s.get("domain")
        if not ids or domain not in DOMAIN_ORDER:
            continue
        used.update(ids)
        merged = [it for i in ids for it in candidates[i]]
        merged.sort(key=lambda x: (not x["summary"], -x["published"].timestamp()))
        region = s.get("region") if s.get("region") in REGION_WEIGHT else merged[0]["region"]
        importance = float(s.get("importance") or 0)
        score = importance + REGION_WEIGHT.get(region, 0) + 0.3 * min(len({it["source"] for it in merged}), 5)
        story = story_from_cluster(merged, domain=domain, region=region, score=score,
                                   why=truncate(str(s.get("why") or ""), 120))
        age_h = (NOW - story["published"]).total_seconds() / 3600
        if s.get("breaking") and age_h <= BREAKING_HOURS and importance >= 7:
            breaking.append(story)
        stories.append(story)
    if len(stories) < 10:
        print(f"[RANK] LLM returned only {len(stories)} usable stories — using fallback")
        return None
    return stories, sorted(breaking, key=lambda s: -s["score"])[:MAX_BREAKING]


def select_digest(stories, breaking):
    """Top N per domain; breaking stories are shown once, in the breaking section."""
    breaking_links = {s["link"] for s in breaking}
    domain_items = {d: [] for d in DOMAIN_ORDER}
    for s in sorted(stories, key=lambda s: (-s["score"], -s["published"].timestamp())):
        if s["link"] in breaking_links:
            continue
        if len(domain_items[s["domain"]]) < MAX_PER_DOMAIN:
            domain_items[s["domain"]].append(s)
    return domain_items


# ---------------------------------------------------------------- intelligence brief

INTEL_PROMPT = """You are a cross-domain intelligence analyst writing the evening brief for a reader in Toronto who follows India, the US and Canada most closely, then China. Domains: AI, tech and semiconductors, finance and banking, stock markets, geopolitics, wars, commodities.

Today's headlines (id | topic | region | sources | headline):
{lines}

Market snapshot (price, daily change):
{market}

Write an analytical brief grounded ONLY in these headlines and figures. Be specific, name actors, countries, sectors and tickers; avoid generic statements. Return JSON only:
{{
  "causal_chains": [{{"title": "short name", "chain": ["event A", "leads to B", "which pressures C"], "domains": ["..."], "ids": [1, 2]}}],
  "second_order": [{{"event": "headline-level fact", "effect": "non-obvious knock-on effect", "who_is_affected": "..."}}],
  "scenarios": [{{"if": "trigger to watch in the next 1-2 weeks", "then": "likely consequence", "likelihood": "low|medium|high", "signpost": "what would confirm it"}}],
  "contrarian": [{{"signal": "evidence in today's news", "mainstream_view": "what most coverage assumes", "why_it_matters": "..."}}],
  "bottom_line": "two sentences on what matters most tonight"
}}
Give 3-4 causal chains, 3-5 second-order effects, 3-4 scenarios and 2-3 contrarian signals.
"""


def build_intelligence(stories, articles_today, snapshot):
    pool = sorted(stories, key=lambda s: -s["score"])[:80]
    seen = {normalize_url(s["link"]) for s in pool}
    extra = [a for a in articles_today if normalize_url(a["link"]) not in seen]
    rows = [(s["domain"], s["region"], ", ".join(s["sources"][:3]), s["title"]) for s in pool]
    rows += [(a.get("domain", ""), a.get("region", ""), a.get("source", ""), a["title"]) for a in extra[:220]]
    lines = "\n".join(f"{i} | {d} | {r} | {src} | {t}" for i, (d, r, src, t) in enumerate(rows))
    mkt = "\n".join(f"{m['label']}: {market.fmt_price(m)} {market.fmt_change(m)}" for m in snapshot) or "n/a"
    result = llm.generate_json(INTEL_PROMPT.format(lines=lines, market=mkt), label="INTEL", temperature=0.4)
    if not result or not any(result.get(k) for k in ("causal_chains", "scenarios", "second_order")):
        return None
    return result


# ---------------------------------------------------------------- article store + state

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def update_article_store(clusters, stories):
    """Keep 7 days of every fetched article (for /deepdive and the intelligence brief)."""
    store = load_json(ARTICLE_STORE, {"articles": []})
    by_link = {normalize_url(a["link"]): a for a in store.get("articles", [])}
    ranked = {normalize_url(s["link"]): s for s in stories}

    for c in clusters:
        for it in c:
            key = normalize_url(it["link"])
            s = ranked.get(key)
            by_link[key] = {
                "title": it["title"],
                "summary": it["summary"],
                "link": it["link"],
                "source": it["source"],
                "domain": s["domain"] if s else it["domain"],
                "region": s["region"] if s else it["region"],
                "published": it["published"].isoformat(),
                "importance": s["score"] if s else by_link.get(key, {}).get("importance"),
            }

    cutoff = NOW - timedelta(days=ARTICLE_STORE_DAYS)
    kept = [a for a in by_link.values() if (parse_iso(a["published"]) or NOW) >= cutoff]
    kept.sort(key=lambda a: a["published"], reverse=True)
    os.makedirs(SAVE_DIR, exist_ok=True)
    with open(ARTICLE_STORE, "w", encoding="utf-8") as f:
        json.dump({"updated": NOW.isoformat(), "days": ARTICLE_STORE_DAYS, "articles": kept},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"[STORE] {len(kept)} articles kept ({ARTICLE_STORE_DAYS} days)")
    return kept


def articles_since(articles, since):
    return [a for a in articles if (parse_iso(a["published"]) or NOW) >= since]


def due_slot():
    """Return the slot id (e.g. '2026-10-21T18') this run should deliver, or None."""
    state = load_json(STATE_FILE, {})
    past = [h for h in ALLOWED_HOURS if h <= NOW.hour]
    if FORCE_RUN:
        slot_hour = past[-1] if past else NOW.hour
        return f"{NOW.date().isoformat()}T{slot_hour:02d}", slot_hour
    if not past:
        return None, None
    slot_hour = past[-1]
    slot = f"{NOW.date().isoformat()}T{slot_hour:02d}"
    if state.get("last_slot") == slot:
        print(f"[SKIP] Slot {slot} already delivered.")
        return None, None
    if NOW.hour - slot_hour >= SLOT_GRACE_HOURS:
        print(f"[SKIP] Not a digest hour ({NOW.strftime('%I %p ET')}).")
        return None, None
    return slot, slot_hour


def save_state(slot):
    state = load_json(STATE_FILE, {})
    state.update({"last_slot": slot, "last_run": NOW.isoformat()})
    os.makedirs(SAVE_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def serialize_story(s):
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in s.items()}


def save_digest(domain_items, breaking, snapshot, intel, ranked_by):
    os.makedirs(SAVE_DIR, exist_ok=True)
    timestamp = NOW.strftime("%Y%m%d_%H%M")
    data = {
        "timestamp": timestamp,
        "datetime": NOW.isoformat(),
        "ranked_by": ranked_by,
        "breaking": [serialize_story(s) for s in breaking],
        "domains": {d: [serialize_story(s) for s in domain_items[d]] for d in DOMAIN_ORDER},
        "market": snapshot,
        "intelligence": intel,
    }
    filepath = os.path.join(SAVE_DIR, f"digest_{timestamp}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    files = sorted(glob.glob(os.path.join(SAVE_DIR, "digest_*.json")), reverse=True)
    for old_file in files[MAX_SAVED_DIGESTS:]:
        try:
            os.remove(old_file)
            print(f"[CLEANUP] removed {old_file}")
        except Exception as e:
            print(f"[CLEANUP ERROR] {old_file}: {e}")
    print(f"[SAVE] digest written to {filepath}")


# ---------------------------------------------------------------- Telegram

def t(text) -> str:
    """Escape for Telegram HTML parse mode."""
    return html.escape(str(text or ""), quote=False)


def sources_label(s) -> str:
    extra = len(s["sources"]) - 1
    return s["source"] + (f" +{extra}" if extra > 0 else "")


def telegram_story(s) -> str:
    line = f'• <a href="{html.escape(s["link"])}">{t(s["title"])}</a> — <i>{t(sources_label(s))}</i>'
    if s.get("why"):
        line += f"\n   ↳ {t(s['why'])}"
    elif s.get("summary"):
        line += f"\n   ↳ {t(truncate(s['summary'], 140))}"
    return line


def build_telegram(domain_items, breaking, snapshot, intel):
    lines = [f"<b>📰 News Digest</b> — {NOW.strftime('%I:%M %p ET · %a %b %d')}", ""]
    if breaking:
        lines.append("<b>🔴 BREAKING</b>")
        lines += [telegram_story(s) for s in breaking]
        lines.append("")
    for domain in DOMAIN_ORDER:
        items = domain_items[domain]
        if items:
            lines.append(f"<b>{EMOJI.get(domain, '•')} {t(domain.upper())}</b>")
            lines += [telegram_story(s) for s in items]
            lines.append("")
    lines.append("<b>📉 MARKET SNAPSHOT</b>")
    for m in snapshot:
        lines.append(f"• {t(m['label'])}: <b>{market.fmt_price(m)}</b> {market.fmt_change(m)}")
    lines.append("")
    if intel:
        lines += build_telegram_intel(intel)
    lines.append("<i>Auto-generated by News Digest Bot · manangoesagain.github.io/news-pipeline</i>")
    return "\n".join(lines)


def build_telegram_intel(intel):
    lines = ["<b>🔗 INTELLIGENCE BRIEF</b>"]
    if intel.get("bottom_line"):
        lines += [f"<i>{t(intel['bottom_line'])}</i>", ""]
    if intel.get("causal_chains"):
        lines.append("<b>Causal chains</b>")
        for c in intel["causal_chains"]:
            lines.append(f"• <b>{t(c.get('title'))}</b>: {t(' → '.join(c.get('chain', [])))}")
        lines.append("")
    if intel.get("second_order"):
        lines.append("<b>Second-order effects</b>")
        for e in intel["second_order"]:
            who = f" ({t(e['who_is_affected'])})" if e.get("who_is_affected") else ""
            lines.append(f"• {t(e.get('event'))} ⇒ {t(e.get('effect'))}{who}")
        lines.append("")
    if intel.get("scenarios"):
        lines.append("<b>If-then scenarios (1-2 weeks)</b>")
        for s in intel["scenarios"]:
            lines.append(f"• <b>If</b> {t(s.get('if'))} <b>then</b> {t(s.get('then'))} "
                         f"[{t(s.get('likelihood', '?'))}]")
            if s.get("signpost"):
                lines.append(f"   ↳ Watch: {t(s['signpost'])}")
        lines.append("")
    if intel.get("contrarian"):
        lines.append("<b>Contrarian signals</b>")
        for c in intel["contrarian"]:
            lines.append(f"• {t(c.get('signal'))} — vs. consensus: {t(c.get('mainstream_view'))}")
        lines.append("")
    lines += ["<i>AI-generated analysis from headlines; not investment advice.</i>", ""]
    return lines


def split_message(message: str, limit: int = 4000):
    """Split on line boundaries so HTML tags are never cut; over-long lines are trimmed."""
    chunks, current = [], ""
    for line in message.split("\n"):
        if len(line) > limit:
            line = truncate(re.sub(r"<[^>]+>", "", line), limit - 10)
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = split_message(message)
    ok = True
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code != 200:
            print(f"[TELEGRAM ERROR] part {i + 1}: {resp.status_code}: {resp.text[:300]} — resending as plain text")
            plain = html.unescape(re.sub(r"<[^>]+>", "", chunk))
            resp = requests.post(url, json={"chat_id": CHAT_ID, "text": plain,
                                            "disable_web_page_preview": True}, timeout=20)
        if resp.status_code == 200:
            print(f"[TELEGRAM] sent part {i + 1}/{len(chunks)}")
        else:
            ok = False
            print(f"[TELEGRAM ERROR] part {i + 1} failed: {resp.status_code}: {resp.text[:300]}")
    return ok


# ---------------------------------------------------------------- email

def send_email(subject: str, html_content: str) -> bool:
    if not RESEND_API_KEY:
        print("[EMAIL] skipped — RESEND_API_KEY not set")
        return True

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
        return True
    print(f"[EMAIL ERROR] {resp.status_code}: {resp.text}")
    return False


def build_email_html(domain_items: dict, breaking: list, snapshot: list, intel) -> str:
    date_str = NOW.strftime("%b %d, %Y")
    time_str = NOW.strftime("%I:%M %p ET")

    def esc(text):
        return html.escape(str(text or ""))

    def story_html(s, size=14):
        why = f'<div style="font-size: 12px; color: #1a73e8; margin-top: 2px;">↳ {esc(s["why"])}</div>' if s.get("why") else ""
        srcs = ", ".join(s["sources"][:4]) + (f" +{len(s['sources']) - 4}" if len(s["sources"]) > 4 else "")
        return f"""
            <div style="margin-bottom: 12px;">
              <a href="{esc(s['link'])}" style="color: #1a1a1a; text-decoration: none; font-weight: 600; font-size: {size}px; display: block; margin-bottom: 3px;">{esc(s['title'])}</a>
              <div style="font-size: 13px; color: #444; line-height: 1.4;">{esc(s['summary'])}</div>
              {why}
              <div style="font-size: 11px; color: #888; margin-top: 2px;">{esc(srcs)}</div>
            </div>"""

    rows = []
    if breaking:
        rows.append('<tr><td style="padding: 14px 0; border-bottom: 2px solid #e74c3c;">'
                    '<div style="font-size: 14px; font-weight: bold; color: #e74c3c; margin-bottom: 8px;">🔴 BREAKING</div>')
        rows += [story_html(s) for s in breaking]
        rows.append("</td></tr>")

    for domain in DOMAIN_ORDER:
        items = domain_items[domain]
        if not items:
            continue
        rows.append(f'<tr><td style="padding: 16px 0 6px 0; border-bottom: 1px solid #eee;">'
                    f'<div style="font-size: 16px; font-weight: bold; color: #222; margin-bottom: 10px;">'
                    f'{EMOJI.get(domain, "•")} {esc(domain.upper())}</div>')
        rows += [story_html(s) for s in items]
        rows.append("</td></tr>")

    market_rows = ""
    for m in snapshot:
        change = market.fmt_change(m)
        color = "#16a34a" if (m.get("change_pct") or 0) > 0 else ("#dc2626" if (m.get("change_pct") or 0) < 0 else "#888")
        market_rows += (f'<tr><td style="padding: 4px 0;">{esc(m["label"])}</td>'
                        f'<td style="padding: 4px 0; text-align: right; font-weight: 600;">{esc(market.fmt_price(m))}</td>'
                        f'<td style="padding: 4px 0 4px 12px; text-align: right; color: {color}; width: 90px;">{esc(change)}</td></tr>')
    rows.append(f"""
    <tr>
      <td style="padding: 16px 0 0 0; border-top: 2px solid #333;">
        <div style="font-size: 16px; font-weight: bold; color: #222; margin-bottom: 8px;">📉 MARKET SNAPSHOT</div>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; color: #444;">{market_rows}</table>
        <div style="font-size: 11px; color: #999; margin-top: 4px;">Change vs previous close (BTC: 24h). Data may be delayed.</div>
      </td>
    </tr>""")

    if intel:
        rows.append(email_intel_html(intel, esc))

    return f"""<!DOCTYPE html>
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
          <tr>
            <td style="padding: 20px 24px; background: #1a73e8; color: #ffffff;">
              <div style="font-size: 20px; font-weight: bold;">📰 News Brief</div>
              <div style="font-size: 13px; color: #cbd6f2; margin-top: 4px;">{date_str} &nbsp;•&nbsp; {time_str}</div>
            </td>
          </tr>
          <tr>
            <td style="padding: 0 24px 24px 24px;">
              <table cellpadding="0" cellspacing="0" width="100%">
                {''.join(rows)}
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding: 16px 24px; background: #f8f9fa; color: #888; font-size: 11px; text-align: center; border-top: 1px solid #eee;">
              — Auto-generated by News Digest Bot • Free sources only •
              <a href="https://manangoesagain.github.io/news-pipeline/" style="color: #888;">Web version</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def email_intel_html(intel, esc):
    h = ['<tr><td style="padding: 20px 0 0 0; border-top: 2px solid #7c3aed;">'
         '<div style="font-size: 16px; font-weight: bold; color: #7c3aed; margin-bottom: 8px;">🔗 INTELLIGENCE BRIEF</div>']
    sub = 'style="font-size: 13px; font-weight: bold; color: #222; margin: 12px 0 6px 0;"'
    item = 'style="font-size: 13px; color: #333; line-height: 1.45; margin-bottom: 8px;"'
    if intel.get("bottom_line"):
        h.append(f'<div style="font-size: 14px; color: #222; font-style: italic; background: #f5f3ff; padding: 10px 12px; border-radius: 6px;">{esc(intel["bottom_line"])}</div>')
    if intel.get("causal_chains"):
        h.append(f"<div {sub}>Causal chains</div>")
        for c in intel["causal_chains"]:
            h.append(f'<div {item}><b>{esc(c.get("title"))}</b><br>{esc(" → ".join(c.get("chain", [])))}</div>')
    if intel.get("second_order"):
        h.append(f"<div {sub}>Second-order effects</div>")
        for e in intel["second_order"]:
            who = f' <span style="color:#888;">({esc(e["who_is_affected"])})</span>' if e.get("who_is_affected") else ""
            h.append(f'<div {item}>{esc(e.get("event"))} ⇒ <b>{esc(e.get("effect"))}</b>{who}</div>')
    if intel.get("scenarios"):
        h.append(f"<div {sub}>If-then scenarios (next 1-2 weeks)</div>")
        for s in intel["scenarios"]:
            watch = f'<br><span style="color:#888;">Watch: {esc(s["signpost"])}</span>' if s.get("signpost") else ""
            h.append(f'<div {item}><b>If</b> {esc(s.get("if"))} <b>then</b> {esc(s.get("then"))} '
                     f'<span style="color:#7c3aed;">[{esc(s.get("likelihood", "?"))}]</span>{watch}</div>')
    if intel.get("contrarian"):
        h.append(f"<div {sub}>Contrarian signals</div>")
        for c in intel["contrarian"]:
            h.append(f'<div {item}>{esc(c.get("signal"))}<br><span style="color:#888;">Consensus: '
                     f'{esc(c.get("mainstream_view"))}</span><br>{esc(c.get("why_it_matters"))}</div>')
    h.append('<div style="font-size: 11px; color: #999; margin-top: 8px;">AI-generated analysis from headlines; not investment advice.</div>')
    h.append("</td></tr>")
    return "".join(h)


# ---------------------------------------------------------------- main

def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main():
    slot, slot_hour = due_slot()
    if not slot:
        set_output("ran", "false")
        return

    if not DRY_RUN and (not BOT_TOKEN or not CHAT_ID):
        print("FATAL: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as repository secrets.")
        sys.exit(1)

    print(f"[RUN] slot {slot}{' (forced)' if FORCE_RUN else ''}{' (dry run)' if DRY_RUN else ''}")

    items = fetch_rss() + fetch_google_news() + fetch_gnews() + fetch_newsdata()
    clusters = build_clusters(items)

    ranked = rank_with_llm(clusters) if llm.available() else None
    ranked_by = llm.last_model if ranked else "heuristic"
    stories, breaking = ranked or rank_heuristic(clusters)
    domain_items = select_digest(stories, breaking)
    print(f"[RANK] by {ranked_by}: {sum(len(v) for v in domain_items.values())} stories, {len(breaking)} breaking")

    snapshot = market.get_snapshot()
    articles = update_article_store(clusters, stories)

    intel = None
    if slot_hour == INTEL_HOUR or FORCE_INTEL:
        since = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        intel = build_intelligence(stories, articles_since(articles, since), snapshot)

    message = build_telegram(domain_items, breaking, snapshot, intel)
    email_html = build_email_html(domain_items, breaking, snapshot, intel)
    email_subject = f"📰 News Brief — {NOW.strftime('%b %d')} {NOW.strftime('%I:%M %p ET')}"
    if intel:
        email_subject += " + Intelligence Brief"

    save_digest(domain_items, breaking, snapshot, intel, ranked_by)

    if DRY_RUN:
        os.makedirs("preview", exist_ok=True)
        with open("preview/telegram.html", "w", encoding="utf-8") as f:
            f.write(message)
        with open("preview/email.html", "w", encoding="utf-8") as f:
            f.write(email_html)
        print(f"[DRY RUN] Telegram message ({len(message)} chars, {len(split_message(message))} parts) "
              f"and email written to preview/ — nothing sent")
        print(message[:3000])
    else:
        sent_tg = send_telegram(message)
        sent_mail = send_email(email_subject, email_html)
        if not (sent_tg or sent_mail):
            print("FATAL: both deliveries failed")
            sys.exit(1)
        save_state(slot)

    set_output("ran", "true")


if __name__ == "__main__":
    main()
