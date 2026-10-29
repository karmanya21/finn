"""BSE corporate announcements: every filing a company makes, with its PDF.

The exchange's own feed (primary data). BSE's API wants browser-like headers; responses
are cached for an hour so the digest can be rebuilt freely.
"""

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta

import pymupdf
import requests

from finn.config import RAW, TEXT
from finn.sources import screener

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
PDF = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{}"
TTL = 3600
RESOLVED: dict[str, str] = {}  # attachment URL -> archive URL it actually lives at

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
})


def scrip_code(slug: str) -> str | None:
    """BSE's six-digit code, from the link on the company's screener page."""
    page = screener.company_page(slug)
    for a in page.select("a[href*='bseindia.com/stock-share-price']"):
        if m := re.search(r"/(\d{6})/?$", a["href"].rstrip("/") + "/"):
            return m.group(1)
    return None


def announcements(code: str, days: int = 30) -> list[dict]:
    """Filings in the last `days`, newest first."""
    until = date.today()
    since = until - timedelta(days=days)
    key = f"{code}_{since:%Y%m%d}_{until:%Y%m%d}"
    cache = RAW / "bse" / f"{key}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < TTL:
        return json.loads(cache.read_text())

    rows, page = [], 1
    while page <= 10:
        r = SESSION.get(API, params={"pageno": page, "strCat": -1, "strPrevDate": f"{since:%Y%m%d}",
                                     "strScrip": code, "strSearch": "P", "strToDate": f"{until:%Y%m%d}",
                                     "strType": "C", "subcategory": -1}, timeout=30)
        r.raise_for_status()
        batch = r.json().get("Table") or []
        rows += batch
        if len(batch) < 50:
            break
        page += 1
        time.sleep(0.5)

    out = [{
        "id": str(x.get("NEWSID")),
        "at": (x.get("NEWS_DT") or x.get("DT_TM") or "")[:19],
        "category": (x.get("CATEGORYNAME") or "").strip(),
        "subcategory": (x.get("SUBCATNAME") or "").strip(),
        "subject": re.sub(r"\s+", " ", (x.get("NEWSSUB") or "")).strip(),
        "headline": re.sub(r"\s+", " ", (x.get("HEADLINE") or "")).strip(),
        "critical": bool(x.get("CRITICALNEWS")),
        "pdf": PDF.format(x["ATTACHMENTNAME"]) if x.get("ATTACHMENTNAME") else None,
    } for x in rows]
    out.sort(key=lambda a: a["at"], reverse=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, indent=1))
    time.sleep(0.5)
    return out


def pdf_text(url: str, max_pages: int = 6) -> str:
    """Text of a filing's attachment (first pages: profiles and summaries sit up front)."""
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    txt = TEXT / "filings" / f"{key}.txt"
    if txt.exists():
        return txt.read_text()
    pdf = RAW / "filings" / f"{key}.pdf"
    if not pdf.exists():
        # BSE moves older attachments from AttachLive to AttachHis.
        r = SESSION.get(url, timeout=60)
        if r.status_code == 404 and "/AttachLive/" in url:
            alt = url.replace("/AttachLive/", "/AttachHis/")
            r = SESSION.get(alt, timeout=60)
            if r.ok:
                RESOLVED[url] = alt
        r.raise_for_status()
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(r.content)
        time.sleep(0.5)
    try:
        with pymupdf.open(pdf) as doc:
            text = "\n".join(p.get_text() for p in list(doc)[:max_pages])
    except Exception:
        text = ""
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text(text)
    return text


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)
