#!/usr/bin/env python3
"""
/deepdive bot — polled from GitHub Actions every ~10 minutes.

Reads new Telegram messages with getUpdates (no webhook or server needed),
answers "/deepdive <topic>" from the configured chat using the 7-day article
store that digest.py publishes to gh-pages (_data/articles.json).
A manual run can pass TOPIC instead of polling.
"""

import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import llm

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TOPIC = (os.environ.get("TOPIC") or "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "manangoesagain/news-pipeline")
STORE_PATH = os.path.join("_data", "articles.json")
STORE_URL = f"https://raw.githubusercontent.com/{REPO}/gh-pages/_data/articles.json"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

ET = ZoneInfo("America/Toronto")
MAX_ARTICLES = 150
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "with",
        "about", "what", "whats", "news", "latest", "happening", "update", "updates"}

HELP = ("<b>🔎 Deep dive bot</b>\n"
        "Send <code>/deepdive topic</code>, e.g. <code>/deepdive India US trade deal</code> or "
        "<code>/deepdive Nvidia export controls</code>.\n"
        "I search the last 7 days of collected articles and reply with a timeline, key players, "
        "drivers and what to watch. Replies take 10-30 minutes (checked every 10 min).")


# ---------------------------------------------------------------- Telegram

def t(text) -> str:
    return html.escape(str(text or ""), quote=False)


def send(text: str):
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    for chunk in chunks:
        resp = requests.post(f"{API}/sendMessage", timeout=20, json={
            "chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True})
        if resp.status_code != 200:
            print(f"[TELEGRAM ERROR] {resp.status_code}: {resp.text[:200]} — resending as plain text")
            requests.post(f"{API}/sendMessage", timeout=20, json={
                "chat_id": CHAT_ID, "text": html.unescape(re.sub(r"<[^>]+>", "", chunk)),
                "disable_web_page_preview": True})


def poll_commands():
    """Return the list of commands from the configured chat and mark all updates read."""
    resp = requests.get(f"{API}/getUpdates", timeout=20,
                        params={"timeout": 0, "allowed_updates": json.dumps(["message"])})
    data = resp.json()
    if not data.get("ok"):
        # 409 means a webhook is set on this bot; getUpdates can't be used alongside it
        print(f"[POLL] getUpdates failed: {data.get('error_code')} {data.get('description')}")
        return []
    updates = data.get("result", [])
    if not updates:
        print("[POLL] no new messages")
        return []

    # Acknowledge first so a crash mid-run can't make the same request repeat forever
    last_id = max(u["update_id"] for u in updates)
    requests.get(f"{API}/getUpdates", timeout=20, params={"offset": last_id + 1, "timeout": 0})

    commands = []
    for u in updates:
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(CHAT_ID):
            print("[POLL] ignoring message from another chat")
            continue
        text = (msg.get("text") or "").strip()
        m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text, re.DOTALL)
        if m:
            commands.append((m.group(1).lower(), m.group(2).strip()))
    print(f"[POLL] {len(updates)} updates, {len(commands)} commands")
    return commands


# ---------------------------------------------------------------- article search

def load_articles():
    if os.path.exists(STORE_PATH):
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("articles", [])
    try:
        resp = requests.get(STORE_URL, timeout=30)
        resp.raise_for_status()
        return resp.json().get("articles", [])
    except Exception as e:
        print(f"[STORE] could not load article store: {e}")
        return []


def find_articles(articles, topic):
    phrase = topic.lower()
    tokens = [w for w in re.findall(r"[a-z0-9]+", phrase) if w not in STOP and len(w) > 1]
    if not tokens:
        return []
    pats = {w: re.compile(r"(?<!\w)" + re.escape(w) + r"s?(?!\w)", re.IGNORECASE) for w in tokens}
    phrase_pat = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)

    def score(a, require_all):
        title, summary = a.get("title", ""), a.get("summary", "")
        hits = [w for w in tokens if pats[w].search(title) or pats[w].search(summary)]
        if require_all and len(hits) < len(tokens):
            return 0
        s = sum(2 for w in tokens if pats[w].search(title)) + sum(1 for w in tokens if pats[w].search(summary))
        if len(tokens) > 1 and (phrase_pat.search(title) or phrase_pat.search(summary)):
            s += 5
        return s

    for require_all in (True, False):
        scored = [(score(a, require_all), a) for a in articles]
        matched = [(s, a) for s, a in scored if s > 0]
        # With partial matching, require at least half of the topic words
        if not require_all:
            min_hits = max(1, (len(tokens) + 1) // 2)
            matched = [(s, a) for s, a in matched
                       if sum(1 for w in tokens if pats[w].search(a.get("title", "") + " " + a.get("summary", ""))) >= min_hits]
        if len(matched) >= 5 or not require_all:
            matched.sort(key=lambda sa: sa[1].get("published", ""), reverse=True)
            matched.sort(key=lambda sa: -sa[0])  # stable: best score first, newest within ties
            top = [a for _, a in matched[:MAX_ARTICLES]]
            return sorted(top, key=lambda a: a.get("published", ""))
    return []


# ---------------------------------------------------------------- analysis

DEEPDIVE_PROMPT = """You are a senior analyst. The reader (based in Toronto; follows India, the US and Canada closely, then China) asked for a deep dive on: "{topic}".

Below are {n} articles from the last 7 days that mention it, oldest first (id | date | source | headline | snippet). Some may be only loosely related; ignore those.

Using ONLY these articles, return JSON:
{{
  "headline": "one-line state of play",
  "summary": "4-5 sentences: what happened, why it matters, where it stands now",
  "timeline": [{{"date": "Oct 18", "event": "...", "ids": [3]}}],
  "key_players": [{{"name": "...", "role": "what they want / did"}}],
  "drivers": ["underlying cause or force"],
  "competing_views": [{{"view": "...", "held_by": "..."}}],
  "outlook": [{{"if": "...", "then": "...", "likelihood": "low|medium|high"}}],
  "watch_next": ["specific dated event, data release or decision to watch"],
  "key_sources": [ids of the 5 most informative articles]
}}
5-10 timeline entries; 3-6 key players; 2-4 of each other list. If the articles don't really cover the topic, say so in "summary" and keep the lists short.

Articles:
{lines}
"""


def fmt_date(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(ET).strftime("%b %d")
    except Exception:
        return "?"


def deep_dive(topic, articles):
    matches = find_articles(articles, topic)
    print(f"[DEEPDIVE] '{topic}': {len(matches)} matching articles")
    if not matches:
        send(f"🔎 <b>Deep dive: {t(topic)}</b>\nNo articles in the last 7 days mention this. "
             f"Try broader or different words (e.g. a company, country or person name).")
        return

    lines = "\n".join(
        f"{i} | {fmt_date(a.get('published'))} | {a.get('source', '')} | {a.get('title', '')} | "
        f"{(a.get('summary') or '')[:160]}"
        for i, a in enumerate(matches))
    result = llm.generate_json(DEEPDIVE_PROMPT.format(topic=topic, n=len(matches), lines=lines),
                               label="DEEPDIVE", require="summary", temperature=0.3)

    days = sorted({fmt_date(a.get("published")) for a in matches})
    out = [f"🔎 <b>Deep dive: {t(topic)}</b>",
           f"<i>{len(matches)} articles · {len(days)} days of coverage</i>", ""]

    if not result:
        out.append("AI analysis is unavailable right now; here are the most relevant articles:")
        for a in matches[-15:]:
            out.append(f'• {fmt_date(a.get("published"))} — <a href="{html.escape(a["link"])}">{t(a["title"])}</a> '
                       f'<i>({t(a.get("source"))})</i>')
        send("\n".join(out))
        return

    if result.get("headline"):
        out.append(f"<b>{t(result['headline'])}</b>")
    if result.get("summary"):
        out += [t(result["summary"]), ""]
    if result.get("timeline"):
        out.append("<b>🗓 Timeline</b>")
        for e in result["timeline"]:
            out.append(f"• <b>{t(e.get('date'))}</b> — {t(e.get('event'))}")
        out.append("")
    if result.get("key_players"):
        out.append("<b>👥 Key players</b>")
        for p in result["key_players"]:
            out.append(f"• <b>{t(p.get('name'))}</b>: {t(p.get('role'))}")
        out.append("")
    if result.get("drivers"):
        out.append("<b>⚙️ Drivers</b>")
        out += [f"• {t(d)}" for d in result["drivers"]]
        out.append("")
    if result.get("competing_views"):
        out.append("<b>⚖️ Competing views</b>")
        for v in result["competing_views"]:
            out.append(f"• {t(v.get('view'))} <i>— {t(v.get('held_by'))}</i>")
        out.append("")
    if result.get("outlook"):
        out.append("<b>🔮 Outlook (if → then)</b>")
        for o in result["outlook"]:
            out.append(f"• <b>If</b> {t(o.get('if'))} <b>then</b> {t(o.get('then'))} [{t(o.get('likelihood', '?'))}]")
        out.append("")
    if result.get("watch_next"):
        out.append("<b>👀 Watch next</b>")
        out += [f"• {t(w)}" for w in result["watch_next"]]
        out.append("")
    ids = [i for i in result.get("key_sources") or [] if isinstance(i, int) and 0 <= i < len(matches)]
    if ids:
        out.append("<b>📚 Key sources</b>")
        for i in ids[:6]:
            a = matches[i]
            out.append(f'• <a href="{html.escape(a["link"])}">{t(a["title"])}</a> <i>({t(a.get("source"))}, '
                       f'{fmt_date(a.get("published"))})</i>')
        out.append("")
    out.append("<i>AI-generated from collected headlines; not investment advice.</i>")
    send("\n".join(out))


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("FATAL: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as repository secrets.")
        sys.exit(1)

    commands = [("deepdive", TOPIC)] if TOPIC else poll_commands()
    if not commands:
        return

    articles = None
    for cmd, arg in commands[:3]:  # cap per run to stay within free LLM limits
        if cmd in ("start", "help") or (cmd == "deepdive" and not arg):
            send(HELP)
        elif cmd == "deepdive":
            if articles is None:
                articles = load_articles()
                print(f"[STORE] {len(articles)} articles loaded")
            if not TOPIC:
                send(f"🔎 Working on a deep dive into <b>{t(arg)}</b>…")
            deep_dive(arg[:120], articles)


if __name__ == "__main__":
    main()
