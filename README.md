# news-pipeline

Automated news digest: 4 times a day (8:00, 11:00, 15:00, 18:00 Toronto time) it collects news from free sources, merges duplicates, ranks by importance, and delivers to Telegram, email and a web page: <https://manangoesagain.github.io/news-pipeline/>.

## What's in each digest

- **Breaking**: major developments from the last 6 hours
- **Seven topics**, top 5 stories each: AI & ML, tech & semiconductors, finance & banking, stock markets, geopolitics, wars & conflicts, commodities
- **Market snapshot**: S&P 500, Nasdaq, Nifty 50, TSX, USD/INR, USD/CAD, gold, WTI, BTC
- **Intelligence brief** (6 PM only): causal chains, second-order effects, if-then scenarios, contrarian signals

Ranking favours India, the US and Canada, then China.

## Deep dives

Send `/deepdive <topic>` to the Telegram bot (e.g. `/deepdive India US trade deal`). Every ~10 minutes a GitHub Action checks for new commands, searches the last 7 days of collected articles, and replies with a timeline, key players, drivers, outlook and sources. `/help` shows usage. You can also run **Actions → Deep Dive Bot → Run workflow** with a topic.

## How it works

| File | Role |
|---|---|
| `digest.py` | Fetch, dedupe, rank, build and send the digest; save data for the page |
| `market.py` | Market snapshot (Yahoo chart API, CNBC quote feed fallback, CoinGecko) |
| `llm.py` | LLM client: Gemini if `GEMINI_API_KEY` is set, otherwise NVIDIA NIM |
| `build_page.py` | Builds `index.html` from the last 4 saved digests |
| `deepdive.py` | `/deepdive` Telegram bot |
| `.github/workflows/digest.yml` | Hourly; delivers each slot once, even if GitHub starts the run late |
| `.github/workflows/deepdive.yml` | Polls Telegram every 10 minutes |

**Sources:** RSS (BBC, Guardian, Al Jazeera, NPR, CBC, NDTV, The Hindu, Economic Times, Mint, TechCrunch, Ars Technica, The Verge, CNBC, MarketWatch), Google News searches (including Reuters and AP), GNews and NewsData.io.

**Data:** saved digests, the 7-day article store (`_data/articles.json`) and delivery state live on the `gh-pages` branch, which is rewritten as a single commit each run.

If no LLM is available, the digest falls back to keyword classification and ranking by how many outlets cover a story; the intelligence brief is skipped.

## Repository secrets

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY`, `GNEWS_API_KEY`, `NEWSDATA_API_KEY`, and at least one of `GEMINI_API_KEY` / `NVIDIA_API_KEY`.

## Manual runs

**Actions → News Digest → Run workflow**:

- **force**: run now even outside a digest hour
- **dry_run**: build everything but send nothing and don't deploy; the Telegram text, email and page are attached to the run as a `preview` artifact
- **intel**: include the intelligence brief outside the 6 PM run
