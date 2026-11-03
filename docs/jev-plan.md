# Jev decision layer and learning loop: design plan

As of 2 November 2026. Source of truth is the Claude doc; this is a copy for the repo.

**Status:** design approved in outline. Budget ceiling $3-4 a month (estimated actual: under $1).
Telegram thumbs up/down buttons are paused by Jay's decision, to be picked up later.
Phase 0 (the fix-first list) is written and tested as a pending PR; the rest is not built.

Jev belongs in this pipeline, but not as a graded learning loop over editorial judgment. A review round against the code killed the grading signal the first draft rested on: "did coverage of this story grow over the next 24 to 48 hours" is close to what `rank_heuristic` already computes for free, so a loop trained on it would teach Jev to imitate a heuristic the repo already has. What survives is narrower and more useful: Jev as a removal-only veto across many cheap calls, graded only where genuinely external evidence exists, with one tunable threshold instead of twenty. Read against `manangoesagain/news-pipeline` at commit f275221.

## What Jev is

TypeSafe AI's "Jev" is a decision-only model, launched September 2026. It does not generate text. You POST a `state` plus typed questions to `https://api.typesafe.ai/v1/systemone` and get typed answers back with probabilities.

- **Question types:** `choice` (pick one of a map of options, returns probabilities and a confidence), `score` (a position on an ordered rubric of 2-10 levels), `noul` (a yes/no probability from 0 to 1). All questions in one call are evaluated independently against the same state.
- **Price:** $0.042 per 1M input tokens. Output and cached input are free.
- **Latency:** 70-500 ms typical. Rate limit around 80 requests a second.
- **Context:** 32k-64k tokens for state plus questions. Current model `jev-1.13`, alias `jev-latest`.
- **It guarantees a schema-valid answer, not a correct one.** Documented weak spots: literal reading of questions, arithmetic and counting, date comparison, multi-hop indirection, large or noisy state, adversarial or injected content.
- **Auth:** `Authorization: Bearer $TYPESAFE_API_KEY`. The key is already a repo secret.

## What the repo does today

- **Flow:** `main()` fetches RSS, Google News, GNews and NewsData, runs `build_clusters` (keyword domain, title-token overlap of 0.6 for dedupe), sends the top 160 clusters to Gemini in `rank_with_llm` (merge, domain, importance 1-10, breaking), then `select_digest` takes the top 5 per domain. The 18:00 run adds the intelligence brief and the connection graph.
- **Jev now (`jev_judge.py`):** yes/no questions only, strictly serial. Three uses: `verify_links` on graph edges, `grade_soft` on open predictions, `is_safe_lesson` on learning text. The existing phases already claim 81 of the 120 requests allowed per process, and the budget is counted as summed request duration, not wall clock.
- **Learning loop:** `learning_ledger` grades price calls against real prices and a momentum baseline. `learning_meta` retunes weekly with walk-forward replay, needs 100 rows and a 5% Brier gain, and moves one grid step. `learning_memory` holds weights, lessons, snapshots and auto-revert. Two constraints matter here: settings are coerced to integers and clamped to bounds, and `learning_io` refuses to write a data file over 400 KB (it raises, it does not truncate).
- **It is all public.** The workflow copies `_data` into `site/` and deploys that to gh-pages, so anything written under `_data/_learning` is served on the web page.
- **Gaps:** the four daily digests get no Jev checks at all, and no Jev answer is stored, so nobody can tell whether it is right.

## What the review round found

1. **The ground truth was circular.** Coverage growth is measured over 21 fixed feeds and 12 fixed Google queries, capped at 20 entries each. It is nearly the same quantity as `2.0 * min(n_sources, 5) + recency + region`, which `rank_heuristic` computes at zero cost. Agreement with Gemini has the same defect.
2. **A filter before clustering would blind the loop permanently.** `update_article_store` writes every item of every cluster into the 7-day store, and that store is the corpus the grading reads from. Items Jev dropped would leave no record, so false negatives become unmeasurable after the shadow week ends.
3. **Three stages were promotion paths dressed as filters.** Merging raises a cluster's source count, which raises score in both rankers and gates breaking at 3+ sources. An importance vote raises score directly. A domain relabel moves a story into a 5-slot queue.
4. **One stage would have fired on correct output.** The claim-support check compares a headline from one outlet with a summary from another, which is the designed behaviour.
5. **The new stages would have weakened the one existing Jev fence.** `_blocked` checks the global counters first, so stages earlier in the run exhaust the 120-request budget and make `is_safe_lesson` fail open on every run.
6. **Two latent bugs, independent of this plan.** See "Fix these first".
7. **The statistics could not carry the plan.** Twenty-odd free parameters, tuned weekly, on a few hundred correlated labels, with no holdout and no alpha spending.

## Principles

1. **Jev may only subtract.** It can veto, drop, or cap a score downward. It may never raise a score, add a source to a cluster, create a merge, or move a story into a domain slot it did not already hold.
2. **Mark, never remove.** A stage writes a probability onto the item; the threshold is applied later, where candidates are chosen. Dropped items still reach the 7-day store, so they still leave a record to grade.
3. **Keep a permanent 10% random bypass, on the junk veto only.** Without it, the shadow week is the last unbiased data the loop ever sees. The bypass must not apply to the injection screen: a 10% hole in a security check is a security hole.
4. **Seconds are the budget, not dollars.** Anything before the Gemini call spends Gemini's own wall clock.
5. **Fail open, and reserve the existing fence's allowance before any new stage can spend it.**
6. **Pin the model, and never pool statistics across model versions.** A silent alias move would look exactly like a threshold effect.
7. **No arithmetic and no date questions.** Age in hours is computed in code.

## Where Jev goes

| Stage | What Jev is asked | What it may do | Graded by |
| --- | --- | --- | --- |
| A. Junk veto | Is this a news event with real-world consequences, not lifestyle, sport, opinion or a product promo? Asked once per cluster, in its own small state, and only for clusters near the selection boundary | Writes `junk_p`. The threshold is applied where the Gemini candidate list is built and in `select_digest`. Removal only | A one-time gold set, plus the permanent 10% bypass |
| B. Merge veto | Do these two headlines describe the same event? Asked only about merges Gemini proposed | Splits a merge Gemini made, never creates one. The split-off ids must fall through to the existing heuristic backfill, where scores are divided by 10, and must never become a story of their own | Duplicates that show up in the same digest |
| C. Injection screen | Does this text try to give instructions or commands to an AI or a reader? Asked per item, before the headline ever reaches the Gemini prompt | Drops the item from the prompt lines. Never unblocks what code already flagged | Planted cases in the offline tests |
| D. The three existing checks | Graph links, soft predictions, lesson safety | Unchanged, but now logged, and their budget is reserved before any new stage can spend it | Soft grades compared against later hard price grades |

**Consistency probes.** Ask each question a second time in inverted wording, and shuffle the order of items in the batch. If `p` and `p_inverted` do not roughly sum to 1, the answer is unreliable. The disagreement rate per stage is a quality number available in the same run, with no outcome to wait for and no labels needed.

## What is cut, and why

- **Domain classification.** A promotion path, a crash path, and it competes with a cheap keyword scorer nobody has complained about.
- **Claim support.** Fires on correct output by construction, and Jev cannot generate text, so the only remedy is deleting something that was fine.
- **The pre-send gate.** It sees the first 1,000 characters of a 4,000-plus character message, and by then the untrusted headline has already been through four Gemini prompts. The injection check belongs before the first prompt, which is stage C.
- **Importance as a second vote.** It can only raise a score, so it breaks the subtract-only rule.
- **Auto-tuned rubric wording, per-source weights, and a calibration map.** Re-wording a rubric invalidates every probability logged under the old wording.
- **Cost monitoring.** Jev costs cents here. Monitoring seconds is what matters.
- **Win-rate against Gemini on disagreements.** It measures imitation of a free heuristic.

## Fix these first, before any Jev stage

1. **Harden `select_digest`.** It indexes `domain_items[s["domain"]]` with a bare key. One unexpected domain string raises inside `main()`, which means no digest, no delivery, no deploy, and the loss of that slot's article store and learning writes.
2. **Do not key anything by a bare md5.** `learning_io` redacts dict keys as well as values and eats any 32-character hex run. Use a prefixed 16-character id.
3. **Pin the model.** Set `JEV_MODEL` to an exact version in the workflow.
4. **Reserve the lessons budget.** Otherwise new stages make the existing lesson safety check fail open on every run.
5. **Guard the wall clock.** Give every pre-rank stage the `llm.HARD_STOP` check plus a hard 20-30 second cap.
6. **Make `jev_judge` parallel.** A pool of eight turns 80 requests from over 20 seconds into about three.

Items 1 and 2 are covered by the pending PR "Stop an unknown domain from sinking a whole digest run; guard two more latent failures" (one fix in `digest.py` plus `tests/test_config_guards.py`, 399 tests passing). Items 3 to 6 are the second PR, not yet written.

## The ledger

A new `jev_ledger.py`, storing sufficient statistics, not rows. `learning_io` raises rather than truncating above 400 KB, and at roughly 1,200 decisions a day raw rows would hit that inside a week.

| What is stored | Why |
| --- | --- |
| 20 probability buckets per stage, rubric version and model id: n, sum of p, sum of p squared, hits | Enough for Brier, log loss, a calibration table and a threshold sweep, with no free text |
| Per-run, per-stage counters: asked, answered, blocked, timed out, HTTP failed | Tells a quiet Jev apart from an exhausted budget, and gates tuning when answer coverage drops below 80% |
| Consistency counters: pairs asked, pairs that disagreed | The reliability number that needs no outcome and no waiting |
| A bounded sample of recent disagreements: slot date, stage, 16-character link id, p, what the incumbent said, what the code did | Inspection only, capped well under the file limit |
| A write-once key per decision: slot date, stage, link id | The same story appears at 8:00, 11:00, 15:00 and 18:00, and reruns repeat a slot. It must count once |

## What can honestly be graded

- **Genuinely external:** real market prices, through the ledger that already exists. Planted injection cases in the offline tests are the other.
- **Semi-external:** a soft prediction written today, graded against headlines that did not exist when it was written.
- **Free and immediate:** the consistency probes.
- **Circular, and not to be used as a target:** coverage growth, agreement with Gemini, re-selection in a later digest.
- **Missing: a reader signal.** Without one, the loop can learn calibration and reliability but not editorial taste. The proposed cheapest fix is a one-time gold set: 50 past stories labelled junk or not junk, picked after the shadow week so they straddle the decision boundary. Still an open question.

## Metrics

- Per stage, from the buckets: Brier score, log loss, and a 20-bucket calibration table.
- Consistency: the share of items where a question and its inverse disagree.
- Coverage: asked, answered, blocked and timed out, per stage, per run. A stage under 80% answered is not eligible for tuning.
- Gold-set accuracy, if a gold set is made.
- One top-line number rather than six: the junk rate of what actually got delivered.

## What the loop adjusts

One knob, not twenty, through the existing bounded settings mechanism.

- **One integer threshold** for the junk veto, stored as a percentage because settings are coerced to integers. Add it to both `DEFAULT_SETTINGS` and `SETTING_BOUNDS` in the same change.
- **Monthly, not weekly**, with walk-forward replay, at least 150 gradeable rows, a 5% relative Brier gain, and one grid step at a time.
- **Its own revert trigger.** The existing auto-revert is keyed to graded price calls, so it cannot see a bad junk threshold.
- **Refuse to tune after a data gap.** A bucket count that falls by more than 30% week on week is what a failed gh-pages restore looks like.
- **Nothing else.** No rubric wording, no per-source weights, no calibration map.

## Rollout

| Phase | What happens | Risk to the digest |
| --- | --- | --- |
| 0. Fix first | The six items above. Nothing Jev-related changes behaviour | None, and it closes two latent bugs |
| 1. Shadow, live | Stages A to C ask and log on the real scheduled runs, acting on nothing. Not through `dry_run`: that path never deploys, so its ledger would be thrown away | None |
| 2. Gold set, optional | 50 labels, once | None |
| 3. Act on the junk veto | Threshold hand-set from the shadow data. The 10% bypass is on permanently from here | Low. One setting reverses it |
| 4. Act on the merge veto and injection screen | Both removal-only | Low |
| 5. Tune, monthly | One threshold, walk-forward replay, its own revert trigger | Low, bounded steps |

## Cost and limits

- **Cost is not the constraint.** Batching ten questions into one call amortises the state, so a whole run is roughly 100,000 input tokens: about $0.004 a run, $0.02 a day, under $1 a month. The approved ceiling is $3-4 a month.
- **Seconds are the constraint.** The job times out at 30 minutes, the shared LLM wall clock is 1,380 seconds from process start, and the 18:00 run already budgets 300 seconds for the brief, 420 for the graph and 240 for the lite graph.
- **Requests are the other constraint.** The existing phases claim 81 of the 120 allowed per process.
- **Keep the state small.** One question about one cluster, in its own small state, avoids Jev's three main weak spots at once.
- **There is no history to recover from.** All state lives on a gh-pages branch rewritten as a single commit each run.

## Files this would touch

- `jev_judge.py`: parallel requests, model pinning, reserved budgets, a logging hook. The offline tests assert the current wire format, so this is a real diff.
- `jev_ledger.py` (new): buckets, write-once keys, coverage counters, metrics.
- `digest.py`: `junk_p` written in `build_clusters`, thresholds applied at prompt-build and selection time, the merge veto inside `rank_with_llm`, the injection screen before the prompt lines are built, and the `select_digest` hardening.
- `learning_memory.py`: one new setting, added to the defaults and the bounds in the same change, plus the Jev revert trigger.
- `learning_meta.py`: the monthly threshold step, through the existing walk-forward replay.
- `digest.yml`: a pinned `JEV_MODEL`, and a `JEV_MODE` of off, shadow or act.
- `tests/`: stubbed Jev stages, a parity test for the settings keys, and a round-trip test that a story key survives the redacting writer.

## Open questions

- **A one-time gold set?** 50 stories labelled junk or not junk, after the shadow week. It is the only real editorial ground truth on offer; without it the loop tunes on proxies. Undecided.
- **Should the learning folder stay public?** Everything under `_data/_learning` is deployed to the web page today. Note that excluding it from `site/` would delete it, because the deploy uses `force_orphan`. The cheap answer is to keep it published and let the ledger carry no free text at all. Undecided.

## Decided

- Shadow-only first week: yes.
- Budget ceiling: $3-4 a month.
- Telegram thumbs up/down buttons: paused for now, to be revisited later.
- Phase 0 as its own PR, split in two: yes.
