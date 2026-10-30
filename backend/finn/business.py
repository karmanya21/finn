"""What the company does, from its latest annual report: segments, revenue mix, geographies.

Annual reports run to hundreds of pages, so we score every page for business-description
content and send only the best few to the cheap model. Any share or amount it returns must
appear in those pages, or it's dropped, the same rule as for promise targets.
"""

import hashlib
import re

import pymupdf

from finn.config import RAW
from finn.llm.client import complete_json
from finn.promises.extract import _numbers, _supported
from finn.sources import bse, screener

PAGE_SIGNALS = [
    (r"segment(s|al)? (revenue|results|information|reporting)|operating segments?", 4),
    (r"business overview|about (the company|us)|who we are|our business(es)?", 3),
    (r"revenue (mix|share|contribution|by)|share of revenue|% of (total )?revenue|contribution to revenue", 3),
    (r"geograph|domestic and (international|export)|exports? (contributed|accounted)", 2),
    (r"management discussion|industry structure|product portfolio|business segments?", 2),
]
MAX_PAGES = 7

SCHEMA = {
    "type": "object",
    "properties": {
        "what": {"type": "string", "description": "What the company does, one plain sentence, at most 30 words"},
        "segments": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "share_pct": {"type": ["number", "null"], "description": "Share of total revenue in %, only if stated or computable from stated figures"},
            "revenue_cr": {"type": ["number", "null"], "description": "Segment revenue in Rs crore, only if stated"},
            "note": {"type": ["string", "null"], "description": "What the segment sells, at most 12 words"},
            "page": {"type": ["integer", "null"]},
        }, "required": ["name", "share_pct", "revenue_cr", "note", "page"]}},
        "geographies": {"type": "array", "items": {"type": "object", "properties": {
            "region": {"type": "string"}, "share_pct": {"type": ["number", "null"]}, "page": {"type": ["integer", "null"]},
        }, "required": ["region", "share_pct", "page"]}},
        "year": {"type": ["string", "null"], "description": "The fiscal year the figures are for, e.g. FY26"},
    },
    "required": ["what", "segments", "geographies", "year"],
}

SYSTEM = """You summarise what an Indian listed company does, using only the annual-report pages given.
- what: one plain sentence: what it makes or sells, and to whom.
- segments: the company's reported business segments (or main businesses), with each one's share of revenue for the latest year if the pages state it or give segment revenues to compute it from. Use null when not given; never guess.
- geographies: domestic vs export or regional split, if stated.
- page: the [page N] the figure came from.
Use only these pages. No outside knowledge."""


def annual_report(slug: str) -> tuple[str, str] | None:
    """(label, url) of the latest annual report linked on the screener page."""
    page = screener.company_page(slug)
    h = page.find("h3", string=re.compile(r"Annual reports", re.I))
    if not h:
        return None
    for a in h.find_parent("div").find_all("a"):
        if a.get("href", "").lower().endswith(".pdf"):
            return a.get_text(" ", strip=True), a["href"]
    return None


def _pages(url: str) -> list[str]:
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    pdf = RAW / "annual" / f"{key}.pdf"
    if not pdf.exists():
        r = bse.SESSION.get(url, timeout=180)
        r.raise_for_status()
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(r.content)
    with pymupdf.open(pdf) as doc:
        return [p.get_text() for p in doc]


def _best_pages(pages: list[str]) -> list[int]:
    scored = []
    for i, t in enumerate(pages):
        low = t.lower()
        s = sum(w * len(re.findall(pat, low)) for pat, w in PAGE_SIGNALS)
        s += 1 if re.search(r"\d+(\.\d+)?\s?%", t) else 0  # shares are usually stated in %
        if s:
            scored.append((s, i))
    return sorted(i for _, i in sorted(scored, reverse=True)[:MAX_PAGES])


def build(company: str, slug: str) -> dict | None:
    ar = annual_report(slug)
    if not ar:
        return None
    label, url = ar
    pages = _pages(url)
    picked = _best_pages(pages)
    if not picked:
        return None
    text = "\n\n".join(f"[page {i + 1}]\n{pages[i][:3500]}" for i in picked)
    out = complete_json(SYSTEM, f"Company: {company}\n\n{text}", SCHEMA)

    nums = _numbers(text)
    def keep(v):
        return v if v is not None and _supported(v, nums) else None
    segs = [{**s, "share_pct": keep(s["share_pct"]), "revenue_cr": keep(s["revenue_cr"])} for s in out["segments"]]
    geos = [{**g, "share_pct": keep(g["share_pct"])} for g in out["geographies"]]
    return {"what": out["what"], "segments": segs[:8], "geographies": geos[:5], "year": out.get("year"),
            "source": label, "url": url, "pages": [i + 1 for i in picked]}
