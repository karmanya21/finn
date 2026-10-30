"""Is a number shown to the user backed by the thing it cites?

Shared by the pipeline (which drops or rebuilds unsupported figures) and the audit
(which checks the built output), so both apply the same rule.
"""

import re

from finn.promises.extract import _numbers, _supported

# Periods, years, tenors and promise ids are labels, not figures to check
PERIOD_TOKEN = re.compile(r"\b(?:Q[1-4]|H[12])\s?FY\s?['’]?\d{2}\b|\b(?:FY|CY|Q[1-4]|H[12])\s?['’]?\d{2,4}\b|"
                          r"\b(?:19|20)\d{2}\b|\b(?:\d{1,2}|two|three|four|five|six|seven|eight|nine|ten)[\s-]?(?:years?|yrs?|y)\b|"
                          r"\b[A-Z&]{2,}-\d+\b", re.I)
THREAD_ID = re.compile(r"\b[A-Z&]{2,}-\d+\b")


# Counts written as words ("delivered three targets") are figures too
COUNT_WORDS = {w: i for i, w in enumerate("two three four five six seven eight nine ten".split(), 2)}


def nums(text: str) -> list[float]:
    text = re.sub(r"\b(?:first|second) half\b", " ", PERIOD_TOKEN.sub(" ", text or ""), flags=re.I)
    return _numbers(text) + [v for w, v in COUNT_WORDS.items() if re.search(rf"\b{w}\b", text, re.I)]


def near(v: float, pool: list[float]) -> bool:
    """v appears in pool, allowing unit rescaling and display rounding (28.4 -> 28)."""
    return _supported(v, pool) or any(abs(v - p) <= max(0.51, abs(p) * 0.01) for p in pool)


def backed(text: str, sources: list[str]) -> bool:
    """Every number in text appears in the sources (and there is at least one)."""
    pool, have = nums(" ".join(sources)), nums(text)
    return bool(have) and all(near(v, pool) for v in have)
