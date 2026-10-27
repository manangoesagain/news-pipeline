"""
Minimal LLM client (no SDKs). Uses Google Gemini when GEMINI_API_KEY is set,
then NVIDIA NIM (OpenAI-compatible) with NVIDIA_API_KEY. Each model is tried
in turn until one returns valid JSON; generate_json() returns None if all fail,
so callers can always fall back to the non-LLM path.
"""

import json
import os
import re
import threading
import time

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")

# Override the first choice with GEMINI_MODEL / NVIDIA_MODEL
GEMINI_MODELS = [m for m in [os.environ.get("GEMINI_MODEL"),
                             "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"] if m]
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

NVIDIA_MODELS = [m for m in [os.environ.get("NVIDIA_MODEL"),
                             # Models that answered for this account's key (probed Oct 2026);
                             # many others in /v1/models return 404 or time out
                             "nvidia/nemotron-3-ultra-550b-a55b", "z-ai/glm-5.3-flash",
                             "stepfun-ai/step-5-preview", "z-ai/glm-5.3"] if m]
NVIDIA_API = "https://integrate.api.nvidia.com/v1"
SYSTEM_PROMPT = ("You are a precise news analyst. Output only the final JSON object, with no "
                 "reasoning, commentary or markdown before or after it.")

last_model = None

# A streaming model that sends no new token for this long is abandoned (the next model gets a turn)
IDLE_S = 75
RETRY_NOTE = ("\n\nYour previous reply was not valid JSON. Return ONLY one valid JSON object, "
              "no commentary, and keep every text field short.")

# Process-wide wall-clock cap shared by every generate_json call (the CI job limit is 30 min)
HARD_STOP = time.time() + float(os.environ.get("LLM_TOTAL_BUDGET_S") or 1380)

_tls = threading.local()  # per-thread: why the last call failed, and which model last succeeded


def _why(reason: str):
    _tls.reason = reason


def model_used():
    """Model that produced the calling thread's last successful generate_json result."""
    return getattr(_tls, "model", None)


def available() -> bool:
    return bool(GEMINI_API_KEY or NVIDIA_API_KEY)


def _drop_stray_closers(text: str) -> str:
    """Remove closing brackets that do not match the open one (a model slip like '}]
]')."""
    out, stack, in_str, esc = [], [], False, False
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                continue  # stray closer: drop it
            stack.pop()
        out.append(ch)
    return "".join(out)


def _extract_json(text: str, require: str = None):
    """Parse JSON from a reply that may be wrapped in reasoning text or code fences.
    Returns the largest JSON object found (that has `require` as a key, if given)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    cleaned = re.sub(r",\s*([}\]])", lambda m: m.group(1), text)  # trailing commas
    if cleaned != text:
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict) and (not require or require in obj):
                return obj
        except json.JSONDecodeError:
            pass
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and (not require or require in obj):
            return obj
    except json.JSONDecodeError:
        pass
    fixed = _drop_stray_closers(text)
    if fixed != text:
        try:
            obj = json.loads(re.sub(r",\s*([}\]])", lambda m: m.group(1), fixed))
            if isinstance(obj, dict) and (not require or require in obj):
                return obj
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    best, best_len = None, 0
    for m in re.finditer(r"\{", text):
        try:
            obj, end = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (not require or require in obj) and end - m.start() > best_len:
            best, best_len = obj, end - m.start()
    return best or _repair_truncated(text, require)


def _repair_truncated(text: str, require: str = None):
    """Salvage a reply cut off mid-JSON (token limit / over-long): cut back to the latest
    point where a value was complete (after a closer or before a comma), close whatever is
    open there, and keep the first cut that parses and still has `require`. A dangling partial
    element is dropped; every complete list element before it is kept."""
    start = text.find("{")
    if start < 0:
        return None
    text = _drop_stray_closers(text[start:])
    stack, in_str, esc, cuts = [], False, False, []
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            stack.pop()  # _drop_stray_closers guarantees a match
            cuts.append((i + 1, "".join(reversed(stack)), len(stack)))
            if not stack:
                break
        elif ch == "," and stack:
            cuts.append((i, "".join(reversed(stack)), len(stack)))  # cut before the comma
    # Drop the dangling partial element: first prefer cuts at the level of the innermost list
    # (or the top level) that was cut off, so complete elements stay and the half one goes.
    open_lists = [k + 1 for k, c in enumerate(stack) if c == "]"]
    level = max(open_lists) if open_lists else (len(stack) and 1)
    for only_level in (True, False):
        for end, closers, depth in reversed(cuts[-300:]):
            if only_level and depth > max(level, 1):
                continue
            try:
                obj = json.loads(text[:end] + closers)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and (not require or require in obj):
                return obj
    return None


def _gemini(model, prompt, label, max_tokens, temperature, deadline_s=None):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": temperature, "maxOutputTokens": max_tokens},
    }
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    stop = time.time() + (deadline_s if deadline_s is not None else 400)
    for attempt in range(2):
        left = stop - time.time()
        if left < 5:
            _why("timeout")
            return None
        try:
            resp = requests.post(GEMINI_API.format(model=model), json=body, headers=headers,
                                 timeout=min(180, max(10, left)))
        except requests.RequestException as e:
            print(f"[{label}] {model} error: {e}")
            _why("timeout" if isinstance(e, requests.Timeout) else "error")
            return None
        if resp.status_code in (429, 500, 503) and attempt == 0 and stop - time.time() > 20 + 30:
            print(f"[{label}] {model} HTTP {resp.status_code}, retrying in 20s")
            time.sleep(20)
            continue
        if resp.status_code != 200:
            print(f"[{label}] {model} HTTP {resp.status_code}: {resp.text[:300]}")
            _why(f"http {resp.status_code}")
            return None
        cand = (resp.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        if not text:
            print(f"[{label}] {model} empty response (finish={cand.get('finishReason')})")
            _why("empty")
        return text or None
    return None


def _nvidia(model, prompt, label, max_tokens, temperature, deadline_s=420):
    """Stream a chat completion (slow models time out on a single blocking read)."""
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json",
               "Accept": "text/event-stream"}
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if "nemotron" in model:
        # Nemotron reasons in plain text by default and can run out of tokens before the JSON
        body["messages"][0]["content"] = "/no_think " + SYSTEM_PROMPT
        body["chat_template_kwargs"] = {"enable_thinking": False}
    started = time.time()
    for attempt in range(2):
        try:
            with requests.post(f"{NVIDIA_API}/chat/completions", json=body, headers=headers,
                               stream=True,
                               timeout=(15, min(IDLE_S, max(5, deadline_s - (time.time() - started))))) as resp:
                if (resp.status_code in (429, 500, 502, 503) and attempt == 0
                        and deadline_s - (time.time() - started) > 20 + 30):
                    print(f"[{label}] {model} HTTP {resp.status_code}, retrying in 20s")
                    time.sleep(20)
                    continue
                if resp.status_code != 200:
                    print(f"[{label}] {model} HTTP {resp.status_code}: {resp.text[:200]}")
                    _why(f"http {resp.status_code}")
                    return None
                parts = []
                last_token = time.time()
                seen_token = False
                for raw in resp.iter_lines(decode_unicode=True):
                    now = time.time()
                    if now - started > deadline_s:
                        print(f"[{label}] {model} exceeded {deadline_s:.0f}s - giving up")
                        _why("timeout")
                        return None
                    if now - last_token > IDLE_S or (not seen_token and now - started > IDLE_S):
                        print(f"[{label}] {model} no new token for {IDLE_S}s - giving up")
                        _why("timeout")
                        return None
                    if not raw or not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if data == "[DONE]":
                        break
                    last_token = time.time()  # a real data event (keep-alive comments don't count)
                    for choice in json.loads(data).get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content") or ""
                        parts.append(piece)
                        if piece or delta.get("reasoning_content") or delta.get("reasoning"):
                            seen_token = True
                text = "".join(parts)
                if not text.strip():
                    print(f"[{label}] {model} empty response")
                    _why("empty")
                return text or None
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"[{label}] {model} error: {e}")
            _why("timeout" if isinstance(e, requests.Timeout) else "error")
            return None
    return None


def _nvidia_live_models():
    try:
        resp = requests.get(f"{NVIDIA_API}/models", timeout=15)
        live = {m["id"] for m in resp.json().get("data", [])}
        return [m for m in NVIDIA_MODELS if m in live] or NVIDIA_MODELS
    except Exception:
        return NVIDIA_MODELS


def _save_failure(label: str, model: str, text: str, attempt: int = 1):
    """Keep a failed raw reply (only the model's text, never keys/headers) in preview/, which
    is git-ignored and not deployed. Enabled by DRY_RUN or LLM_SAVE_FAILURES = true."""
    if "true" not in (os.environ.get("DRY_RUN", "").lower(), os.environ.get("LLM_SAVE_FAILURES", "").lower()):
        return
    safe = lambda v: re.sub(r"[^A-Za-z0-9._-]", "_", v)
    try:
        os.makedirs("preview", exist_ok=True)
        with open(f"preview/llm_fail_{safe(label)}_{safe(model.split('/')[-1])}_a{attempt}.txt", "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def generate_json(prompt: str, label: str = "LLM", require: str = None,
                  max_tokens: int = 16384, temperature: float = 0.2, budget_s: int = 300,
                  per_model_s: int = None):
    """Try each configured model until one returns a JSON object (containing `require`).
    Gives up once `budget_s` seconds have been spent across all models."""
    global last_model
    candidates = []
    if GEMINI_API_KEY:
        candidates += [(_gemini, m) for m in GEMINI_MODELS]
    if NVIDIA_API_KEY:
        candidates += [(_nvidia, m) for m in _nvidia_live_models()]
    if not candidates:
        print(f"[{label}] skipped — no GEMINI_API_KEY or NVIDIA_API_KEY set")
        return None

    deadline = time.time() + min(budget_s, HARD_STOP - time.time())
    for call, model in candidates:
        prompt_now, temp_now, retried = prompt, temperature, False
        while True:
            remaining = deadline - time.time()
            if remaining < 20:
                print(f"[{label}] time budget used up (call budget {budget_s}s, "
                      f"process cap {max(0, HARD_STOP - time.time()):.0f}s left) - using fallback")
                return None
            started = time.time()
            _why("no reply")
            cap = min(remaining, per_model_s or remaining)
            text = (call(model, prompt_now, label, max_tokens, temp_now, deadline_s=cap)
                    if call is _nvidia else
                    call(model, prompt_now, label, max_tokens, temp_now, deadline_s=cap))
            secs = time.time() - started
            if not text:
                print(f"[{label}] {model} FAIL reason={getattr(_tls, 'reason', 'no reply')} secs={secs:.0f} chars=0")
                break
            result = _extract_json(text, require)
            if result is not None:
                last_model = model
                _tls.model = model
                print(f"[{label}] ok via {model} in {secs:.0f}s ({len(text)} chars)")
                return result
            reason = "missing key" if require and _extract_json(text) is not None else "not json"
            print(f"[{label}] {model} FAIL reason={reason} secs={secs:.0f} chars={len(text)}; "
                  f"starts: {text[:100]!r} ends: {text[-120:]!r}")
            _save_failure(label, model, text, 2 if retried else 1)
            # Retry the same model once if a call of this length still fits in the budget
            if not retried and deadline - time.time() >= max(20, secs):
                retried = True
                prompt_now, temp_now = prompt + RETRY_NOTE, max(0.0, temperature - 0.1)
                print(f"[{label}] {model} retrying once with a corrective note")
                continue
            break
    print(f"[{label}] all models failed — using fallback")
    return None


if __name__ == "__main__":
    # Probe which NVIDIA models answer for this key: python llm.py model1 model2 ...
    import sys
    for model in sys.argv[1:]:
        t0 = time.time()
        out = _nvidia(model, 'Return JSON {"ok": true, "n": 3}.', "PROBE", 200, 0.0, deadline_s=120)
        print(f"[PROBE] {model}: {'OK' if out else 'FAIL'} in {time.time() - t0:.1f}s {(out or '')[:80]!r}")
