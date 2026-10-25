"""
Minimal LLM client (no SDKs). Uses Google Gemini when GEMINI_API_KEY is set,
then NVIDIA NIM (OpenAI-compatible) with NVIDIA_API_KEY. Each model is tried
in turn until one returns valid JSON; generate_json() returns None if all fail,
so callers can always fall back to the non-LLM path.
"""

import json
import os
import re
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


def available() -> bool:
    return bool(GEMINI_API_KEY or NVIDIA_API_KEY)


def _extract_json(text: str, require: str = None):
    """Parse JSON from a reply that may be wrapped in reasoning text or code fences.
    Returns the largest JSON object found (that has `require` as a key, if given)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
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
    """Close brackets on a reply cut off mid-JSON (token limit) and keep what parsed."""
    start = text.find("{")
    if start < 0:
        return None
    stack, in_str, esc, last_ok = [], False, False, None
    for i in range(start, len(text)):
        ch = text[i]
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
            if stack:
                stack.pop()
            last_ok = i  # a complete value ended here
        if not stack:
            return None  # fully closed yet unparseable: genuinely malformed
    if last_ok is None:
        return None
    # Cut back to the last complete value, then re-close whatever was open there
    head = text[start:last_ok + 1]
    stack, in_str, esc = [], False, False
    for ch in head:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            stack.pop()
    try:
        obj = json.loads(head + "".join(reversed(stack)))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and (not require or require in obj) else None


def _gemini(model, prompt, label, max_tokens, temperature):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": temperature, "maxOutputTokens": max_tokens},
    }
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    for attempt in range(2):
        try:
            resp = requests.post(GEMINI_API.format(model=model), json=body, headers=headers, timeout=180)
        except requests.RequestException as e:
            print(f"[{label}] {model} error: {e}")
            return None
        if resp.status_code in (429, 500, 503) and attempt == 0:
            print(f"[{label}] {model} HTTP {resp.status_code}, retrying in 20s")
            time.sleep(20)
            continue
        if resp.status_code != 200:
            print(f"[{label}] {model} HTTP {resp.status_code}: {resp.text[:300]}")
            return None
        cand = (resp.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        if not text:
            print(f"[{label}] {model} empty response (finish={cand.get('finishReason')})")
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
                               stream=True, timeout=(15, 90)) as resp:
                if resp.status_code in (429, 500, 502, 503) and attempt == 0:
                    print(f"[{label}] {model} HTTP {resp.status_code}, retrying in 20s")
                    time.sleep(20)
                    continue
                if resp.status_code != 200:
                    print(f"[{label}] {model} HTTP {resp.status_code}: {resp.text[:200]}")
                    return None
                parts = []
                for raw in resp.iter_lines(decode_unicode=True):
                    if time.time() - started > deadline_s:
                        print(f"[{label}] {model} exceeded {deadline_s}s — giving up")
                        return None
                    if not raw or not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if data == "[DONE]":
                        break
                    for choice in json.loads(data).get("choices") or []:
                        parts.append((choice.get("delta") or {}).get("content") or "")
                text = "".join(parts)
                if not text.strip():
                    print(f"[{label}] {model} empty response")
                return text or None
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"[{label}] {model} error: {e}")
            return None
    return None


def _nvidia_live_models():
    try:
        resp = requests.get(f"{NVIDIA_API}/models", timeout=15)
        live = {m["id"] for m in resp.json().get("data", [])}
        return [m for m in NVIDIA_MODELS if m in live] or NVIDIA_MODELS
    except Exception:
        return NVIDIA_MODELS


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

    deadline = time.time() + budget_s
    for call, model in candidates:
        remaining = deadline - time.time()
        if remaining < 20:
            print(f"[{label}] time budget ({budget_s}s) used up")
            break
        started = time.time()
        text = (call(model, prompt, label, max_tokens, temperature, deadline_s=min(remaining, per_model_s or remaining))
                if call is _nvidia else call(model, prompt, label, max_tokens, temperature))
        if not text:
            continue
        result = _extract_json(text, require)
        if result is None:
            print(f"[{label}] {model} gave no usable JSON; starts: {text[:160]!r}")
            continue
        last_model = model
        print(f"[{label}] ok via {model} in {time.time() - started:.0f}s ({len(text)} chars)")
        return result
    print(f"[{label}] all models failed — using fallback")
    return None


if __name__ == "__main__":
    # Probe which NVIDIA models answer for this key: python llm.py model1 model2 ...
    import sys
    for model in sys.argv[1:]:
        t0 = time.time()
        out = _nvidia(model, 'Return JSON {"ok": true, "n": 3}.', "PROBE", 200, 0.0, deadline_s=120)
        print(f"[PROBE] {model}: {'OK' if out else 'FAIL'} in {time.time() - t0:.1f}s {(out or '')[:80]!r}")
