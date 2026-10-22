"""
Minimal LLM client (no SDKs). Uses Google Gemini when GEMINI_API_KEY is set,
otherwise NVIDIA NIM (OpenAI-compatible) with NVIDIA_API_KEY.
generate_json() returns parsed JSON or None, so callers can always fall back
to the non-LLM path.
"""

import json
import os
import re
import time

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")

# First model that answers wins; override with GEMINI_MODEL / NVIDIA_MODEL
GEMINI_MODELS = [m for m in [os.environ.get("GEMINI_MODEL"),
                             "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"] if m]
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

NVIDIA_MODELS = [m for m in [os.environ.get("NVIDIA_MODEL"),
                             # Models that answered for this account's key (probed Oct 2026);
                             # many others in /v1/models return 404 or time out
                             "nvidia/nemotron-3-ultra-550b-a55b", "z-ai/glm-5.3-flash",
                             "stepfun-ai/step-5-preview", "z-ai/glm-5.3"] if m]
NVIDIA_API = "https://integrate.api.nvidia.com/v1"

last_model = None


def available() -> bool:
    return bool(GEMINI_API_KEY or NVIDIA_API_KEY)


def _extract_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _post(url, body, headers, label, model, timeout):
    """POST with one retry on 429/5xx. Returns the response or None."""
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            print(f"[{label}] {model} error: {e}")
            return None
        if resp.status_code in (429, 500, 502, 503) and attempt == 0:
            print(f"[{label}] {model} HTTP {resp.status_code}, retrying in 20s")
            time.sleep(20)
            continue
        if resp.status_code != 200:
            print(f"[{label}] {model} HTTP {resp.status_code}: {resp.text[:300]}")
            return None
        return resp
    return None


def _gemini(prompt, label, max_tokens, temperature):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": temperature, "maxOutputTokens": max_tokens},
    }
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    for model in GEMINI_MODELS:
        resp = _post(GEMINI_API.format(model=model), body, headers, label, model, 120)
        if not resp:
            continue
        data = resp.json()
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        if text:
            usage = data.get("usageMetadata", {})
            return text, model, usage.get("promptTokenCount"), usage.get("candidatesTokenCount")
        print(f"[{label}] {model} empty response (finish={cand.get('finishReason')})")
    return None


def _nvidia_live_models():
    try:
        resp = requests.get(f"{NVIDIA_API}/models", timeout=15)
        live = {m["id"] for m in resp.json().get("data", [])}
        return [m for m in NVIDIA_MODELS if m in live] or NVIDIA_MODELS
    except Exception:
        return NVIDIA_MODELS


def _nvidia_stream(model, prompt, label, max_tokens, temperature, deadline_s=420):
    """Stream a chat completion (slow models time out on a single blocking read)."""
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json",
               "Accept": "text/event-stream"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise news analyst. Reply with valid JSON only, no prose."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
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
                parts, usage = [], {}
                for raw in resp.iter_lines(decode_unicode=True):
                    if time.time() - started > deadline_s:
                        print(f"[{label}] {model} exceeded {deadline_s}s — giving up")
                        return None
                    if not raw or not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        parts.append((choice.get("delta") or {}).get("content") or "")
                text = "".join(parts)
                if text.strip():
                    return text, model, usage.get("prompt_tokens"), usage.get("completion_tokens")
                print(f"[{label}] {model} empty response")
                return None
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"[{label}] {model} error: {e}")
            return None
    return None


def _nvidia(prompt, label, max_tokens, temperature):
    for model in _nvidia_live_models():
        out = _nvidia_stream(model, prompt, label, max_tokens, temperature)
        if out:
            return out
    return None


def generate_json(prompt: str, label: str = "LLM", max_tokens: int = 8192, temperature: float = 0.2):
    global last_model
    providers = []
    if GEMINI_API_KEY:
        providers.append(_gemini)
    if NVIDIA_API_KEY:
        providers.append(_nvidia)
    if not providers:
        print(f"[{label}] skipped — no GEMINI_API_KEY or NVIDIA_API_KEY set")
        return None

    started = time.time()
    for provider in providers:
        out = provider(prompt, label, max_tokens, temperature)
        if not out:
            continue
        text, model, tok_in, tok_out = out
        try:
            result = _extract_json(text)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[{label}] {model} returned invalid JSON ({e}); first 200 chars: {text[:200]!r}")
            continue
        last_model = model
        print(f"[{label}] ok via {model} in {time.time() - started:.0f}s (in={tok_in}, out={tok_out})")
        return result
    print(f"[{label}] all models failed — using fallback")
    return None


if __name__ == "__main__":
    # Probe: which NVIDIA models answer for this key, and how fast
    import sys
    for model in sys.argv[1:]:
        t0 = time.time()
        out = _nvidia_stream(model, 'Return JSON {"ok": true, "n": 3}.', "PROBE", 200, 0.0, deadline_s=120)
        print(f"[PROBE] {model}: {'OK' if out else 'FAIL'} in {time.time() - t0:.1f}s "
              f"{(out[0][:80] if out else '')!r}")
