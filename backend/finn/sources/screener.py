"""screener.in company pages: concall transcript links and the quarterly P&L table.

One polite GET per company, cached on disk. Transcripts are the BSE-hosted PDFs
that screener links to, so the text we read is the company's own filing.
"""

import hashlib
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf
import requests
from bs4 import BeautifulSoup

from finn.config import RAW, TEXT, USER_AGENT

SCREENER = "https://www.screener.in"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = USER_AGENT
PAGE_TTL = 6 * 3600


@dataclass
class Concall:
    label: str  # e.g. "Oct 2026", as screener shows it
    transcript_url: str | None


def _get(url: str, dest: Path, ttl: float | None) -> Path:
    if dest.exists() and (ttl is None or time.time() - dest.stat().st_mtime < ttl):
        return dest
    resp = SESSION.get(url, timeout=60)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    time.sleep(1)  # be polite to free sources
    return dest


def company_page(slug: str, consolidated: bool = True) -> BeautifulSoup:
    """The company's screener page. Consolidated by default; companies without
    subsidiaries only publish standalone numbers, so fall back when that table is empty."""
    kind = "consolidated" if consolidated else "standalone"
    url = f"{SCREENER}/company/{requests.utils.quote(slug)}/{'consolidated/' if consolidated else ''}"
    path = _get(url, RAW / "screener" / f"{slug}.{kind}.html", PAGE_TTL)
    page = BeautifulSoup(path.read_text(), "lxml")
    if consolidated and not table(page, "quarters").get("Sales") and not table(page, "quarters").get("Revenue"):
        return company_page(slug, consolidated=False)
    return page


def schedule(page: BeautifulSoup, parent: str, section: str = "balance-sheet") -> dict[str, dict[str, float | None]]:
    """A row's breakdown ("Other Assets" -> Inventories, Cash Equivalents, ...), the data
    behind screener's expandable "+" rows. {row: {column: value}}, cached like pages."""
    tag = page.find(attrs={"data-company-id": True})
    if not tag:
        return {}
    cid = tag["data-company-id"]
    consolidated = bool(page.find("a", href=re.compile(r"/consolidated/$"), class_=re.compile("active")) or
                        "consolidated" in (page.find("link", rel="canonical") or {}).get("href", ""))
    params = {"parent": parent, "section": section}
    if consolidated:
        params["consolidated"] = ""
    dest = RAW / "screener" / f"schedule_{cid}_{section}_{parent.replace(' ', '_')}_{int(consolidated)}.json"
    if not dest.exists() or time.time() - dest.stat().st_mtime > PAGE_TTL:
        r = SESSION.get(f"{SCREENER}/api/company/{cid}/schedules/", params=params, timeout=30)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(r.text)
        time.sleep(1)
    import json
    raw = json.loads(dest.read_text())
    return {row: {c: _num(v) for c, v in cols.items()} for row, cols in raw.items() if isinstance(cols, dict)}


def search(query: str, limit: int = 8) -> list[dict]:
    """[{symbol, name}] for listed companies matching a name or ticker."""
    r = SESSION.get(f"{SCREENER}/api/company/search/", params={"q": query}, timeout=30)
    r.raise_for_status()
    out = []
    for c in r.json()[:limit]:
        m = re.match(r"^/company/([^/]+)/", c.get("url", ""))
        if m:
            out.append({"symbol": requests.utils.unquote(m.group(1)), "name": c["name"]})
    return out


def meta(page: BeautifulSoup) -> dict:
    """Name and industry from the page header and peer-comparison links."""
    h1 = page.find("h1")
    tags = {a.get("title"): a.get_text(strip=True) for a in page.select("#peers a[title]")}
    return {"name": h1.get_text(strip=True) if h1 else None,
            "sector": tags.get("Industry") or tags.get("Broad Industry") or tags.get("Sector")}


def concalls(page: BeautifulSoup) -> list[Concall]:
    heading = page.find("h3", string=re.compile(r"^\s*Concalls\s*$"))
    if not heading:
        return []
    box = heading.find_parent("div").find_parent("div")
    out = []
    for li in box.select("ul.list-links > li"):
        label = li.find("div").get_text(strip=True)
        a = li.find("a", title="Raw Transcript")
        out.append(Concall(label, a["href"] if a else None))
    return out


def table(page: BeautifulSoup, section_id: str) -> dict[str, dict[str, float | None]]:
    """{row name: {column label: value}} from a screener table section (Rs crore).

    Sections: quarters, profit-loss, balance-sheet, cash-flow, ratios.
    """
    section = page.find("section", id=section_id)
    if not section or not section.find("table"):
        return {}
    t = section.find("table")
    cols = [th.get_text(strip=True) for th in t.select("thead th")][1:]
    rows: dict[str, dict[str, float | None]] = {}
    for tr in t.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells or not cells[0]:
            continue
        name = cells[0].rstrip("+").strip()
        rows[name] = {c: _num(v) for c, v in zip(cols, cells[1:])}
    return rows


def quarterly_results(page: BeautifulSoup) -> dict[str, dict[str, float | None]]:
    return table(page, "quarters")


def _num(v: str) -> float | None:
    v = v.replace(",", "").replace("%", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def transcript_text(symbol: str, call: Concall) -> Path | None:
    """Download the transcript PDF (once, content never changes) and extract text."""
    if not call.transcript_url:
        return None
    key = hashlib.sha1(call.transcript_url.encode()).hexdigest()[:12]
    stem = f"{symbol}_{call.label.replace(' ', '')}_{key}"
    txt = TEXT / "transcripts" / f"{stem}.txt"
    if txt.exists():
        return txt
    pdf = _get(call.transcript_url, RAW / "transcripts" / f"{stem}.pdf", ttl=None)
    with pymupdf.open(pdf) as doc:
        pages = [f"[[page {i + 1}]]\n{p.get_text()}" for i, p in enumerate(doc)]
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("\n".join(pages))
    return txt


def transcript_path(filename: str) -> Path:
    return TEXT / "transcripts" / filename


def as_dict(c: Concall) -> dict:
    return asdict(c)
