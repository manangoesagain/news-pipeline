# Handoff: Hermes news pipeline

Written for a session with no memory of the project. Facts below were checked against the code, git state and GitHub on 2026-11-02 unless marked **unverified**. Section 9 lists what was and wasn't verified.

## 1. What this is

An automated news intelligence system for Manan (GitHub: `manangoesagain`). Four times a day it fetches news from free, no-paywall sources, classifies and de-duplicates stories, ranks them with an LLM, and delivers a digest by **Telegram**, **email (Resend)** and a **GitHub Pages web page**: <https://manangoesagain.github.io/news-pipeline/>.

- **Schedule:** 8:00, 11:00, 15:00 and 18:00 Toronto time. The workflow runs hourly (`0 * * * *`) and `digest.py` acts only on those four slots, delivering each slot once even if GitHub starts the run late.
- **Content:** a BREAKING section (last 6 hours), then the top 5 stories in each of seven domains: AI & ML, Technology & Semiconductors, Finance & Banking, Stock Markets & Trading, Geopolitics, Wars & Conflicts, Commodities & Prices (see `DOMAIN_ORDER` in `digest.py`). Ranking favours India, the US and Canada, then China.
- **Sources:** free RSS (BBC, Guardian, Al Jazeera, NPR, CBC, NDTV, The Hindu, Economic Times, Mint, TechCrunch, Ars Technica, The Verge, CNBC, MarketWatch), Google News searches (which is how Reuters and AP are reached), plus the GNews and NewsData.io APIs. No Bloomberg, FT, WSJ, Economist or Stratechery (paywalled).
- **LLM:** `llm.py` uses Google Gemini when `GEMINI_API_KEY` is set, otherwise NVIDIA NIM (`NVIDIA_API_KEY`). It tries a list of models in turn until one returns valid JSON. The original plan named `gemini-2.0-flash`; today it is the *last* Gemini fallback, after `gemini-2.5-flash-lite`, `gemini-flash-latest`, `gemini-flash-lite-latest` and `gemini-2.5-flash`. With no LLM at all the digest falls back to keyword classification and ranking by outlet count.

## 2. The brief was out of date: what is actually built

The project instructions say the browser page, market snapshot, intelligence layer and deep dive bot are not built yet. **They are all built and running on `main`.**

| Feature | Status | Where |
|---|---|---|
| Fetch, classify, dedupe, rank | Working | `digest.py`, `llm.py` |
| Telegram delivery | Working | `digest.py` |
| Email (HTML via Resend) | Working | `digest.py` (`send_email`, `build_email_html`) |
| GitHub Pages page (latest 4 digests) | Built and deployed | `build_page.py`, `archive_page.py`, `gh-pages` branch |
| Market snapshot (S&P 500, Nasdaq, Nifty 50, TSX, USD/INR, USD/CAD, gold, WTI, BTC) | Built | `market.py`, `market_history.py`; shown in Telegram, email and page |
| Intelligence brief (6 PM only): causal chains, scenarios, contrarian signals, with connection map graphics | Built | `build_intelligence` in `digest.py`, `graph_page.py`, `graph_image.py` |
| Deep dive bot (`/deepdive <topic>` over 7 days of articles) | Built | `deepdive.py`, `.github/workflows/deepdive.yml` (polls Telegram every 10 minutes) |
| Permanent archive and story threads | Built | `archive.py`, `archive_page.py`, `patterns.py` |
| Self-learning loop (records price calls, grades them, writes lessons, weekly self-tuning, auto-revert) | Built | `learning_*.py`, `track_page.py`; full description in `README.md` |
| Jev add-on (optional yes/no probability checks) | Built, off unless `TYPESAFE_API_KEY` is set | `jev_judge.py` |

Evidence: the last scheduled digest run on `main` (run 120, 2026-11-02 08:36 UTC) succeeded, `gh-pages` holds `index.html`, `archive.html`, four `digest_*.json` files and a populated `_data/_learning/`, and the full offline suite passes (389 tests on `main`).

I could **not** confirm from here that the Telegram message, email and market snapshot look right on a real device, only that the code paths exist and runs succeed. Ask Manan if something looks wrong.

## 3. Repo layout

Root files (all Python, flat layout, no package):

- `digest.py` (about 1900 lines): the main program. Fetches, classifies, ranks, builds the intelligence brief, sends Telegram and email, saves `_data/` files. Entry point for the workflow.
- `llm.py`: LLM client with model fallback, retry, salvage of malformed JSON, idle timeout and hard time limit.
- `market.py`, `market_history.py`: market snapshot (Yahoo chart API, CNBC quote feed fallback, CoinGecko for BTC) and a 400-day history file.
- `build_page.py`, `archive_page.py`, `track_page.py`: build `index.html`, `archive.html` and the "Track record" strip.
- `graph_page.py`, `graph_image.py`: connection map, scenario tree and causal flow renderers (inline SVG for the page, PNG cards for Telegram and email; matplotlib).
- `archive.py`, `patterns.py`: permanent archive by day, story threads, pattern detection and a predictions ledger.
- `deepdive.py`: the `/deepdive` Telegram bot.
- `learning_io.py`, `learning_ledger.py`, `learning_priors.py`, `learning_memory.py`, `learning_meta.py`: the self-learning loop. `learning_io` is the hard fence: it may only write data files (`.json`, `.md`, `.txt`) inside `_data/_learning`, redacts anything that looks like a key or email, and refuses paths outside the folder.
- `jev_judge.py`: optional TypeSafe "Jev" client. Does nothing without the key; never raises; talks only to `api.typesafe.ai`; has time and request budgets.
- `learning_seed/`: a bundled 10-year price history snapshot used only when fresh history can't be downloaded.
- `tests/`: offline test suite, no network, stubs for feedparser, requests and matplotlib (`tests/_stubs.py`).
- `requirements.txt`: `feedparser`, `requests`, `matplotlib`.
- `README.md`: user-facing overview plus a long self-learning section (kill switch, files, honest limits). Read it for the learning loop.

Workflows:

- `.github/workflows/digest.yml` ("News Digest"): hourly. Restores `_data` from `gh-pages`, runs `archive.py`, then `digest.py`, then `build_page.py`, then deploys to `gh-pages` with `force_orphan: true`. Manual run inputs: `force`, `dry_run` (builds everything, sends nothing, uploads a `preview` artifact), `intel`. Concurrency group `news-digest` so two runs never race.
- `.github/workflows/deepdive.yml` ("Deep Dive Bot"): every 10 minutes, plus manual run with a topic.

### Branches (as of 2026-11-02)

| Branch | State |
|---|---|
| `main` | Production. Workflows run from here. |
| `recursive-learning` | Identical to `main` (0 ahead, 0 behind). |
| `self-learning-loop` | Fully merged history, 3 commits behind `main`. |
| `ai-map-reliability` | Fully merged, 4 behind `main`. |
| `focus-cards-preview` | Fully merged, 10 behind `main`. |
| `fix/latent-crash-guards` | Open PR #1 (section 5). |
| `gh-pages` | Generated output, not source. See below. |

The first four non-`main` branches carry no unmerged work; they are old feature branches and could be deleted (Manan's call).

### gh-pages is not a normal branch

Each run deploys with `force_orphan: true`, so `gh-pages` is **rewritten as a single commit every run** ("deploy: <sha of main>"). There is no history to recover. It holds:

- the published site: `index.html`, `archive.html`
- `_data/digest_*.json` (latest digests), `_data/articles.json` (7-day article store used by the deep dive bot), `_data/state.json` (last delivered slot), `_data/market_history.json`, `_data/predictions.json`, `_data/threads.json`, `_data/archive/`
- `_data/_learning/`: the learning loop's calls, scorecard, priors, lessons, weights, settings, experiments, changelog and snapshots

Consequence: a run that crashes before deploy loses that run's article store and learning writes, and nothing can be restored. This is why PR #1 matters. Never edit `gh-pages` by hand without copying the data first. The learning folder is therefore **public** (public repo, free Pages); see open questions.

## 4. Secrets and variables

Names only. Manan adds them himself in repo settings.

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RESEND_API_KEY`, `GNEWS_API_KEY`, `NEWSDATA_API_KEY`, `GEMINI_API_KEY`, `NVIDIA_API_KEY`, optional `TYPESAFE_API_KEY` (Jev), optional `RECIPIENT_EMAIL_OVERRIDE`. Variable `LEARNING_ENABLED=false` is the learning-loop kill switch.

I cannot see which secrets are actually set. **Unverified:** whether `TYPESAFE_API_KEY` is configured, i.e. whether Jev is live today.

## 5. Open work

**PR #1, `fix/latent-crash-guards`** (<https://github.com/manangoesagain/news-pipeline/pull/1>): awaiting Jay's review, no review or CI results seen yet. It does three things, no behaviour change on any path that works today:

1. `select_digest` skips (and logs) a story whose domain isn't in `DOMAIN_ORDER` instead of raising. Not reachable today; one typo in a feed's domain hint would make it reachable and cost a whole slot.
2. A test that `DEFAULT_SETTINGS` and `SETTING_BOUNDS` hold the same keys (a mismatch would silently disable the snapshot, auto-revert and weekly tuner path).
3. A test that ledger keys stay short, because `learning_io` redacts any run of 32 or more hex characters, which would collapse a store keyed by a full md5 or sha digest.

Diff verified: `digest.py` (+9/-2) and new `tests/test_config_guards.py` (122 lines). The PR says 399 tests pass (389 plus 10); I confirmed 389 on `main` and did not re-run the PR branch.

## 6. Verification loop and Jev

**The idea:** the system already grades its own price calls against real prices. The verification loop adds a second check, using Jev (TypeSafe) to give yes/no probabilities on headline claims and on the links in the intelligence brief's causal graph, so unsupported links get dropped and soft ("if X then Y") calls can be graded.

**What already exists in code** (`jev_judge.py` and hooks in `digest.py`): `verify_links` / `drop_unsupported` filter graph edges (`jev_filter_links`), `grade_soft` grades soft calls (`jev_grade_soft_calls`), a `ping` self-test, per-phase budgets (`begin_phase`), 90 s default and 150 s global time budgets, and a fixed API host. Without `TYPESAFE_API_KEY` every call is skipped silently.

**Decisions from Jay** (relayed from the project, not visible in the repo): about **$3 to $4 a month** of Jev spend is approved. Telegram thumbs up/down feedback buttons are **paused for later**.

**Still unanswered by Jay:**
1. The one-time gold set (about 50 past stories labelled junk or not, 20 minutes; recommended after the shadow week).
2. Whether the `_data/_learning` folder stays public.

The Jev design plan was written in the project's "Research Jev for news pipeline" thread. A condensed copy is in **`docs/jev-plan.md`** (design only, no code yet; next step is a "Phase 0 / PR 2" of Jev plumbing: pin model, reserve budgets, parallel requests, wall-clock guard). The plan estimates under $1 a month, inside the approved ceiling.

## 7. History on disk (Manan's Windows machine)

Base folder: `C:\Users\enma6\Desktop\Claude Projects import\`

- `old-account\hermes\Hermes agent conversation - top 20 news sources.md`: the original chat with the Hermes agent.
- `old-account\hermes\setup-guide-all-services.md`: click-by-click setup of every account and key (keys masked).
- `old-account\hermes\hermes-news-pipeline-prompts.md`: the original Hermes prompts 1 to 5.
- `old-account\cleaned\Hermes news pipeline - prompts.md`: the same prompts as typed in the session.
- `transcripts\7 Hermes agent\2026-10-17 03bb7441.md`: the full 100-message planning and build session.
- `transcripts\7 Hermes agent\2026-10-17 5ee5079e.md`: the shorter first attempt with the task split.
- `C:\Users\enma6\Downloads\find-top-20-fast-popular-news-sources-local-ff.json`: the original Hermes export.
- `C:\Users\enma6\Desktop\hermes agent\` and `C:\Users\enma6\Desktop\Hermes_Capabilities.txt`: the Hermes app notes vault and capability list.

I could not read any of these from the cloud; the paths come from the project instructions (**unverified**). Background: the project began as one giant request to a Hermes desktop agent that kept hitting tool errors, so it was split into three tasks (news pipeline, intelligence layer, deep dive bot) built with short sequential prompts. All three now exist in this repo.

## 8. Standing rules

1. **Never ask for or repeat API keys.** Manan adds repository secrets himself.
2. **Never change the email recipient default in `digest.py`** (`RECIPIENT_EMAIL`, with the optional `RECIPIENT_EMAIL_OVERRIDE` secret). It is correct as is. This doc deliberately doesn't repeat the address.
3. **Scheduled Actions can run 5 to 60 minutes late.** Late is normal; the slot logic handles it.
4. **The `gh` CLI must be signed in as `manangoesagain`, not `Manan-J-Shah`.** A mix-up created an empty duplicate repo under `Manan-J-Shah`; Manan can delete it.
5. Free GitHub plans need a **public** repo for Pages.
6. Telegram's chat id only shows up in `getUpdates` after you message the bot.
7. Run `python -m unittest discover -s tests` before committing (offline, about 16 seconds).
8. Keep the learning loop inside its fence: only data files in `_data/_learning`.

## 9. What was verified and what wasn't

Verified: repo tree and module docstrings; both workflow files; branch relationships (`git rev-list`); PR #1 contents; `gh-pages` contents and single-commit deploy; recent Actions runs succeeded; the 389-test suite passes on `main`; model list in `llm.py`; schedule, `INTEL_HOUR = 18`, domain list.

Corrected versus the brief: the browser page, market snapshot, intelligence layer and deep dive bot are built, not pending; `gemini-2.0-flash` is no longer the primary model; no `CLAUDE.md` was tracked on `main`, so this PR adds a fresh one (an untracked `CLAUDE.md` in Jay's working copy will conflict on pull; keep whichever you prefer, or merge the two).

Not verified: which secrets are set (so whether Jev is live); the content of delivered Telegram and email messages; Windows-side files; the Jev plan's claims about the code (copied from its doc, not re-checked line by line); PR #1 review status.
