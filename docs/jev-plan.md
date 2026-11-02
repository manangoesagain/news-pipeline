# Jev decision layer and learning loop: design plan

**Status: design only. No code for this plan is written.** Copied (condensed, nothing added) on 2026-11-02 from the Claude Doc "Jev decision layer and learning loop: design plan" (<https://claude.ai/artifact/6xyVr22DhYxUwWrVqwGEJP>), which was written against this repo at commit `f275221`. That doc went through three review rounds; its claims about the code were not re-verified line by line when copying here. If the doc and this file differ, the doc is newer.

## What Jev is

A "decision" model from TypeSafe AI (launched Sep 2026). It writes no text: you send state plus typed questions (choice, score, yes/no probability) and get typed answers with probabilities. It guarantees valid output, not correct output, is weak at arithmetic, and headlines fed to it are untrusted text that can steer it. API: `POST https://api.typesafe.ai/v1/systemone`, bearer auth, secret `TYPESAFE_API_KEY`. Sign-ups were reported paused in Sep 2026; unconfirmed whether they reopened.

## What the repo does today

- `jev_judge.py` is yes/no only and strictly serial. Three uses: `verify_links` (graph edges), `grade_soft` (open soft predictions), `is_safe_lesson` (learning text). Existing phases already claim 81 of the 120 requests allowed per process; the time budget counts summed request duration, not wall clock.
- The four daily digests get **no** Jev checks, and no Jev answer is stored, so nobody can tell whether it is right.
- Everything under `_data/_learning` is published on the web page (the workflow copies `_data` into `site/`).

## Principles

1. **Jev may only subtract.** Veto, drop or cap a score downward. Never raise a score, add a source to a cluster, create a merge, or move a story into a domain slot.
2. **Mark, never remove.** A stage writes a probability onto the item; the threshold is applied later. Dropped items must still reach the 7-day store so there is something to grade.
3. **Permanent 10% random bypass on the junk veto only** (not on the injection screen), recording the answer it would have acted on.
4. **Seconds are the budget, not dollars.** Anything before the Gemini call spends Gemini's wall clock (1,380 s from process start).
5. **Fail open, and reserve the lessons-safety allowance** before any new stage can spend the request cap.
6. **Pin the model**; never pool statistics across model versions.
7. **No arithmetic or date questions** for Jev; compute in code.

## Stages

| Stage | Asks | May do | Graded by |
|---|---|---|---|
| A. Junk veto | Is this a news event with real-world consequences (not lifestyle, sport, opinion, promo)? Once per cluster, near the selection boundary | Writes `junk_p`; removal only | One-time gold set plus the 10% bypass |
| B. Merge veto | Do these two headlines describe the same event? Only for merges Gemini proposed | Splits a Gemini merge; split-off ids fall to heuristic backfill (scores divided by 10), never become their own story | Duplicates appearing in the same digest |
| C. Injection screen | Does this text try to instruct an AI or reader? Per item, before the Gemini prompt | Drops the item from the prompt lines; never unblocks what code flagged | Planted cases in offline tests |
| D. Existing checks | Graph links, soft predictions, lesson safety | Unchanged but logged, budget reserved | Soft grades vs later price grades |

**Consistency probes:** ask each question again in inverted wording with shuffled batch order; if `p` and `p_inverted` don't roughly sum to 1 the answer is unreliable. Gives a per-stage quality number in the same run.

**Cut from the first draft:** domain classification, claim support, pre-send gate, importance as a second vote, auto-tuned rubric wording / per-source weights / calibration map, cost monitoring, win-rate against Gemini.

## Fix these first (Phase 0)

1. Harden `select_digest` against an unknown domain. **Done in PR #1.**
2. Never key anything by a bare md5 (`learning_io` redacts 32+ hex characters, including in dict keys); use a prefixed 16-character id. **Guarded by a test in PR #1.**
3. Pin `JEV_MODEL` in the workflow (today it is the first entry of `/v1/models`, an alias).
4. Reserve the lessons budget.
5. Guard the wall clock: `llm.HARD_STOP` check plus a hard 20 to 30 s cap on pre-rank stages.
6. Make `jev_judge` parallel (a pool of eight turns 80 requests from over 20 s into about 3 s).

Items 3 to 6 are the planned "PR 2: Jev plumbing", not started.

## Ledger

New `jev_ledger.py` storing sufficient statistics, not rows (`learning_io` raises above 400 KB): 20 probability buckets per stage / rubric version / model id; per-run per-stage counters (asked, answered, blocked, timed out, HTTP failed); consistency counters; a bounded sample of recent disagreements; a write-once key per decision (slot date, stage, link id) so the same story at 8, 11, 15 and 18 counts once. No headline text.

## Grading

- Genuinely external: real market prices (existing ledger) and planted injection cases.
- Semi-external: soft predictions graded against later headlines.
- Immediate: consistency probes.
- **Circular, never optimise against:** coverage growth, agreement with Gemini, re-selection in later digests.
- Missing: a reader signal. Cheapest fix is Jay labelling 50 past stories junk or not, once, ideally 50 that straddle Jev's decision boundary chosen after the shadow week.

## What the loop adjusts

One integer threshold for the junk veto (stored as a percentage), monthly not weekly, walk-forward replay, at least 150 gradeable rows, 5% relative Brier gain, one grid step at a time. Add it to **both** `DEFAULT_SETTINGS` and `SETTING_BOUNDS` (PR #1 adds a test for this). Own revert trigger on the delivered junk rate. Refuse to tune after a data gap (bucket counts down more than 30% week on week).

## Rollout

0. Fix first. 1. Shadow, live: stages A to C ask and log on real scheduled runs and act on nothing (not through `dry_run`, which never deploys). First week is shadow-only. 2. Optional gold set. 3. Act on the junk veto. 4. Act on merge veto and injection screen. 5. Tune monthly.

## Cost and limits

Roughly 100,000 input tokens a run: about $0.004 a run, $0.02 a day, under $1 a month. Approved ceiling: $3 to $4 a month. The earlier $3-30 estimate assumed one call per item and was wrong. Requests (120 per process) and seconds are the real limits. Keep each state small: one question about one cluster.

## Files it would touch

`jev_judge.py` (parallel, pinning, reserved budgets, logging hook; offline tests assert the current wire format), new `jev_ledger.py`, `digest.py`, `learning_memory.py` (one setting plus revert trigger), `learning_meta.py` (monthly step), `.github/workflows/digest.yml` (`JEV_MODEL`, `JEV_MODE` of off / shadow / act), `tests/`.

## Decisions and open questions

Decided by Jay: shadow-only first week; $3 to $4 a month; Telegram thumbs up/down buttons paused for later.

Open:
1. Will Jay label the 50-story gold set once? The plan's author recommends doing it after the shadow week.
2. Should `_data/_learning` stay public? Excluding it from the site is not simple because deploys use `force_orphan`: anything not copied into `site/` is deleted next run. Recommended: keep it published and make the ledger carry no headline text; a private `data` branch (about 20 workflow lines plus race risk) is the alternative.
