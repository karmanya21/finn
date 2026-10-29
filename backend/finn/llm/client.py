"""One function, `complete_json`, over free-tier providers, with a disk cache.

Every response is cached by a hash of (provider, model, system, prompt, schema), so
re-running the pipeline over unchanged documents costs nothing and burns no quota.
"""

import hashlib
import json
import time

import requests

from finn import config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"


class LLMError(RuntimeError):
    pass


def complete_json(system: str, prompt: str, schema: dict, model: str | None = None,
                  provider: str | None = None, temperature: float = 0.0,
                  fallback: str | None = None) -> dict:
    """`fallback`: a model to use if `model` stays unavailable. Free-tier capacity for
    the stronger models comes and goes (503 "high demand"), so nothing on the critical
    path may depend on them alone."""
    provider = provider or config.LLM_PROVIDER
    model = model or (config.MODEL_CHEAP if provider == "gemini" else GROQ_MODEL)
    key = hashlib.sha256(json.dumps([provider, model, system, prompt, schema],
                                    sort_keys=True).encode()).hexdigest()
    path = config.CACHE / "llm" / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())["data"]

    call = _gemini if provider == "gemini" else _groq
    attempts = 3 if fallback else 5
    for attempt in range(attempts):
        try:
            data, usage = call(system, prompt, schema, model, temperature)
            break
        except _RateLimited as e:  # free tiers throttle hard; back off and retry
            # With a fallback ready, don't wait long on a model that's out of capacity.
            time.sleep(e.wait or (5 if fallback else 15) * (attempt + 1))
        except (requests.Timeout, requests.ConnectionError):  # stalls and network blips
            time.sleep(10 * (attempt + 1))
    else:
        if fallback and fallback != model:
            return complete_json(system, prompt, schema, fallback, provider, temperature)
        raise LLMError(f"{provider}/{model}: unavailable after {attempts} attempts")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": provider, "model": model, "usage": usage,
                                "at": time.time(), "data": data}, indent=1))
    return data


class _RateLimited(Exception):
    def __init__(self, wait: float | None = None):
        self.wait = wait


def _gemini(system, prompt, schema, model, temperature):
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set (see .env.example)")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                             "responseJsonSchema": schema},
    }
    r = requests.post(GEMINI_URL.format(model=model), json=body, timeout=300,
                      headers={"x-goog-api-key": config.GEMINI_API_KEY})
    if r.status_code in (429, 500, 503):  # quota, or "model is experiencing high demand"
        raise _RateLimited()
    if r.status_code != 200:
        raise LLMError(f"gemini {r.status_code}: {r.text[:500]}")
    out = r.json()
    try:
        text = out["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise LLMError(f"gemini returned no content: {json.dumps(out)[:500]}")
    return json.loads(text), out.get("usageMetadata", {})


def _groq(system, prompt, schema, model, temperature):
    if not config.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY is not set (see .env.example)")
    body = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "out", "schema": schema}},
    }
    r = requests.post(GROQ_URL, json=body, timeout=300,
                      headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
    if r.status_code == 429:
        raise _RateLimited(float(r.headers.get("retry-after", 0)) or None)
    if r.status_code != 200:
        raise LLMError(f"groq {r.status_code}: {r.text[:500]}")
    out = r.json()
    return json.loads(out["choices"][0]["message"]["content"]), out.get("usage", {})
