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

import archive
import graph_image
import llm
import market
import patterns
import jev_judge
import learning_ledger
import learning_priors
import learning_memory
import track_page

def lctx(kind):
    """What the digest has learned from its own graded predictions (empty until there is enough data)."""
    try:
        text = learning_memory.prompt_context(kind)
        return ("\n\n" + text) if text else ""
    except Exception as e:
        print(f"[LEARN] prompt context failed: {type(e).__name__}")
        return ""


def _recent_headlines(days=3, limit=600):
    """Titles of the last few days of collected articles (for grading predictions against later news)."""
    try:
        store = load_json(ARTICLE_STORE, {"articles": []})
        cutoff = NOW - timedelta(days=days)
        out = []
        for a in store.get("articles", []):
            when = parse_iso(a.get("published", ""))
            if when and when >= cutoff and a.get("title"):
                out.append(a["title"])
        return out[:limit]
    except Exception:
        return []


def jev_grade_soft_calls():
    """Optional (TypeSafe/Jev): grade open soft predictions against recent headlines. Soft grades never feed learning."""
    try:
        if not jev_judge.available():
            return
        if not learning_memory.enabled():
            return
        calls = learning_ledger.open_soft_calls(2, 30, NOW)
        heads = _recent_headlines()
        if not calls or not heads:
            return
        getattr(jev_judge, "begin_phase", lambda *a, **k: None)("soft", 30, 15)
        t0 = time.time()
        grades, asked = {}, 0
        for k in range(0, len(calls), 5):
            chunk = calls[k:k + 5]
            picked = []
            for c in chunk:
                want = archive.tokens(f"{c.get('if', '')} {c.get('then', '')}")
                ranked = sorted(heads, key=lambda h: -len(want & archive.tokens(h)))[:3]
                picked += [h for h in ranked if h not in picked]
            probs = jev_judge.grade_soft([{"id": c["id"], "if": c.get("if", ""), "then": c.get("then", "")} for c in chunk],
                                         picked[:12])
            asked += len(chunk)
            for cid, pr in (probs or {}).items():
                grades[cid] = jev_judge.classify(pr)
        done = learning_ledger.apply_external_soft_grades(grades, NOW, "jev") if grades else []
        print(f"[JEV] soft predictions asked {asked}, graded {len(done)}, {time.time() - t0:.0f}s")
    except Exception as e:
        print(f"[JEV] soft grading failed: {type(e).__name__}")


def learning_cycle(llm_step=False):
    """Before the digest (llm_step=False): fetch history priors if there are none yet, grade due calls and refresh the
    scoreboard. After delivery (llm_step=True): refresh priors, let Jev grade soft predictions, and let the learning
    step update its own notes. Never raises, never starves a digest."""
    try:
        if not llm_step:
            try:
                jev_judge.ping()
            except Exception:
                pass
        if not llm_step and not learning_priors.load_priors():
            learning_priors.refresh_priors(NOW, budget_s=45)
        learning_ledger.grade_calls(NOW)
        learning_ledger.scorecard(NOW)
        if llm_step:
            if llm.HARD_STOP - time.time() < 400:
                print("[LEARN] skipped the learning step: not enough time left")
            else:
                learning_priors.refresh_priors(NOW, budget_s=45)
                jev_grade_soft_calls()
                if llm.available():
                    getattr(jev_judge, "begin_phase", lambda *a, **k: None)("lessons", 20, 25)
                    res = learning_memory.run_daily(NOW, llm)
                    print(f"[LEARN] learning step: {res}")
                learning_ledger.grade_calls(NOW)
                learning_ledger.scorecard(NOW)
    except Exception as e:
        print(f"[LEARN] cycle failed: {type(e).__name__}: {e}")


# --- Secrets from GitHub Actions env ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or "PYRAMIDMANAN@gmail.com"
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
    if link and urlparse(link).scheme not in ("http", "https"):
        link = ""
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
6. Give a short "why" (max 10 words) explaining why it matters.

Select up to 6 stories per domain (fewer if the domain has nothing important). Be brief; return JSON only:
{{"stories": [{{"ids": [12, 40], "domain": "...", "importance": 8, "region": "India", "breaking": false, "why": "..."}}]}}

Candidates:
{lines}
"""


def rank_with_llm(clusters):
    # Send the 160 most promising stories (by coverage breadth and recency) to keep the call fast
    candidates = sorted(clusters, key=lambda c: (-len({it["source"] for it in c}),
                                                 -max(it["published"] for it in c).timestamp()))[:160]
    lines = []
    for i, c in enumerate(candidates):
        lead = c[0]
        age = (NOW - max(it["published"] for it in c)).total_seconds() / 3600
        srcs = ", ".join(sorted({it["source"] for it in c})[:4])
        snippet = truncate(next((it["summary"] for it in c if it["summary"]), ""), 140)
        lines.append(f"{i} | {age:.1f}h | {srcs} | {lead['domain']} | {lead['title']} | {snippet}")

    prompt = RANK_PROMPT.format(n=len(candidates), domains="; ".join(DOMAIN_ORDER),
                                breaking_h=BREAKING_HOURS, lines="\n".join(lines)) + lctx("rank")
    result = llm.generate_json(prompt, label="RANK", require="stories", max_tokens=6000, budget_s=240)
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
        # Only merge clusters whose headlines actually overlap with the first one
        lead_tokens = candidates[ids[0]][0]["tokens"]
        kept = [ids[0]] + [i for i in ids[1:] if len(lead_tokens & candidates[i][0]["tokens"]) >= 2]
        if len(kept) < len(ids):
            s["why"] = ""  # the model's note may describe a story we didn't merge
        ids = kept
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

    # Backfill thin domains from the heuristic ranking; scaled down so LLM picks stay on top
    leftovers, _ = rank_heuristic([c for i, c in enumerate(candidates) if i not in used])
    for h in leftovers:
        h["score"] = round(h["score"] / 10, 2)
    stories += leftovers
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

Today is {today}. Write an analytical brief grounded ONLY in these headlines and figures. Be specific, name actors, countries, sectors and tickers; avoid generic statements. Do not cite id numbers in the text. Return JSON only:
{{
  "causal_chains": [{{"title": "short name", "chain": ["event A", "leads to B", "which pressures C"], "domains": ["..."], "ids": [1, 2]}}],
  "second_order": [{{"event": "headline-level fact", "effect": "non-obvious knock-on effect", "who_is_affected": "..."}}],
  "scenarios": [{{"if": "trigger to watch in the next 1-2 weeks", "then": "likely consequence", "likelihood": "low|medium|high", "signpost": "what would confirm it"}}],
  "contrarian": [{{"signal": "evidence in today's news", "mainstream_view": "what most coverage assumes", "why_it_matters": "..."}}],
  "bottom_line": "two sentences on what matters most tonight"
}}
Give 3-4 causal chains, 3-5 second-order effects, 3-4 scenarios and 2-3 contrarian signals.
"""


GRAPH_PROMPT = """HARD LIMITS (a longer reply fails): 28-34 nodes, 36-50 edges, JSON only, no commentary. Node label max 28 chars, node detail max 100 chars, edge label max 4 words, each node's "ids" list max 2 numbers. Every node must have at least one link.

You are an intelligence analyst. Today's headlines (id | topic | region | sources | headline):
{lines}

Market snapshot (price, daily change):
{market}

Today is {today}. The evening brief already found these main themes: {themes}

Return JSON only, in exactly this shape:
{{
  "graph": {{
    "nodes": [{{"id": "n1", "label": "short name", "type": "event|country|company|sector|commodity|index|institution", "domain": "one of: {domains}, or empty", "region": "India|US|Canada|China|Global", "detail": "key number or name, max 100 chars", "direction": "up|down|flat|none", "market": "exact label from the market snapshot if this node is that ticker/commodity/index, else empty", "ids": [3, 17]}}],
    "edges": [{{"from": "n1", "to": "n2", "label": "mechanism, max 4 words", "effect": "up|down|mixed", "strength": 1, "confidence": "low|medium|high", "status": "reported|predicted", "order": 1, "kind": "causal|feedback|contradicts"}}]
  }}
}}
Build a connection map of tonight's news. Node types: event (something that happened), country, company, sector, commodity, index (stock index, currency, crypto), institution (central bank, regulator, policy, alliance). Cover EVERY domain. Every edge is a cause->effect arrow (from causes to) whose label states the MECHANISM (e.g. raises shipping costs), with effect = whether the target goes up or down (or mixed). status "reported" = the headlines directly report this link; "predicted" = your inference. order 1 = direct, 2 = second-order, 3 = third-order: build multi-step chains (A->B->C) and mark them with order. kind "feedback" = an edge looping back to an earlier cause; "contradicts" = one development cuts against another. Make hub nodes (one cause with 3+ effects) where the news supports it; include India, US, Canada and China angles. strength 3 = strong/direct, 2 = moderate, 1 = weak spillover. "ids" are the numbers at the start of the supporting headline lines (max 2). Every edge endpoint must be a node id (n1, n2, ...). Only draw a link when the headlines support a real mechanism, never link A->B and B->A, and put each node in the topic it truly belongs to.
"""

GRAPH_LITE_PROMPT = """HARD LIMITS (a longer reply fails): 16-20 nodes, 20-28 edges, JSON only, no commentary. Node label max 24 chars, NO detail field, edge label max 3 words, no extra fields.

You are an intelligence analyst. Today's headlines (id | topic | region | sources | headline):
{lines}

Market snapshot (price, daily change):
{market}

Today is {today}. Main themes: {themes}

Return JSON only, in exactly this shape:
{{"graph": {{"nodes": [{{"id": "n1", "label": "short name", "type": "event|country|company|sector|commodity|index|institution", "domain": "one of: {domains}, or empty", "market": "exact market snapshot label if this node is that instrument, else empty", "ids": [3]}}],
"edges": [{{"from": "n1", "to": "n2", "label": "mechanism", "effect": "up|down|mixed", "strength": 2, "status": "reported|predicted"}}]}}}}
Map the biggest cause->effect links in tonight's news (from causes to), covering the main domains and India, US, Canada and China where the news supports it. Every node needs at least one link; every edge endpoint must be a node id; never link A->B and B->A.
"""


def _graph_ok(res):
    """Caller-side check: a model reply only counts when it really contains a usable map."""
    g = sanitize_graph((res or {}).get("graph"))
    return bool(g and len(g["edges"]) >= 8)


def _graph_ok_lite(res):
    g = sanitize_graph((res or {}).get("graph"))
    return bool(g and len(g["edges"]) >= 5)


def generate_graph(lines, mkt, themes, today, rows_n):
    """Ask the AI for the connection map. Full request first; if it fails, one cheaper 'lite' request.
    Returns a sanitized graph or None."""
    fmt = dict(market=mkt, today=today, themes=themes or "n/a", domains="; ".join(DOMAIN_ORDER))
    try:
        gres = llm.generate_json(GRAPH_PROMPT.format(lines="\n".join(lines.split("\n")[:120]), **fmt) + lctx("graph"), label="GRAPH",
                                 require="graph", temperature=0.3, max_tokens=9000, budget_s=420, per_model_s=150,
                                 prefer="nvidia", validate=_graph_ok)
        g = sanitize_graph((gres or {}).get("graph"))
        if g and len(g["edges"]) >= 8:  # a salvaged, node-only graph is not good enough
            return g
        print("[GRAPH] full request gave no valid graph")
    except Exception as e:
        print(f"[GRAPH] full request error: {e}")
    try:
        short = "\n".join(lines.split("\n")[:70])
        gres = llm.generate_json(GRAPH_LITE_PROMPT.format(lines=short, **fmt) + lctx("graph"), label="GRAPH-LITE", require="graph",
                                 temperature=0.3, max_tokens=5000, budget_s=240, per_model_s=100,
                                 prefer="nvidia", validate=_graph_ok_lite)
        g = sanitize_graph((gres or {}).get("graph"))
        if g and len(g["edges"]) >= 5:
            print("[GRAPH] lite fallback used")
            return g
        print("[GRAPH] lite request gave no valid graph")
    except Exception as e:
        print(f"[GRAPH] lite request error: {e}")
    return None


def jev_filter_links(g):
    """Optional second opinion (TypeSafe/Jev): drop map links the headlines do not support. Never raises."""
    try:
        if not jev_judge.available() or not g or not g.get("edges"):
            return g
        if llm.HARD_STOP - time.time() < 600:
            print("[JEV] link check skipped: not enough time left")
            return g
        getattr(jev_judge, "begin_phase", lambda *a, **k: None)("links", 60, 40)
        t0 = time.time()
        probs = jev_judge.verify_links(g)
        if not probs:
            print("[JEV] links: no answers")
            return g
        g2, dropped = jev_judge.drop_unsupported(g, probs)
        print(f"[JEV] links checked: {len(probs)} of {len(g['edges'])}, dropped {len(dropped)}, {time.time() - t0:.0f}s")
        return g2 if g2 and len(g2.get("edges", [])) >= 8 else g
    except Exception as e:
        print(f"[JEV] link check failed: {type(e).__name__}")
        return g


def build_intelligence(stories, articles_today, snapshot):
    pool = sorted(stories, key=lambda s: -s["score"])[:80]
    seen = {normalize_url(s["link"]) for s in pool}
    extra = [a for a in articles_today if normalize_url(a["link"]) not in seen]
    extra = extra[:220]
    rows =[(s["domain"], s["region"], ", ".join(s["sources"][:3]), s["title"]) for s in pool]
    rows += [(a.get("domain", ""), a.get("region", ""), a.get("source", ""), a["title"]) for a in extra]
    rows_meta = [(s["title"], s["link"]) for s in pool] + [(a["title"], a["link"]) for a in extra]
    lines = "\n".join(f"{i} | {d} | {r} | {src} | {t}" for i, (d, r, src, t) in enumerate(rows))
    mkt = "\n".join(f"{m['label']}: {market.fmt_price(m)} {market.fmt_change(m)}" for m in snapshot) or "n/a"
    result = llm.generate_json(INTEL_PROMPT.format(lines=lines, market=mkt, today=NOW.strftime("%A, %B %d, %Y"),
                                                       domains="; ".join(DOMAIN_ORDER)) + lctx("brief"), label="INTEL", require="causal_chains",
                              temperature=0.4, budget_s=300)
    if not result or not any(result.get(k) for k in ("causal_chains", "scenarios", "second_order")):
        return None
    g = None
    try:
        themes = "; ".join(c.get("title", "") for c in (result.get("causal_chains") or []) if isinstance(c, dict))[:600]
        g = generate_graph(lines, mkt, themes, NOW.strftime("%A, %B %d, %Y"), len(rows))
    except Exception as e:
        print(f"[INTEL] graph validation error: {e}")
        g = None
    if g:
        try:
            g = enrich_graph(g, snapshot, load_json(archive.THREADS_FILE, {"threads": []}), rows_meta)
        except Exception as e:
            print(f"[INTEL] graph enrichment error: {e}")
        g = jev_filter_links(g)  # optional: needs TYPESAFE_API_KEY; skipped silently otherwise
        try:
            attach_market(g, snapshot)
            add_focus_cards(g, snapshot)
            finish_graph(g, snapshot)
        except Exception as e:
            print(f"[INTEL] focus cards error: {e}")
        result["graph"] = g
    else:
        result.pop("graph", None)
        print("[INTEL] no valid graph in reply")
    return result



CARDS_PROMPT = """You are an intelligence analyst writing "focus cards" for the busiest nodes of tonight's news map. Plain English, specific, grounded ONLY in the nodes, links and headlines below plus well-known background; never invent figures that are not given. Readers are in Toronto and care about India, the US and Canada, and about the Indian rupee (INR), Canadian dollar (CAD) and US dollar (USD).

Today is {today}. Market snapshot (price, daily change):
{market}

STRICT RULES: (1) Use only numbers, names and dates that appear in the nodes, links or market snapshot below. Any future price level, date, probability or figure you add yourself must be written as a scenario in words ("if oil holds above its current level") - never invent specific levels, dates, dollar amounts or statistics. (2) Never write node ids such as n12 or "IDs 5, 18" anywhere in the text; use names. (3) For "currency", dir means the PRESSURE THIS STORY PUTS on that currency (stronger/weaker/flat); it is not today's move, which is shown separately. (4) Keep predictions consistent with the key points and with each other; do not contradict yourself. (5) "pattern" must be a hedged, general recurring pattern, not specific historical numbers from memory.

Nodes (id | type | domain | label | detail):
{nodes}

Links (from -> to | mechanism | effect | status):
{links}

Return JSON only:
{{
  "cards": {{
    "<node id>": {{
      "so_what": "the whole story in at most 15 plain words: what it means and who gains or loses",
      "headline": "one sentence takeaway, max 140 chars",
      "points": ["exactly 4 short bullets in this order: (1) what is happening, with numbers and names; (2) why it happens - the first-principles mechanism; (3) what is likely next, as an if-then; (4) what to watch for and what would prove this wrong. Max 120 chars each"],
      "for_you": {{"India": {{"dir": "up|down|neutral", "why": "at most 4 words"}}, "US": {{"dir": "up|down|neutral", "why": "at most 4 words"}}, "Canada": {{"dir": "up|down|neutral", "why": "at most 4 words"}}}},
      "currency": {{"INR": {{"dir": "stronger|weaker|flat", "why": "max 100 chars"}}, "CAD": {{"dir": "stronger|weaker|flat", "why": "..."}}, "USD": {{"dir": "stronger|weaker|flat", "why": "..."}}}},
      "predictions": [{{"if": "max 90 chars", "then": "max 110 chars", "odds": "low|medium|high", "horizon": "days|weeks", "reasoning": "first principles: because X must lead to Y unless Z, max 170 chars", "watch": "signpost, max 100 chars", "wrong_if": "what would prove it wrong, max 100 chars"}}],
      "against": ["signal that cuts against the story, max 110 chars"],
      "pattern": "a historical precedent or recurring pattern, hedged, max 150 chars"
    }}
  }},
  "links": {{
    "<from id>><to id>": {{"steps": ["2-4 short mechanism steps"], "evidence": "the real-world fact that supports this link, max 140 chars; never describe the data fields (no words like detail, label, node)", "weaken": "what could weaken this link, max 120 chars", "next": "where it leads next, max 100 chars", "seen_before": "max 120 chars, hedged"}}
  }}
}}
Write a card for EVERY node id in this list: {hub_ids}. Give 2-3 predictions per card. For currency, think through each of INR, CAD and USD separately (for example an oil spike tends to weaken INR, strengthen CAD, and strengthen USD as a haven). Write "links" entries only for these link keys: {link_keys}.
"""

FOCUS_HUBS = 15
FOCUS_BATCH = 5
FOCUS_LINKS = 30


def hub_ids(g, n=FOCUS_HUBS):
    deg = {}
    for e in g["edges"]:
        for k in ("from", "to"):
            deg[e[k]] = deg.get(e[k], 0) + 1
    ranked = sorted(g["nodes"], key=lambda x: (-deg.get(x["id"], 0), x["label"]))
    return [x["id"] for x in ranked[:n]], deg


def sanitize_cards(res, g, hubs):
    """Validate the AI's focus cards and attach them to nodes/edges. Returns how many cards were kept."""
    if not isinstance(res, dict):
        return 0
    cards = res.get("cards") if isinstance(res.get("cards"), dict) else {}
    links = res.get("links") if isinstance(res.get("links"), dict) else {}
    by_id = {x["id"]: x for x in g["nodes"]}
    kept = 0

    def txt(v, n):
        return _clean_text(v, n)

    def lst(v, n, size):
        return [txt(x, size) for x in v[:n] if txt(x, size)] if isinstance(v, list) else []

    for nid, c in cards.items():
        node = by_id.get(nid)
        if node is None or not isinstance(c, dict):
            continue
        points = lst(c.get("points"), 4, 120)
        if not points:
            continue
        card = {"headline": txt(c.get("headline"), 140), "points": points}
        so_what = txt(c.get("so_what"), 110)
        if so_what:
            card["so_what"] = so_what
        fy = c.get("for_you") if isinstance(c.get("for_you"), dict) else {}
        card["for_you"] = {}
        for k in ("India", "US", "Canada"):
            v = fy.get(k)
            if isinstance(v, dict):
                d = str(v.get("dir") or "").strip().lower()
                d = d if d in ("up", "down", "neutral") else "neutral"
                why = txt(v.get("why"), 40)
                if why or d != "neutral":
                    card["for_you"][k] = {"dir": d, "why": why}
            elif txt(v, 100):
                card["for_you"][k] = {"dir": "neutral", "why": txt(v, 40)}
        cur = c.get("currency") if isinstance(c.get("currency"), dict) else {}
        card["currency"] = {}
        for k in ("INR", "CAD", "USD"):
            v = cur.get(k) if isinstance(cur.get(k), dict) else {}
            d = str(v.get("dir") or "").strip().lower()
            if d in ("stronger", "weaker", "flat"):
                card["currency"][k] = {"dir": d, "why": txt(v.get("why"), 100)}
        preds = []
        for pr in (c.get("predictions") if isinstance(c.get("predictions"), list) else [])[:3]:
            if not isinstance(pr, dict) or not txt(pr.get("then"), 110):
                continue
            odds = str(pr.get("odds") or "").strip().lower()
            hz = str(pr.get("horizon") or "").strip().lower()
            preds.append({"if": txt(pr.get("if"), 90), "then": txt(pr.get("then"), 110),
                          "odds": odds if odds in ("low", "medium", "high") else "medium",
                          "horizon": "days" if "day" in hz else "weeks",
                          "reasoning": txt(pr.get("reasoning"), 170), "watch": txt(pr.get("watch"), 100),
                          "wrong_if": txt(pr.get("wrong_if"), 100)})
        card["predictions"] = preds
        card["against"] = lst(c.get("against"), 2, 110)
        card["pattern"] = txt(c.get("pattern"), 150)
        node["card"] = card
        kept += 1
    for e in g["edges"]:
        c = links.get(f"{e['from']}>{e['to']}")
        if isinstance(c, dict):
            steps = lst(c.get("steps"), 4, 100)
            if steps:
                ev = txt(c.get("evidence"), 140)
                if re.search(r"\b(label(?:l)?ed|detail|node)\b", ev, re.I):
                    ev = ""  # the model described the data fields instead of a fact
                e["card"] = {"steps": steps, "evidence": ev, "weaken": txt(c.get("weaken"), 120),
                             "next": txt(c.get("next"), 100), "seen_before": txt(c.get("seen_before"), 120)}
    return kept


def light_cards(g):
    """No-AI cards for the busiest nodes, derived from node details and link labels."""
    hubs, deg = hub_ids(g)
    by_id = {x["id"]: x for x in g["nodes"]}
    for nid in hubs:
        node = by_id[nid]
        if node.get("card"):
            continue
        ins = [e for e in g["edges"] if e["to"] == nid][:2]
        outs = [e for e in g["edges"] if e["from"] == nid][:3]
        points = []
        if node.get("detail"):
            points.append(_clean(node["detail"], 120))
        if ins:
            points.append(_clean("Driven by: " + "; ".join(f"{by_id[e['from']]['label']} ({e['label']})" for e in ins), 120))
        if outs:
            points.append(_clean("Leads to: " + "; ".join(f"{by_id[e['to']]['label']} ({e['label']})" for e in outs), 120))
        ups = [by_id[e["to"]]["label"] for e in outs if e.get("effect") == "up"]
        downs = [by_id[e["to"]]["label"] for e in outs if e.get("effect") == "down"]
        so_what = ""
        if ups or downs:
            bits = []
            if ups:
                bits.append("lifts " + " and ".join(ups[:2]))
            if downs:
                bits.append("hurts " + " and ".join(downs[:2]))
            so_what = _clean(f"{node['label']} {' but '.join(bits)}", 110)
        if points:
            node["card"] = {"so_what": so_what, "headline":_clean(node.get("why_it_matters") or node.get("detail") or node["label"], 140),
                            "points": points, "for_you": {}, "currency": {}, "predictions": [], "against": [],
                            "pattern": "", "light": True}


def attach_market(g, snapshot):
    """Currency tiles need today's numbers: ship the snapshot with the graph."""
    g["market"] = [{"label": m["label"], "price": market.fmt_price(m), "change": market.fmt_change(m),
                    "change_pct": m.get("change_pct")} for m in (snapshot or []) if m.get("price") is not None]


def add_focus_cards(g, snapshot, themes=""):
    """Separate AI calls (own time budget): full cards for the busiest nodes, in small parallel batches
    so one slow reply cannot lose them all. Never raises."""
    try:
        from concurrent.futures import ThreadPoolExecutor
        hubs, deg = hub_ids(g)
        nodes_txt = "\n".join(f"{n['id']} | {n['type']} | {n.get('domain', '')} | {n['label']} | {n.get('detail', '')}"
                              for n in g["nodes"])
        links_txt = "\n".join(f"{e['from']} -> {e['to']} | {e.get('label', '')} | {e.get('effect', '')} | {e.get('status', '')}"
                              for e in g["edges"])
        mkt = "\n".join(f"{m['label']}: {m['price']} {m['change']}" for m in g.get("market", [])) or "n/a"
        today = NOW.strftime("%A, %B %d, %Y")
        batches = [hubs[i:i + FOCUS_BATCH] for i in range(0, len(hubs), FOCUS_BATCH)]
        by_id_arm = {n["id"]: n for n in g["nodes"]}

        def run(batch):
            bset = set(batch)
            touching = [e for e in g["edges"] if e["from"] in bset or e["to"] in bset]
            touching.sort(key=lambda e: (-e.get("strength", 1), e["from"]))
            keys = [f"{e['from']}>{e['to']}" for e in touching[:FOCUS_LINKS // 2]]
            try:
                arm = "control"
                try:
                    arm = learning_memory.assign_arm(f"{NOW:%Y%m%d%H}|" + "|".join(batch), "cards")
                    hint = learning_memory.variant_text("cards", arm)
                except Exception:
                    hint = ""
                for nid in batch:
                    if nid in by_id_arm:
                        by_id_arm[nid]["arm"] = arm
                extra = f"\n\nStyle hint: {hint}" if hint else ""
                return llm.generate_json(CARDS_PROMPT.format(today=today, market=mkt, nodes=nodes_txt, links=links_txt,
                                                             hub_ids=", ".join(batch), link_keys=", ".join(keys))
                                         + lctx("cards") + extra,
                                         label="CARDS", require="cards", temperature=0.3, budget_s=300, per_model_s=150,
                                         prefer="nvidia")
            except Exception as e:
                print(f"[CARDS] batch error {type(e).__name__}: {e}")
                return None

        with ThreadPoolExecutor(max_workers=len(batches) or 1) as ex:
            results = list(ex.map(run, batches))
        kept = 0
        for res, batch in zip(results, batches):
            n = sanitize_cards(res, g, batch)
            for attempt in range(2):  # a whole batch failed (the free tier is flaky): try it again, one at a time
                if n or llm.HARD_STOP - time.time() < 240:
                    break
                print(f"[CARDS] batch failed; retrying it alone (try {attempt + 1})")
                n = sanitize_cards(run(batch), g, batch)
            kept += n
        print(f"[CARDS] {kept} of {len(hubs)} focus cards kept ({len(batches)} batches)")
    except Exception as e:
        print(f"[CARDS ERROR] {type(e).__name__}: {e}")
    try:
        light_cards(g)
    except Exception as e:
        print(f"[CARDS LIGHT ERROR] {e}")
    return g


def compute_impact(g):
    """No-AI relevance for each node: size (small/medium/big), how sure, how soon, and the top 5 for the reader."""
    try:
        nodes = g["nodes"]
        edges = g["edges"]
        deg, strong, conf, lags = {}, {}, {}, {}
        weight = {"low": 1, "medium": 2, "high": 3}
        for e in edges:
            for k in (e["from"], e["to"]):
                deg[k] = deg.get(k, 0) + 1
                if e.get("strength", 1) >= 3:
                    strong[k] = strong.get(k, 0) + 1
                conf.setdefault(k, []).append(weight.get(e.get("confidence", "medium"), 2))
                if e.get("lag"):
                    lags.setdefault(k, []).append(e["lag"])
        scores = {}
        for n in nodes:
            nid = n["id"]
            mk = n.get("market") if isinstance(n.get("market"), dict) else {}
            cur = (n.get("card") or {}).get("currency") or {}
            score = deg.get(nid, 0) + 2 * strong.get(nid, 0)
            if mk:
                score += 3
                if abs(mk.get("change_pct") or 0) >= 1:
                    score += 2
            if n.get("region") in ("India", "US", "Canada"):
                score += 2
            score += 2 * len([k for k in ("INR", "CAD", "USD") if k in cur])
            if n.get("card") and not n["card"].get("light"):
                score += 1
            scores[nid] = score
        ordered = sorted(nodes, key=lambda n: (-scores[n["id"]], n["label"]))
        total = max(1, len(ordered))
        rank = {n["id"]: i for i, n in enumerate(ordered)}
        for n in nodes:
            nid = n["id"]
            pos = rank[nid] / total
            size = "big" if pos < 0.15 else "medium" if pos < 0.5 else "small"
            if deg.get(nid, 0) <= 1 and not n.get("market"):
                size = "small"
            c = conf.get(nid, [2])
            avg = sum(c) / len(c)
            sure = "high" if avg >= 2.5 else "medium" if avg >= 1.7 else "low"
            ls = lags.get(nid, [])
            when = "days"
            if ls:
                when = max(set(ls), key=ls.count)
                when = {"hours": "days", "days": "days", "weeks": "weeks"}.get(when, "days")
            n["impact"] = {"score": scores[nid], "size": size, "sure": sure, "when": when, "top5": 0}
        for i, n in enumerate([n for n in ordered if n["impact"]["size"] != "small"][:5], 1):
            n["impact"]["top5"] = i
    except Exception as e:
        print(f"[IMPACT ERROR] {type(e).__name__}: {e}")
    return g


def finish_graph(g, snapshot):
    """Shared by the AI and backup graphs: market numbers, history stats, light cards."""
    attach_market(g, snapshot)
    try:
        if hasattr(patterns, "enrich_nodes_with_stats"):
            patterns.enrich_nodes_with_stats(g, NOW)
    except Exception as e:
        print(f"[HISTORY ERROR] {e}")
    compute_impact(g)
    return g


GRAPH_NODE_TYPES = {"event", "country", "company", "sector", "commodity", "index", "institution", "market"}
MAX_NODES, MAX_EDGES = 50, 80


def _clean(text, n):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return truncate(text, n) if len(text) > n else text


_LEAK_RE = re.compile(r"\s*\((?:ids?|nodes?|links?)\b[^)]*\)|\b[Nn]odes?\s+n\d+\b|\(?\bn\d{1,3}\b\)?", re.I)


def _word_cut(text, n):
    """Shorten to at most n characters on a word boundary, without an ellipsis."""
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut or text[:n]


def _clean_text(text, n):
    """_clean for human-readable fields: also drops leaked node ids like 'n12' or '(IDs 5, 18)'."""
    text = _LEAK_RE.sub("", str(text or ""))
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return _clean(text, n)


def sanitize_graph(g):
    """Validate LLM graph data. Returns a clean {"nodes","edges"} dict or None."""
    if not isinstance(g, dict):
        return None
    nodes, ids = [], set()
    raw_nodes = g.get("nodes") if isinstance(g.get("nodes"), list) else []
    raw_edges = g.get("edges") if isinstance(g.get("edges"), list) else []
    for n in raw_nodes:  # a truncated last node (missing fields) is simply skipped
        if not isinstance(n, dict):
            continue
        nid = _clean(n.get("id"), 12)
        label = _clean_text(n.get("label"), 28)
        if not nid or not label or nid in ids:
            continue
        ntype = str(n.get("type") or "").strip().lower()
        if ntype == "market":
            ntype = "index"
        if ntype not in GRAPH_NODE_TYPES:
            ntype = "event"
        domain = str(n.get("domain") or "").strip()
        node = {"id": nid, "label": label, "type": ntype, "domain": domain if domain in DOMAIN_ORDER else ""}
        for key, size in (("detail", 260), ("why_it_matters", 200), ("market", 20)):
            val = _clean_text(n.get(key), size)
            if val:
                node[key] = val
        region = str(n.get("region") or "").strip()
        if region in REGION_WEIGHT or region == "Global":
            node["region"] = region
        direction = str(n.get("direction") or "").strip().lower()
        if direction in ("up", "down", "flat"):
            node["direction"] = direction
        raw_ids = n.get("ids") if isinstance(n.get("ids"), list) else []
        node["ids"] = [int(i) for i in raw_ids if str(i).strip().isdigit()][:4]
        ids.add(nid)
        nodes.append(node)
    nodes = nodes[:MAX_NODES]
    # merge duplicates the model sometimes writes twice (same market ticker, or near-identical names)
    idmap, kept = {}, []
    for n in nodes:
        twin = None
        toks = archive.tokens(n["label"])
        for k in kept:
            ktoks = archive.tokens(k["label"])
            same_mkt = n.get("market") and n.get("market") == k.get("market")
            overlap = len(toks & ktoks) / max(1, len(toks | ktoks))
            contained = len(toks) >= 1 and len(ktoks) >= 1 and (toks <= ktoks or ktoks <= toks) \
                and n["type"] in ("index", "commodity") and k["type"] in ("index", "commodity")
            if same_mkt or contained or (overlap >= 0.75 and n["type"] == k["type"]) or n["label"].lower() == k["label"].lower():
                twin = k
                break
        if twin is None:
            kept.append(n)
        else:
            idmap[n["id"]] = twin["id"]
            for key in ("detail", "why_it_matters", "region", "direction", "market"):
                if n.get(key) and not twin.get(key):
                    twin[key] = n[key]
            twin["ids"] = (twin.get("ids", []) + n.get("ids", []))[:4]
    nodes = kept
    ids = {n["id"] for n in nodes}
    edges, seen = [], set()
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        a, b = _clean(e.get("from"), 12), _clean(e.get("to"), 12)
        a, b = idmap.get(a, a), idmap.get(b, b)
        if a not in ids or b not in ids or a == b or (a, b) in seen:
            continue
        try:
            strength = min(3, max(1, int(round(float(e.get("strength", 2))))))
        except (TypeError, ValueError):
            strength = 2
        try:
            order = min(3, max(1, int(round(float(e.get("order", 1))))))
        except (TypeError, ValueError):
            order = 1
        edge = {"from": a, "to": b, "label": _word_cut(_clean_text(e.get("label"), 200), 38), "strength": strength, "order": order}
        for key, allowed, default in (("effect", ("up", "down", "mixed"), "mixed"),
                                      ("confidence", ("low", "medium", "high"), "medium"),
                                      ("status", ("reported", "predicted"), "predicted"),
                                      ("kind", ("causal", "feedback", "contradicts"), "causal"),
                                      ("lag", ("hours", "days", "weeks"), "")):
            val = str(e.get(key) or "").strip().lower()
            val = val if val in allowed else default
            if val:
                edge[key] = val
        why = _clean_text(e.get("why"), 220)
        if why:
            edge["why"] = why
        seen.add((a, b))
        edges.append(edge)
    # one direction per pair (keep the stronger), unless it is a deliberate feedback loop
    best = {}
    for e in edges:
        key = frozenset((e["from"], e["to"]))
        if e.get("kind") == "feedback":
            continue
        if key not in best or e["strength"] > best[key]["strength"]:
            best[key] = e
    edges = [e for e in edges if e.get("kind") == "feedback" or best[frozenset((e["from"], e["to"]))] is e]
    for e in edges:  # weak links stay (drawn thin on the full map); small views can skip them
        if e["strength"] <= 1 and e.get("confidence") == "low" and e.get("kind") != "contradicts":
            e["weak"] = True
    edges = edges[:MAX_EDGES]
    used = {x for e in edges for x in (e["from"], e["to"])}
    nodes = [n for n in nodes if n["id"] in used]  # drop orphans
    if len(nodes) < 4 or len(edges) < 3:
        return None
    return {"nodes": nodes, "edges": edges}


def enrich_graph(g, snapshot, thread_store, rows_meta):
    """Attach source links, today's market move and thread age to each node (no LLM)."""
    by_label = {m["label"]: m for m in snapshot or []}
    threads = (thread_store or {}).get("threads", [])
    for n in g["nodes"]:
        ids_ = n.pop("ids", [])
        n["sources"] = [{"title": rows_meta[i][0], "link": rows_meta[i][1]}
                        for i in ids_ if 0 <= i < len(rows_meta)][:3]
        m = by_label.get(n.pop("market", ""))
        if m and m.get("price") is not None:
            n["market"] = {"label": m["label"], "price": market.fmt_price(m), "change": market.fmt_change(m),
                           "change_pct": m.get("change_pct")}
        toks = archive.tokens(n["label"] + " " + n.get("detail", ""))
        best, shared = None, 0
        for t in threads:
            k = len(toks & set(t.get("keywords", [])))
            if k > shared:
                best, shared = t, k
        if best and shared >= 3 and len(best.get("stories", [])) >= 2:
            try:
                first = datetime.fromisoformat(best["first_seen"])
                days = max(1, (NOW - first.astimezone(ET)).days)
            except Exception:
                days = None
            n["thread"] = {"id": best["id"], "stories": len(best["stories"]), "days": days,
                           "first_seen": best["first_seen"][:10]}
    return g


# market instrument label -> headline words that mean the story is about it
MARKET_WORDS = {
    "S&P 500": {"wall", "s&p", "dow", "nasdaq", "stocks", "equities"}, "Nasdaq": {"nasdaq", "tech", "stocks"},
    "Nifty 50": {"nifty", "sensex", "dalal", "indian"}, "TSX": {"tsx", "toronto", "canadian"},
    "USD/INR": {"rupee", "inr"}, "USD/CAD": {"loonie", "canadian", "cad"},
    "Gold": {"gold", "bullion"}, "WTI Crude": {"oil", "crude", "opec", "brent", "wti", "tanker"},
    "Bitcoin": {"bitcoin", "crypto", "btc"},
}


def build_fallback_graph(stories, thread_store, snapshot=None):
    """No-AI map from story threads (cross-domain or 3+ stories) plus big market movers.
    Edges join threads that share keywords, and movers to threads about them.
    Returns {"graph":..., "source":"threads"} or None."""
    threads = [t for t in (thread_store or {}).get("threads", [])
               if len(t.get("stories", [])) >= 2]
    threads.sort(key=lambda t: (len(t["domains"]) > 1, len(t["stories"]), t.get("last_seen", "")), reverse=True)
    threads = threads[:30]
    nodes = []
    for i, t in enumerate(threads, 1):
        latest = sorted(t["stories"], key=lambda x: x["date"], reverse=True)
        try:
            days = max(1, (NOW - datetime.fromisoformat(t["first_seen"]).astimezone(ET)).days)
        except Exception:
            days = None
        nodes.append({
            "id": f"n{i}", "label": _clean(t["title"], 28), "type": "event",
            "domain": t["domains"][0] if t["domains"] and t["domains"][0] in DOMAIN_ORDER else "",
            "region": latest[0].get("region") if latest[0].get("region") in REGION_WEIGHT else "Global",
            "detail": _clean(f"{len(t['stories'])} stories across {', '.join(t['domains'][:3])}. Latest: {latest[0]['title']}", 260),
            "sources": [{"title": x["title"], "link": x["link"]} for x in latest[:3]],
            "thread": {"id": t["id"], "stories": len(t["stories"]), "days": days, "first_seen": t["first_seen"][:10]},
        })
    edges = []
    for i in range(len(threads)):
        for j in range(i + 1, len(threads)):
            shared = set(threads[i].get("keywords", [])) & set(threads[j].get("keywords", []))
            if len(shared) >= 2:
                a, b = (i, j) if threads[i]["last_seen"] >= threads[j]["last_seen"] else (j, i)
                edges.append({"from": f"n{a+1}", "to": f"n{b+1}", "label": _clean("shares: " + ", ".join(sorted(shared)[:3]), 32),
                              "strength": 3 if len(shared) >= 4 else 2 if len(shared) == 3 else 1,
                              "status": "reported", "confidence": "low", "order": 1, "kind": "causal", "effect": "mixed"})
    # Few threads yet (early days, quiet news): add today's top stories as nodes, linked by shared words
    if len(nodes) < 14:
        known = {x["link"] for t in threads for x in t["stories"]}
        extra = [x for x in sorted(stories, key=lambda x: -x["score"]) if x["link"] not in known][:14 - len(nodes)]
        base = len(nodes)
        toks = [archive.tokens(x["title"]) for x in extra]
        for k, x in enumerate(extra):
            nodes.append({"id": f"n{base + k + 1}", "label": _clean(x["title"], 28), "type": "event",
                          "domain": x["domain"] if x["domain"] in DOMAIN_ORDER else "",
                          "region": x["region"] if x["region"] in REGION_WEIGHT else "Global",
                          "detail": _clean(x.get("summary") or x["title"], 260),
                          "sources": [{"title": x["title"], "link": x["link"]}]})
        for a in range(len(extra)):
            for b in range(a + 1, len(extra)):
                shared = toks[a] & toks[b]
                if len(shared) >= 2:
                    edges.append({"from": f"n{base + a + 1}", "to": f"n{base + b + 1}",
                                  "label": _clean("shares: " + ", ".join(sorted(shared)[:3]), 32), "strength": 1,
                                  "status": "reported", "confidence": "low", "order": 1, "kind": "causal", "effect": "mixed"})
        # link new story nodes to the nearest thread by shared words, so the map joins up
        for a, x in enumerate(extra):
            for i, t in enumerate(threads):
                if len(toks[a] & set(t.get("keywords", []))) >= 2:
                    edges.append({"from": f"n{i + 1}", "to": f"n{base + a + 1}", "label": "same story line", "strength": 1,
                                  "status": "reported", "confidence": "low", "order": 1, "kind": "causal", "effect": "mixed"})
                    break
    # market movers (>= 1% either way) linked to threads that talk about them
    n_id = len(nodes)
    for m in snapshot or []:
        pct = m.get("change_pct")
        if m.get("price") is None or pct is None or abs(pct) < 1:
            continue
        words = MARKET_WORDS.get(m["label"], set())
        hits = [i for i, n in enumerate(nodes) if words & archive.tokens(n["label"] + " " + n.get("detail", ""))]
        if not hits:
            continue
        n_id += 1
        nid = f"n{n_id}"
        nodes.append({"id": nid, "label": m["label"], "type": "index", "domain": "", "direction": "up" if pct > 0 else "down",
                      "detail": f"{m['label']} moved {market.fmt_change(m)} to {market.fmt_price(m)}.",
                      "market": {"label": m["label"], "price": market.fmt_price(m), "change": market.fmt_change(m), "change_pct": pct}})
        for i in hits[:3]:
            edges.append({"from": f"n{i+1}", "to": nid, "label": "moves the market", "strength": 2, "status": "predicted",
                          "confidence": "low", "order": 1, "kind": "causal", "effect": "up" if pct > 0 else "down"})
    edges = sorted(edges, key=lambda e: -e["strength"])[:60]
    used = {x for e in edges for x in (e["from"], e["to"])}
    nodes = [n for n in nodes if n["id"] in used]
    if len(nodes) < 2 or not edges:
        return None
    return {"graph": {"nodes": nodes, "edges": edges}, "source": "threads"}


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


def save_digest(domain_items, breaking, snapshot, intel, ranked_by, intel_lite=None, extra=None):
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
    if intel_lite:
        data["intel_lite"] = intel_lite
    if extra:
        data.update(extra)
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

def strip_ids(text) -> str:
    """Drop '(id 3)' / '(ids 6, 33)' citations the LLM sometimes leaves in."""
    return re.sub(r"\s*\((?:ids?|per [^()]*?id)\s*[\d,\s]+(?:and\s*\d+)?\)", "", str(text or ""))


def t(text) -> str:
    """Escape for Telegram HTML parse mode."""
    return html.escape(strip_ids(text), quote=False)


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


def send_telegram_photo(png: bytes, caption: str) -> bool:
    try:
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                             data={"chat_id": CHAT_ID, "caption": caption[:1000], "parse_mode": "HTML"},
                             files={"photo": ("connection_map.png", png, "image/png")}, timeout=60)
    except requests.RequestException as e:
        print(f"[TELEGRAM ERROR] map image: {type(e).__name__}")
        return False
    if resp.status_code == 200:
        print("[TELEGRAM] sent connection map")
        return True
    print(f"[TELEGRAM ERROR] map image: {resp.status_code}: {resp.text[:200]}")
    return False


# ---------------------------------------------------------------- email

def send_email(subject: str, html_content: str, attachments=None) -> bool:
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
    if attachments:
        payload["attachments"] = attachments
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=45)
    except requests.RequestException as e:
        print(f"[EMAIL ERROR] {type(e).__name__}")
        return False
    if resp.status_code == 200:
        print("[EMAIL] sent successfully")
        return True
    print(f"[EMAIL ERROR] {resp.status_code}: {resp.text}")
    return False


def build_email_html(domain_items: dict, breaking: list, snapshot: list, intel) -> str:
    date_str = NOW.strftime("%b %d, %Y")
    time_str = NOW.strftime("%I:%M %p ET")

    def esc(text):
        return html.escape(strip_ids(text))

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

    learning_cycle()  # grading only: no AI call before the digest is built

    items = fetch_rss() + fetch_google_news() + fetch_gnews() + fetch_newsdata()
    clusters = build_clusters(items)

    ranked = rank_with_llm(clusters) if llm.available() else None
    ranked_by = llm.last_model if ranked else "heuristic"
    stories, breaking = ranked or rank_heuristic(clusters)
    domain_items = select_digest(stories, breaking)
    print(f"[RANK] by {ranked_by}: {sum(len(v) for v in domain_items.values())} stories, {len(breaking)} breaking")

    snapshot = market.get_snapshot()
    try:
        import market_history
        market_history.record(snapshot, NOW)
    except Exception as e:
        print(f"[MARKET HISTORY] {type(e).__name__}: {e}")
    articles = update_article_store(clusters, stories)

    intel = None
    if slot_hour == INTEL_HOUR or FORCE_INTEL:
        since = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        intel = build_intelligence(stories, articles_since(articles, since), snapshot)

    # Pattern detection + prediction scoring (all failures are non-fatal)
    extra = {}
    try:
        if intel:
            recent = [a["title"] for a in articles_since(articles, NOW - timedelta(days=3))]
            patterns.review_predictions(NOW, llm, recent)
            if not DRY_RUN:
                patterns.record_predictions(intel.get("scenarios") or [], NOW)
        extra["patterns"] = patterns.detect_patterns(NOW)
        extra["prediction_accuracy"] = patterns.accuracy_summary()
    except Exception as e:
        print(f"[PATTERNS ERROR] {e}")

    # Record today's checkable calls so they can be graded later (never blocks a delivery)
    try:
        if intel:
            graph_now = intel.get("graph")
            if graph_now:
                learning_ledger.record_next_day_calls(graph_now, snapshot, NOW)
                learning_ledger.record_calls(graph_now, NOW)
            learning_ledger.record_scenarios(intel.get("scenarios") or [], NOW)
        learning_ledger.scorecard(NOW)
    except Exception as e:
        print(f"[LEARN] recording failed: {type(e).__name__}: {e}")

    message = build_telegram(domain_items, breaking, snapshot, intel)
    note_pending = False
    try:
        tline = track_page.telegram_line(learning_ledger.track_record(), priors=learning_priors.load_priors() or None)
        if tline:
            message += "\n\n\U0001F4CA " + t(tline)
        note = learning_memory.weekly_note(NOW, commit=False)
        if note:
            message += "\n" + "\n".join(t(line) for line in str(note).splitlines()[:3])
            note_pending = True
    except Exception as e:
        print(f"[LEARN] track line failed: {type(e).__name__}")
    email_html = build_email_html(domain_items, breaking, snapshot, intel)
    email_subject = f"📰 News Brief — {NOW.strftime('%b %d')} {NOW.strftime('%I:%M %p ET')}"
    if intel:
        email_subject += " + Intelligence Brief"

    try:
        added = archive.update_archive(domain_items, breaking, NOW)
        archive.update_threads(added, NOW)
    except Exception as e:  # memory is a bonus; never block a delivery
        print(f"[ARCHIVE ERROR] {e}")

    intel_lite = None
    try:
        intel_lite = build_fallback_graph(stories, load_json(archive.THREADS_FILE, {"threads": []}), snapshot)
        if intel_lite:
            finish_graph(intel_lite["graph"], snapshot)
            light_cards(intel_lite["graph"])
            compute_impact(intel_lite["graph"])
    except Exception as e:
        print(f"[GRAPH LITE ERROR] {e}")

    save_digest(domain_items, breaking, snapshot, intel, ranked_by, intel_lite, extra)

    # Static picture of the connection map for Telegram and email
    map_png, map_note = None, ""
    try:
        graph = (intel or {}).get("graph") or (intel_lite or {}).get("graph")
        if graph:
            map_note = "Connection map" if (intel or {}).get("graph") else "Connection map (from story threads, no AI brief this run)"
            map_png = graph_image.render_graph_png(graph, f"Connections — {NOW.strftime('%b %d, %I:%M %p ET')}")
    except Exception as e:
        print(f"[MAP ERROR] {e}")
    attachments = None
    if map_png:
        import base64
        attachments = [{"filename": "connection_map.png", "content": base64.b64encode(map_png).decode(),
                        "content_id": "connmap"}]
        block = (f'<div style="margin:16px 0"><div style="font-weight:600;margin-bottom:6px">{html.escape(map_note)}</div>'
                 f'<img src="cid:connmap" alt="Connection map" style="max-width:100%;height:auto"></div>')
        email_html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + block, email_html, count=1) if "<body" in email_html else block + email_html

    if DRY_RUN:
        os.makedirs("preview", exist_ok=True)
        with open("preview/telegram.html", "w", encoding="utf-8") as f:
            f.write(message)
        with open("preview/email.html", "w", encoding="utf-8") as f:
            f.write(email_html)
        if map_png:
            with open("preview/connection_map.png", "wb") as f:
                f.write(map_png)
        print(f"[DRY RUN] Telegram message ({len(message)} chars, {len(split_message(message))} parts) "
              f"and email written to preview/ — nothing sent")
        print(message[:3000])
    else:
        sent_tg = send_telegram(message)
        if sent_tg and note_pending:
            try:
                learning_memory.mark_note_sent(NOW)
            except Exception as e:
                print(f"[LEARN] could not mark the weekly note as sent: {type(e).__name__}")
        if sent_tg and map_png:
            send_telegram_photo(map_png, map_note)
        sent_mail = send_email(email_subject, email_html, attachments)
        if not (sent_tg or sent_mail):
            print("FATAL: both deliveries failed")
            sys.exit(1)
        save_state(slot)

    learning_cycle(llm_step=True)  # after delivery: the learning step may use leftover time only

    set_output("ran", "true")


if __name__ == "__main__":
    main()
