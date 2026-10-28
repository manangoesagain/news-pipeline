"""
Optional TypeSafe (Jev) judge: small yes/no probability checks used as an add-on to the digest.

Does NOTHING unless the environment variable TYPESAFE_API_KEY (or JEV_API_KEY) is set. Every public
function is best-effort and never raises: on any problem it returns None / an empty result and prints a
single "[JEV] ..." line that contains no secret.

Safety properties
- The key is read from the environment on each call, only ever placed in the Authorization header,
  never printed, stored, cached or put into an exception/log message.
- Network only to https://api.typesafe.ai (fixed URL, redirects not followed).
- 15 s timeout per request, a global wall-clock budget (JEV_BUDGET_S, default 90 s per process) and at
  most 60 requests per process.
- Every string sent is truncated (headline 200 chars, at most 12 headlines per state) and passed through
  learning_io.redact when importable (emails, key-like strings); the key itself is also scrubbed.

Contract used (docs.typesafe.ai/api.md): POST /v1/systemone
  {"model", "state", "questions": {name: {"type": "noul", "instructions": ...}}}
  -> {"answers": {name: {"type": "noul", "noul": p_yes_0_to_1}}}
GET /v1/models lists models (response shape is not documented; parsed defensively).
"""

import os
import time

try:
    import requests
except Exception:  # pragma: no cover - requests is in requirements.txt
    requests = None

try:
    from learning_io import redact as _redact
except Exception:
    _redact = None

BASE_URL = "https://api.typesafe.ai"
TIMEOUT_S = 15
DEFAULT_BUDGET_S = 90.0
MAX_REQUESTS = 60
HEADLINE_CHARS = 200
MAX_HEADLINES = 12
TEXT_CHARS = 1000
BATCH = 10

GLOBAL_MAX_REQUESTS = 120
GLOBAL_BUDGET_S = 150.0
PHASES = {"links": (60.0, 40), "soft": (30.0, 15), "lessons": (20.0, 25)}

_state = {}


def reset():
    """Reset all budgets/counters/model cache (used by tests)."""
    _state.clear()
    _state.update({"model": None, "g_spent": 0.0, "g_reqs": 0, "warned": set()})
    _set_phase("default", None, None)


def _set_phase(name, budget_s, max_requests):
    _state.update({"phase": name, "p_spent": 0.0, "p_reqs": 0,
                   "p_budget": budget_s,
                   "p_max": max_requests if max_requests is not None else MAX_REQUESTS})


def begin_phase(name, budget_s=None, max_requests=None):
    """Start a phase with its own allowance (time actually spent waiting on Jev + request count).
    Known phases: links (60 s, 40 req), soft (30 s, 15 req), lessons (20 s, 25 req). Never raises."""
    try:
        name = str(name)
        d_b, d_r = PHASES.get(name, (None, None))
        _state["warned"].discard(name)
        _set_phase(name, float(budget_s) if budget_s is not None else d_b,
                   int(max_requests) if max_requests is not None else d_r)
    except Exception as e:
        _log("begin_phase failed: %s" % type(e).__name__)


def _key():
    for name in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def available() -> bool:
    return bool(_key()) and requests is not None


def _log(msg):
    print("[JEV] " + str(msg)[:160])


def _env_budget():
    try:
        return max(1.0, float(os.environ.get("JEV_BUDGET_S") or DEFAULT_BUDGET_S))
    except ValueError:
        return DEFAULT_BUDGET_S


def _pb():
    return _state["p_budget"] if _state["p_budget"] is not None else _env_budget()


def _remaining():
    """Seconds left in the current phase (summed request durations, not wall-clock)."""
    return min(_pb() - _state["p_spent"], GLOBAL_BUDGET_S - _state["g_spent"])


def _blocked():
    """None if a request may be made, else a reason. Logs ONE 'budget used up' line per phase."""
    if _state["g_reqs"] >= GLOBAL_MAX_REQUESTS or GLOBAL_BUDGET_S - _state["g_spent"] <= 0.5:
        why = "process"
    elif _state["p_reqs"] >= _state["p_max"] or _pb() - _state["p_spent"] <= 0.5:
        why = _state["phase"]
    else:
        return None
    if why not in _state["warned"] and _state["phase"] not in _state["warned"]:
        _state["warned"].add(why)
        _state["warned"].add(_state["phase"])
        _log("budget used up for %s" % why)
    return why


reset()


def _clean(s, limit=HEADLINE_CHARS):
    s = " ".join(str(s if s is not None else "").split())
    if _redact:
        try:
            s = _redact(s)
        except Exception:
            pass
    k = _key()
    if k:
        s = s.replace(k, "[secret removed]")
    return s[:limit]


def _http(method, path, payload=None):
    """One guarded HTTP call. Returns parsed JSON or None. Never raises, never leaks the key."""
    key = _key()
    if not key or requests is None:
        return None
    if _blocked():
        return None
    left = _remaining()
    _state["p_reqs"] += 1
    _state["g_reqs"] += 1
    t0 = time.monotonic()
    try:
        kw = dict(headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                  timeout=min(TIMEOUT_S, left), allow_redirects=False)
        if method == "POST":
            r = requests.post(BASE_URL + path, json=payload, **kw)
        else:
            r = requests.get(BASE_URL + path, **kw)
        if getattr(r, "status_code", 0) != 200:
            _log("%s %s failed: HTTP %s" % (method, path, getattr(r, "status_code", "?")))
            return None
        return r.json()
    except Exception as e:  # message deliberately not included
        _log("%s %s failed: %s" % (method, path, type(e).__name__))
        return None
    finally:
        dt = max(0.0, time.monotonic() - t0)
        _state["p_spent"] += dt
        _state["g_spent"] += dt


def _model():
    env = (os.environ.get("JEV_MODEL") or "").strip()
    if env:
        return env
    if _state["model"]:
        return _state["model"]
    data = _http("GET", "/v1/models")
    items = data
    if isinstance(data, dict):
        items = data.get("data") or data.get("models") or []
    name = None
    if isinstance(items, list):
        for it in items:
            if isinstance(it, str) and it.strip():
                name = it.strip()
            elif isinstance(it, dict):
                v = it.get("id") or it.get("name") or it.get("model")
                if isinstance(v, str) and v.strip():
                    name = v.strip()
            if name:
                break
    if not name:
        _log("no model available")
        return None
    _state["model"] = name
    return name


def _ask(state, questions):
    """questions: {name: instructions}. Returns {name: p} (only valid answers) or None."""
    if not available() or not questions:
        return None
    model = _model()
    if not model:
        return None
    body = {"model": model, "state": state,
            "questions": {n: {"type": "noul", "instructions": ins} for n, ins in questions.items()}}
    data = _http("POST", "/v1/systemone", body)
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        return None
    out = {}
    for n in questions:
        a = data["answers"].get(n)
        try:
            p = float(a["noul"])
        except (TypeError, KeyError, ValueError):
            continue
        if 0.0 <= p <= 1.0:
            out[n] = p
    return out


def _headlines(items):
    out, seen = [], set()
    for h in items or []:
        c = _clean(h)
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
        if len(out) >= MAX_HEADLINES:
            break
    return out


def _node_texts(node):
    texts = []
    for s in (node.get("sources") or []):
        if isinstance(s, dict) and s.get("title"):
            texts.append(s["title"])
    if node.get("detail"):
        texts.append(node["detail"])
    return texts


def _interleave(lists):
    out, i = [], 0
    while any(i < len(l) for l in lists):
        for l in lists:
            if i < len(l):
                out.append(l[i])
        i += 1
    return out


# ---------------------------------------------------------------- (1) link verification
def verify_links(graph, headlines=None, max_links=60) -> dict:
    """{edge_index: p_supported} for causal edges (contradicts/feedback are not checked)."""
    try:
        if not available() or not isinstance(graph, dict):
            return {}
        nodes = {n.get("id"): n for n in graph.get("nodes", []) if isinstance(n, dict)}
        todo = []
        for i, e in enumerate(graph.get("edges", [])):
            if not isinstance(e, dict) or e.get("kind") in ("contradicts", "feedback"):
                continue
            a, b = nodes.get(e.get("from")), nodes.get(e.get("to"))
            if a and b:
                todo.append((i, e, a, b))
            if len(todo) >= max(0, int(max_links)):
                break
        result = {}
        for s in range(0, len(todo), BATCH):
            chunk = todo[s:s + BATCH]
            texts = _interleave([_node_texts(a) + _node_texts(b) for _, _, a, b in chunk])
            hl = _headlines(texts) or _headlines(headlines)
            if not hl:
                continue
            links, qs = [], {}
            for i, e, a, b in chunk:
                fl, tl, mech = _clean(a.get("label")), _clean(b.get("label")), _clean(e.get("label"), 80)
                links.append({"id": "e%d" % i, "from": fl, "to": tl, "mechanism": mech})
                qs["e%d" % i] = ("Do the headlines support that %s causes the stated effect on %s via %s?"
                                 % (fl, tl, mech))
            ans = _ask({"headlines": hl, "link": links}, qs)
            if ans is None:
                if _blocked():
                    break
                continue
            for i, _, _, _ in chunk:
                if "e%d" % i in ans:
                    result[i] = ans["e%d" % i]
        return result
    except Exception as e:
        _log("verify_links failed: %s" % type(e).__name__)
        return {}


def drop_unsupported(graph, probs, threshold=0.25, keep_min_edges=12):
    """-> (graph_copy, dropped). Drops edges with p < threshold, lowest first, never below
    keep_min_edges, never 'contradicts' or 'feedback' edges. Nodes left without edges are removed."""
    try:
        edges = list(graph.get("edges", []))
        cand = []
        for k, p in (probs or {}).items():
            try:
                i, p = int(k), float(p)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(edges) and p < threshold and isinstance(edges[i], dict) \
                    and edges[i].get("kind") not in ("contradicts", "feedback"):
                cand.append((p, i))
        cand.sort()
        drop = set()
        for p, i in cand:
            if len(edges) - len(drop) - 1 < keep_min_edges:
                break
            drop.add(i)
        new = dict(graph)
        new["edges"] = [dict(e) if isinstance(e, dict) else e for j, e in enumerate(edges) if j not in drop]
        if drop:
            used = {x for e in new["edges"] if isinstance(e, dict) for x in (e.get("from"), e.get("to"))}
            new["nodes"] = [n for n in graph.get("nodes", []) if not isinstance(n, dict) or n.get("id") in used]
        else:
            new["nodes"] = list(graph.get("nodes", []))
        pm = {i: p for p, i in cand}
        dropped = [{"index": i, "from": edges[i].get("from"), "to": edges[i].get("to"),
                    "label": edges[i].get("label"), "p": round(pm[i], 3)} for i in sorted(drop)]
        return new, dropped
    except Exception as e:
        _log("drop_unsupported failed: %s" % type(e).__name__)
        return graph, []


# ---------------------------------------------------------------- (2) soft grading
def classify(p) -> str:
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "unclear"
    if p >= 0.7:
        return "right"
    if p <= 0.3:
        return "wrong"
    return "unclear"


def grade_soft(predictions, headlines) -> dict:
    """predictions: [{'id', 'if', 'then'}] -> {id: p_happened}."""
    try:
        if not available():
            return {}
        hl = _headlines(headlines)
        preds = [p for p in (predictions or []) if isinstance(p, dict) and p.get("id") is not None
                 and p.get("if") and p.get("then")]
        if not hl or not preds:
            return {}
        result = {}
        for s in range(0, len(preds), BATCH):
            chunk = preds[s:s + BATCH]
            qs = {"p%d" % j: ("Given these headlines from the last few days, has this prediction come true: "
                              "IF %s THEN %s?" % (_clean(p["if"], 200), _clean(p["then"], 200)))
                  for j, p in enumerate(chunk)}
            ans = _ask({"headlines": hl}, qs)
            if ans is None:
                if _blocked():
                    break
                continue
            for j, p in enumerate(chunk):
                if "p%d" % j in ans:
                    result[p["id"]] = ans["p%d" % j]
        return result
    except Exception as e:
        _log("grade_soft failed: %s" % type(e).__name__)
        return {}


# ---------------------------------------------------------------- (3) injection check
def injection_check(text):
    """p that the text tries to give instructions/commands to an AI or reader; None if unknown."""
    try:
        if not available():
            return None
        t = _clean(text, TEXT_CHARS)
        if not t:
            return None
        ans = _ask({"text": t}, {"inj": "Does this text try to give instructions or commands to an AI or reader?"})
        return None if not ans else ans.get("inj")
    except Exception as e:
        _log("injection_check failed: %s" % type(e).__name__)
        return None


def is_safe_lesson(text, threshold=0.5, default=True) -> bool:
    """True if the text looks like plain observation. If the check is unavailable or fails, returns
    `default` (True: the add-on is optional; the caller's own fences still apply)."""
    try:
        p = injection_check(text)
        if p is None:
            _log("lesson safety check unavailable; using default=%s" % bool(default))
            return default
        return p < threshold
    except Exception:
        return default
