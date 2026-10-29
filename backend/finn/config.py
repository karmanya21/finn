import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
RAW = DATA / "raw"  # fetched HTML/PDFs, keyed by source
TEXT = DATA / "text"  # extracted transcript text
CACHE = DATA / "cache"  # LLM response cache
OUT = DATA / "out"  # pipeline outputs

for d in (RAW, TEXT, CACHE, OUT):
    d.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (FINN research prototype)"

# Free-tier routing: a cheap model for bulk extraction, a stronger one only
# where reasoning is needed. Pinned versions, not "-latest" aliases: an alias can
# move under the eval, and in Oct 2026 the newest Flash (3.8) hung on the free tier.
LLM_PROVIDER = os.getenv("FINN_LLM_PROVIDER", "gemini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL_CHEAP = os.getenv("FINN_MODEL_CHEAP", "gemini-3.5-flash-lite")
MODEL_STRONG = os.getenv("FINN_MODEL_STRONG", "gemini-3.5-flash")
# Free allowances are per model per day (Oct 2026: 500 requests for 3.5 Flash-Lite, 20 for
# 3.5 Flash), so cheap work rolls over to the next model when one runs out.
MODEL_POOL = os.getenv("FINN_MODEL_POOL", f"{MODEL_CHEAP},gemini-3.1-flash-lite").split(",")
