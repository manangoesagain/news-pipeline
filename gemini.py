"""
Minimal Gemini REST client (no SDK). Returns parsed JSON or None on failure,
so callers can always fall back to the non-LLM path.
"""

import json
import os
import re
import time

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# First model that answers wins; override with the GEMINI_MODEL env var
MODELS = [m for m in [os.environ.get("GEMINI_MODEL"),
                      "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"] if m]
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def available() -> bool:
    return bool(GEMINI_API_KEY)


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: outermost {...}
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def generate_json(prompt: str, label: str = "gemini", max_tokens: int = 16384, temperature: float = 0.2):
    if not GEMINI_API_KEY:
        print(f"[{label}] skipped — GEMINI_API_KEY not set")
        return None

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}

    for model in MODELS:
        for attempt in range(2):
            try:
                resp = requests.post(API.format(model=model), json=body, headers=headers, timeout=120)
                if resp.status_code == 429 and attempt == 0:
                    print(f"[{label}] {model} rate-limited, retrying in 20s")
                    time.sleep(20)
                    continue
                if resp.status_code != 200:
                    print(f"[{label}] {model} HTTP {resp.status_code}: {resp.text[:300]}")
                    break
                data = resp.json()
                cand = (data.get("candidates") or [{}])[0]
                parts = cand.get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                if not text:
                    print(f"[{label}] {model} empty response (finish={cand.get('finishReason')})")
                    break
                result = _extract_json(text)
                usage = data.get("usageMetadata", {})
                print(f"[{label}] ok via {model} "
                      f"(in={usage.get('promptTokenCount')}, out={usage.get('candidatesTokenCount')})")
                return result
            except Exception as e:
                print(f"[{label}] {model} error: {e}")
                break
    print(f"[{label}] all models failed — using fallback")
    return None
