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
| `track_page.py` | The "Track record" strip on the page and the weekly Telegram line |
| `learning_*.py` | Self-learning loop: `io` (fenced file writes), `ledger` (calls and grading), `priors` (10-year history), `memory` (lessons, weights, revert), `meta` (weekly self-tuning) |
| `jev_judge.py` | Optional TypeSafe "Jev" yes/no checks (needs `TYPESAFE_API_KEY`) |
| `deepdive.py` | `/deepdive` Telegram bot |
| `.github/workflows/digest.yml` | Hourly; delivers each slot once, even if GitHub starts the run late |
| `.github/workflows/deepdive.yml` | Polls Telegram every 10 minutes |

**Sources:** RSS (BBC, Guardian, Al Jazeera, NPR, CBC, NDTV, The Hindu, Economic Times, Mint, TechCrunch, Ars Technica, The Verge, CNBC, MarketWatch), Google News searches (including Reuters and AP), GNews and NewsData.io.

**Data:** saved digests, the 7-day article store (`_data/articles.json`) and delivery state live on the `gh-pages` branch, which is rewritten as a single commit each run. The learning files live in `_data/_learning/` on the same branch.

If no LLM is available, the digest falls back to keyword classification and ranking by how many outlets cover a story; the intelligence brief is skipped.

## Repository secrets

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY`, `GNEWS_API_KEY`, `NEWSDATA_API_KEY`, and at least one of `GEMINI_API_KEY` / `NVIDIA_API_KEY`. Optional: `TYPESAFE_API_KEY` (see below).

## Manual runs

**Actions → News Digest → Run workflow**:

- **force**: run now even outside a digest hour
- **dry_run**: build everything but send nothing and don't deploy; the Telegram text, email and page are attached to the run as a `preview` artifact
- **intel**: include the intelligence brief outside the 6 PM run

## Self-learning loop

The digest keeps score of itself and slowly gets better at the one thing that can be checked: price calls.

**What it does**
- **Records calls.** Whenever the digest says a market or instrument is likely to go up or down, it writes that down with a date. The same idea is not counted twice inside its own time window, so the sample stays honest.
- **Grades them.** Later it compares each call with real prices. Every call is also scored against a dumb "momentum baseline" (just assume yesterday's direction continues). Beating the baseline is what counts, not just being right. Flat outcomes are never counted as hits.
- **Starts with 10 years of history.** On day 1 it already knows patterns such as "when the S&P 500 jumps, how often does Nifty rise next". These are called priors.
- **Writes its own lessons.** Short rules of thumb, per-topic playbooks and numeric weights, saved as plain text/data only. They are pasted into later prompts; they are never run as code.
- **Retunes itself weekly.** Once a week, without any AI, it replays past calls and nudges its own settings by at most one small step, and retires lessons that made later calls worse.
- **Tests wording.** About 20% of the cards use an experimental prompt wording, one experiment at a time. It is cancelled automatically if it does worse than the normal wording.
- **Auto-reverts.** If accuracy drops clearly after a change (enough new calls, at least 8 points worse), it restores the last good snapshot and marks the bad one so it is not retried.
- **Reports.** A weekly 3-line note goes to Telegram, and the web page shows a "Track record" strip.

**The hard fence.** The loop can only write data files (`.json`, `.md`, `.txt`) inside `_data/_learning`. It can never touch code, workflows, secrets or the email recipient. Anything that looks like a key or an email address is blanked out before saving, and paths outside the folder are refused.

**Files in `_data/_learning`** (on `gh-pages`)

| File | What it is for |
|---|---|
| `calls.json`, `calls_archive.json` | Open and graded calls; older graded ones |
| `scorecard.json` | Latest accuracy numbers |
| `priors.json`, `history/` | 10-year statistics and the cached price history behind them |
| `lessons.md`, `playbooks.md` | The loop's own rules of thumb and per-topic notes |
| `weights.json` | Numeric calibration, computed in code |
| `settings.json` | Its tunable knobs, kept inside fixed limits |
| `experiments.json`, `adopted.json` | The running wording experiment and wordings it kept |
| `meta.json`, `state.json` | Weekly review bookkeeping, last cycle, bad snapshots |
| `changelog.md` | Newest-first list of every self-change (last 40) |

Old snapshots (last 14) and retired lessons are also kept so any change can be undone by hand.

**Kill switch.** Create a repository variable named `LEARNING_ENABLED` set to `false` at <https://github.com/manangoesagain/news-pipeline/settings/variables/actions/new>. Empty or `true` means on. When off, the loop stops writing lessons, weights, settings and experiments and stops reverting; the digest itself carries on exactly as before. Existing files stay where they are. To resume, set it to `true` or delete the variable.

**Optional secrets**
- `GEMINI_API_KEY` (already above): lets the loop write its lessons and playbooks in words. Without an LLM it still records, grades and reports numbers.
- `TYPESAFE_API_KEY`: turns on the **Jev** add-on from TypeSafe, which answers small yes/no probability questions about headlines as an extra check. Without the key it is skipped completely and nothing changes. It only talks to `api.typesafe.ai`, has a time and request budget, and never logs the key.

**Reading the Track record strip.** It shows the overall hit rate, the momentum baseline next to it, and "skill" (points above the baseline; positive is good). Smaller bars break results down by topic and instrument. No percentage is shown for fewer than 5 calls, and the overall number is only trusted after 30 price-checked calls; until then it says "building" or "not enough data". A high "no move" share means many calls ended flat and were not counted.

**Honest limits**
- Samples are small at first; early numbers can swing wildly.
- Markets change, so a pattern that worked can stop working.
- Most priors are weak. The clear exception is the S&P 500 to Nifty lead-lag.
- It makes no profit claims. It is for reading the news, not trading advice.

**Troubleshooting.** Open **Actions → News Digest →** the run → the "Run digest and send to Telegram + Email" step log and search for `[LEARN]` (learning loop) or `[JEV]` (Jev add-on). Each problem prints one line there and the digest carries on regardless. A missing strip or "building" message usually just means too few graded calls yet.
