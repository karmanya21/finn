"""One function, `complete_json`, over free-tier providers, with a disk cache.

Every response is cached by a hash of (provider, model, system, prompt, schema), so
re-running the pipeline over unchanged documents costs nothing and burns no quota.

Free tiers fail in three ways, each handled differently:
  - per-minute throttling (429)       -> back off and retry the same model;
  - "high demand" / hangs (503, timeout) -> skip that model for 10 minutes;
  - daily allowance used up (429 PerDay) -> skip it until Google's reset, remembered on
    disk so every process (pipeline, API server) agrees.
Each request walks a chain of models: the one asked for, its fallback, then the cheap
pool. Gemini's free allowance is counted per model, so the pool multiplies the budget.
"""

import hashlib
import json
import re
import time

import requests

from finn import config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
EXHAUSTED_FILE = config.CACHE / "exhausted.json"


class LLMError(RuntimeError):
    pass


class _RateLimited(Exception):
    def __init__(self, wait: float | None = None):
        self.wait = wait


class _Exhausted(Exception):
    def __init__(self, seconds: float):
        self.seconds = seconds


class _Unavailable(Exception):
    pass


_DOWN: dict[str, float] = {}  # model -> skip until (in-process: overloaded or hanging)


def _exhausted() -> dict[str, float]:
    try:
        return {m: t for m, t in json.loads(EXHAUSTED_FILE.read_text()).items() if t > time.time()}
    except (FileNotFoundError, ValueError):
        return {}


def _mark_exhausted(model: str, seconds: float) -> None:
    ex = _exhausted()
    ex[model] = time.time() + seconds
    EXHAUSTED_FILE.write_text(json.dumps(ex))


def _key(provider, model, system, prompt, schema) -> str:
    return hashlib.sha256(json.dumps([provider, model, system, prompt, schema],
                                     sort_keys=True).encode()).hexdigest()


def complete_json(system: str, prompt: str, schema: dict, model: str | None = None,
                  provider: str | None = None, temperature: float = 0.0,
                  fallback: str | None = None) -> dict:
    """`model` is the preferred model; `fallback` and then the cheap pool are tried when
    it's unavailable. Nothing on the critical path depends on one free model."""
    provider = provider or config.LLM_PROVIDER
    first = model or (config.MODEL_CHEAP if provider == "gemini" else GROQ_MODEL)
    chain = [first] + ([fallback] if fallback else [])
    if provider == "gemini":
        chain += [m for m in config.MODEL_POOL if m not in chain]

    for m in chain:  # any model's cached answer will do
        path = config.CACHE / "llm" / f"{_key(provider, m, system, prompt, schema)}.json"
        if path.exists():
            return json.loads(path.read_text())["data"]

    skip = _exhausted()
    live = [m for m in chain if m not in skip]
    if live and all(_DOWN.get(m, 0) > time.time() for m in live):
        _DOWN.pop(live[-1], None)  # never bench the last model standing: overloads pass
    tried = []
    for i, m in enumerate(chain):
        if m in skip or _DOWN.get(m, 0) > time.time():
            continue
        tried.append(m)
        last = i == len(chain) - 1
        try:
            data, usage = _attempt(provider, m, system, prompt, schema, temperature, patient=last)
        except _Exhausted as e:
            _mark_exhausted(m, e.seconds)
            continue
        except _Unavailable:
            _DOWN[m] = time.time() + 600
            continue
        path = config.CACHE / "llm" / f"{_key(provider, m, system, prompt, schema)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"provider": provider, "model": m, "usage": usage,
                                    "at": time.time(), "data": data}, indent=1))
        return data
    raise LLMError(f"no free model available (tried {', '.join(tried) or 'none'}; "
                   f"daily allowance used up: {', '.join(skip) or 'none'})")


def _attempt(provider, model, system, prompt, schema, temperature, patient: bool):
    """Call one model with retries for per-minute throttling. `patient` (the last model in
    the chain) waits longer; earlier models get one quick chance before we move on."""
    call = _gemini if provider == "gemini" else _groq
    attempts, timeout = (5, 300) if patient else (2, 90)
    for attempt in range(attempts):
        try:
            return call(system, prompt, schema, model, temperature, timeout)
        except _RateLimited as e:
            time.sleep(e.wait or (15 if patient else 5) * (attempt + 1))
        except (requests.Timeout, requests.ConnectionError):
            time.sleep(5 * (attempt + 1))
    raise _Unavailable()


def _gemini(system, prompt, schema, model, temperature, timeout=300):
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set (see .env.example)")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                             "responseJsonSchema": schema},
    }
    r = requests.post(GEMINI_URL.format(model=model), json=body, timeout=timeout,
                      headers={"x-goog-api-key": config.GEMINI_API_KEY})
    if r.status_code == 429 and "PerDay" in r.text:
        m = re.search(r'"retryDelay":\s*"(\d+)s"', r.text)
        raise _Exhausted(float(m.group(1)) if m else 6 * 3600)
    if r.status_code in (429, 500, 503):  # per-minute throttling, or "high demand"
        raise _RateLimited()
    if r.status_code != 200:
        raise LLMError(f"gemini {r.status_code}: {r.text[:500]}")
    out = r.json()
    try:
        text = out["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise LLMError(f"gemini returned no content: {json.dumps(out)[:500]}")
    return json.loads(text), out.get("usageMetadata", {})


def _groq(system, prompt, schema, model, temperature, timeout=300):
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
    r = requests.post(GROQ_URL, json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
    if r.status_code == 429:
        raise _RateLimited(float(r.headers.get("retry-after", 0)) or None)
    if r.status_code != 200:
        raise LLMError(f"groq {r.status_code}: {r.text[:500]}")
    out = r.json()
    return json.loads(out["choices"][0]["message"]["content"]), out.get("usage", {})
