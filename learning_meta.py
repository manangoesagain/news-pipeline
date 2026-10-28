"""
Learning how to learn: a weekly, LLM-free review of the learning loop's own settings and lessons, plus the
weekly proposal of one prompt-wording experiment.

  (a) walk-forward evaluation: replay the price-checked graded calls in time order; for each candidate
      setting (shrinkage_k, lookback_days) compute the Brier score and log-loss of the probability the system
      WOULD have stated using only information available before each call (earlier, already-checked calls
      plus the historical prior). The best candidate must beat the current setting by >= 2%, and the setting
      moves at most ONE grid step per week (applied through learning_memory.settings_set).
  (b) lesson-type review: for every playbook / lesson heading, compare the hit rate of the calls it applies to
      (matched by link type / instrument words) before vs after it existed; archive it when the calls got
      worse by >= 1.5 standard errors.
  (c) experiments: close the running prompt-wording experiment when it has a verdict, and propose a new one at
      most weekly (LLM label 'LEARN-EXP', else a small list of built-in safe hints).

Writes only through learning_memory / learning_io, so only inside _data/_learning. Nothing here raises.
Switched off entirely by LEARNING_ENABLED (see learning_memory.enabled()).
"""

import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta

import learning_io
import learning_memory as lm

META_EVERY_DAYS = 7
MIN_WALK_CALLS = 100
MIN_IMPROVEMENT = 0.05          # relative Brier improvement needed to change a setting
K_GRID = (2, 4, 8, 16, 32)
LOOKBACK_GRID = (30, 60, 120)
MAX_WALK_ROWS = 600
REVIEW_MIN = 10                 # calls before and after a lesson, each
REVIEW_Z = 1.5
PROPOSE_EVERY_DAYS = 7

_STOP = {"the", "and", "for", "with", "calls", "call", "rules", "rule", "playbook", "lessons", "lesson", "notes",
         "link", "links", "type", "types", "shock", "move", "moves", "from", "that", "this", "when", "into"}


def _log(msg):
    print(f"[LEARN] {msg}")


def _day(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").toordinal()
    except ValueError:
        return None


# ---------- (a) walk-forward evaluation ----------

def _walk_rows(rows):
    """Hard rows as (made_ordinal, due_ordinal, key, hit, prior) in time order; rows without a date are skipped."""
    out = []
    for c in rows:
        made = _day(c.get("made"))
        if made is None:
            continue
        due = _day(c.get("due"))
        if due is None or due < made:
            due = made
        key = lm.norm_inst(c.get("instrument")) or lm.norm_name(c.get("link_type")) or "other"
        prior = lm._prior_of(c)
        val = {"right": 1.0, "partial": 0.5}.get(lm._result(c), 0.0)      # same values as the revert logic
        out.append((made, due, key, val, 0.5 if prior is None else prior))
    out.sort(key=lambda r: (r[0], r[1]))
    return out[-MAX_WALK_ROWS:]


def walk_forward(rows, k, lookback_days):
    """-> {'brier', 'logloss', 'n'} of the 'call is right' probability stated before each call: shrunk
    posterior (hits + k*prior) / (n + k) of the same bucket, using only calls already price-checked
    (due before this call was made) and made within `lookback_days` of it."""
    wr = _walk_rows(rows)
    sq = ll = 0.0
    for i, (made, _due, key, y, prior) in enumerate(wr):
        n = hits = 0
        for (m2, d2, k2, y2, _p) in wr[:i]:
            if k2 == key and d2 < made and made - m2 <= lookback_days:
                n += 1
                hits += y2
        p = max(0.01, min(0.99, (hits + k * prior) / (n + k)))
        sq += (p - y) ** 2
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    n = len(wr)
    return {"brier": sq / n if n else None, "logloss": ll / n if n else None, "n": n}


def _step_toward(grid, cur, target):
    """One grid step from `cur` (nearest grid index) towards `target`."""
    ci = min(range(len(grid)), key=lambda i: abs(grid[i] - cur))
    ti = grid.index(target)
    if ti == ci:
        return None
    return grid[ci + (1 if ti > ci else -1)]


def evaluate_settings(rows, settings):
    """Pick at most one single-step setting change from the walk-forward grid.
    -> {'n', 'current': brier, 'best': (k, lookback, brier), 'change': (key, old, new, brier) | None}"""
    k0, lb0 = settings["shrinkage_k"], settings["lookback_days"]
    cur = walk_forward(rows, k0, lb0)
    out = {"n": cur["n"], "current": cur["brier"], "best": None, "change": None, "logloss": cur["logloss"]}
    if cur["n"] < MIN_WALK_CALLS or cur["brier"] is None:
        return out
    grid = {(k, lb): walk_forward(rows, k, lb)["brier"] for k in K_GRID for lb in LOOKBACK_GRID}
    (bk, blb), bb = min(grid.items(), key=lambda kv: (kv[1], kv[0]))
    out["best"] = (bk, blb, bb)
    cands = []
    nk = _step_toward(K_GRID, k0, bk)
    if nk is not None:
        cands.append(("shrinkage_k", k0, nk, walk_forward(rows, nk, lb0)["brier"]))
    nl = _step_toward(LOOKBACK_GRID, lb0, blb)
    if nl is not None:
        cands.append(("lookback_days", lb0, nl, walk_forward(rows, k0, nl)["brier"]))
    if cands:
        key, old, new, brier = min(cands, key=lambda c: (c[3], c[0]))
        if brier <= cur["brier"] * (1 - MIN_IMPROVEMENT):
            out["change"] = (key, old, new, brier)
    return out


# ---------- (b) lesson-type review ----------

def _tokens(text):
    return {t for t in re.findall(r"[a-z]{3,}", str(text).lower()) if t not in _STOP}


def _applies(row, toks):
    return bool(toks & _tokens(f"{row.get('link_type') or ''} {row.get('instrument') or ''}"))


def _split_lessons(text):
    """[(heading, [lines])] sections of lessons.md (lines before the first heading get heading None)."""
    secs = [[None, []]]
    for line in (text or "").splitlines():
        if line.startswith("# "):
            secs.append([line[2:].strip(), [line]])
        else:
            secs[-1][1].append(line)
    return secs


def _drop_verdict(before, after):
    """Hit-rate comparison of two lists of hits (0/1). -> (drop, se, worse)"""
    nb, na = len(before), len(after)
    pb, pa = sum(before) / nb, sum(after) / na
    qb, qa = (sum(before) + .5) / (nb + 1), (sum(after) + .5) / (na + 1)
    se = math.sqrt(qb * (1 - qb) / nb + qa * (1 - qa) / na)
    drop = pb - pa
    return pb, pa, se, drop > 0 and drop >= REVIEW_Z * se


def review_lessons(now, rows):
    """Archive playbooks / lesson headings whose applicable calls got clearly worse after they were added.
    -> list of archived keys."""
    archived = []
    if not lm.enabled():
        return archived
    st = lm._state()
    born = st.get("born") if isinstance(st.get("born"), dict) else {}
    today = now.strftime("%Y-%m-%d")
    items = []
    for slug, (title, _body) in lm._read_playbooks().items():
        items.append((slug, "playbook", _tokens(slug + " " + title)))
    secs = _split_lessons(lm._text(lm._p("lessons.md")))
    for head, _lines in secs:
        if head:
            items.append((lm.heading_key(head), "heading", _tokens(head)))
    for key, kind, toks in items:
        b = born.get(key)
        if not toks or not isinstance(b, str) or not _day(b):
            continue
        app = [r for r in rows if _applies(r, toks)]
        before = [1 if lm._result(r) == "right" else 0 for r in app if str(r.get("made") or "")[:10] <= b]
        after = [1 if lm._result(r) == "right" else 0 for r in app if str(r.get("made") or "")[:10] > b]
        if len(before) < REVIEW_MIN or len(after) < REVIEW_MIN:
            continue
        pb, pa, _se, worse = _drop_verdict(before, after)
        if not worse:
            continue
        if _archive(key, kind, secs, today):
            archived.append(key)
            lm._changelog_add(now, f"meta: archived {key} because its calls fell from {pb:.0%} to {pa:.0%} "
                                   f"(n={len(before)}/{len(after)}) after it was added")
            lm._event(now, f"archived lesson {key}")
            born.pop(key, None)
    if archived:
        lm._state_update(born=born)
    return archived


def _archive(key, kind, secs, today):
    """Move a playbook file / lesson section into _data/_learning/archive/. -> True when done."""
    name = f"{key}-{today}"
    if not learning_io.safe_slug(name[:40]):
        name = f"lesson-{today}"
    if kind == "playbook":
        title, body = lm._read_playbooks().get(key, ("", ""))
        if not body:
            return False
        learning_io.safe_write_text(lm._p("archive", name[:40] + ".md"), lm._fmt_playbook(title, body))
        lm._remove(lm._p("playbooks", key + ".md"))
        return True
    keep, gone = [], []
    for head, lines in secs:
        (gone if head and lm.heading_key(head) == key else keep).extend(lines)
    if not gone:
        return False
    learning_io.safe_write_text(lm._p("archive", name[:40] + ".md"), "\n".join(gone) + "\n")
    rest = "\n".join(keep).strip()
    learning_io.safe_write_text(lm._p("lessons.md"), (rest + "\n") if rest else "")
    return True


# ---------- (c) experiment proposal ----------

def _proposal_prompt(kind, used):
    types_ = {k: v for k, v in lm.HINT_TYPES.items() if v not in used}
    return (
        "Pick ONE wording hint type to A/B test on a news-analysis prompt. Choose only from this closed list "
        "(the hint text is fixed by the type; you cannot write your own):\n"
        f"{json.dumps(types_)[:900]}\n"
        "Return ONE JSON object: {\"type\": \"<one of the type names above>\"}."
    )


def propose_experiment(now, llm_module=None):
    """Start one wording experiment if none is running and none was proposed in the last 7 days. -> the new id."""
    if not lm.enabled():
        return None
    st = lm._state()
    last = _day(st.get("exp_last_proposed"))
    if last is not None and now.toordinal() - last < PROPOSE_EVERY_DAYS:
        return None
    sc = lm._record()
    if not sc.get("enough_data") or lm._frozen(st, now):
        return None
    if lm.experiment_running():
        return None
    e = lm._exp_load()
    used = {h["text"] for h in e["history"]} | {a["text"] for a in e["adopted"]}
    kind = lm.EXP_PROPOSE_KINDS[0]                                # only 'cards' is wired into digest.py
    text = None
    if llm_module is not None:
        try:
            out = llm_module.generate_json(_proposal_prompt(kind, used), label="LEARN-EXP", require="type",
                                           max_tokens=600, temperature=0.6, budget_s=60, per_model_s=40)
            raw = (lm.hint_for_type(out.get("type")) or out.get("text")) if isinstance(out, dict) else None
            cand = lm.validate_hint(raw, judge=True)             # allowlist: only a template text can pass
            text = cand if cand and cand not in used else None
        except Exception as ex:
            lm._log(f"experiment proposal failed: {type(ex).__name__}: {ex}")
    if text is None:
        free = [t for t in lm.BUILTIN_HINTS if t not in used and lm.validate_hint(t)]
        text = free[now.toordinal() % len(free)] if free else None
    if text is None:
        return None
    with lm._LOCK:
        e = lm._exp_load()
        if e["running"]:
            return None
        eid = "exp-%s-%s" % (now.strftime("%Y%m%d"), hashlib.sha256(text.encode("utf-8")).hexdigest()[:4])
        e["running"] = {"id": eid, "kind": kind, "text": text, "share": lm.EXP_SHARE,
                        "started": now.strftime("%Y-%m-%d"), "n_control": 0, "hits_control": 0,
                        "n_variant": 0, "hits_variant": 0, "units_control": [], "units_variant": [], "seen": []}
        lm._exp_save(e)
        lm._state_update(exp_last_proposed=now.strftime("%Y-%m-%d"))
        lm._changelog_add(now, f"experiment {eid} started on {kind} prompts for {round(lm.EXP_SHARE * 100)}% of calls")
        lm._event(now, f"experiment {eid} started")
    return eid


# ---------- entry point ----------

def run_meta(now=None, llm_module=None):
    """-> {'ran': bool (weekly review done), 'settings': [changes], 'archived': [keys], 'experiment': str|None}
    Every call closes a finished experiment and may start a new one; the weekly review runs at most every
    7 days and needs >= 30 price-checked calls. Never raises."""
    res = {"ran": False, "settings": [], "archived": [], "experiment": None}
    try:
        if not lm.enabled():
            lm._off_log()
            return res
        now = now or datetime.now()
        verdict = lm.experiment_decide(now)
        if verdict:
            res["experiment"] = verdict
        st = lm._state()
        if not lm._frozen(st, now):
            last = _day(st.get("meta_last"))
            if last is None or now.toordinal() - last >= META_EVERY_DAYS:
                _weekly(now, res)
            started = propose_experiment(now, llm_module)
            if started:
                res["experiment"] = "started:" + started
    except Exception as e:
        lm._log(f"meta failed: {type(e).__name__}: {e}")
    return res


def _weekly(now, res):
    if not lm.enabled():
        return
    S = lm.settings_get()
    rows = lm._hard_rows(lm._graded(1000), now, max(lm.SETTING_BOUNDS["lookback_days"][1], 180))
    ev = evaluate_settings(rows, S)
    if ev["n"] < MIN_WALK_CALLS:
        return
    sc = lm._record()
    with lm._LOCK:
        if not lm._snapshots():                       # ONE baseline before the first change of this run
            lm._snapshot(now - timedelta(minutes=1), "baseline", sc)
        changed = False
        if ev["change"]:
            key, old, new, brier = ev["change"]
            why = (f"meta: {key} {old}->{new} because walk-forward Brier {ev['current']:.3f}->{brier:.3f} "
                   f"on {ev['n']} calls")
            applied = lm.settings_set({key: new}, why=why, now=now)
            if applied.get(key) == new:
                res["settings"].append((key, old, new))
                changed = True
        else:
            b = ev["best"]
            lm._changelog_add(now, f"meta: settings kept (shrinkage_k {S['shrinkage_k']}, lookback_days "
                                   f"{S['lookback_days']}); walk-forward Brier {ev['current']:.3f} on {ev['n']} calls"
                                   + (f", best grid point {b[2]:.3f}" if b else ""))
        archived = review_lessons(now, rows)
        if archived:
            res["archived"] = archived
            changed = True
        if changed:                                   # ONE change snapshot after all of the run's changes
            lm._snapshot(now, "change", sc)
        lm._state_update(meta_last=now.strftime("%Y-%m-%d"))
    res["ran"] = True
