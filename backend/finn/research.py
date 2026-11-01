"""Research any listed company: the whole pipeline behind one call.

    research("POLYCAB", progress)  ->  ledger dict (also cached to data/out/ledgers/)

Nothing here is specific to the demo portfolio; a company we never picked goes through
exactly the same steps. `progress(step, detail)` lets the API show what's happening.
"""

import json
from collections import Counter
from datetime import date
from typing import Callable

from finn import business, financials, fiscal, push, snapshot
from finn.config import OUT, ROOT
from finn.portfolio import by_symbol
from finn.promises import evidence, extract, link, resolve
from finn.sources import screener

LEDGERS = OUT / "ledgers"
MOVES = {"raised", "lowered", "narrowed", "widened", "deferred", "advanced"}
N_CALLS = 6

Progress = Callable[[str, str], None]


def _quiet(step: str, detail: str = "") -> None:
    pass


def company_meta(sym: str) -> tuple[str, dict]:
    """(screener slug, {name, sector, size}) for a portfolio company or any other ticker."""
    known = by_symbol(sym)
    slug = known.slug if known else sym
    page = screener.company_page(slug)
    m = screener.meta(page)
    if known:
        return slug, {"name": known.name, "sector": known.sector, "size": known.size}
    return slug, {"name": m["name"] or sym, "sector": m["sector"] or "", "size": None}


def extract_calls(sym: str, slug: str, n: int = N_CALLS, progress: Progress = _quiet) -> list[dict]:
    """Download the last n transcripts and extract promises from each, oldest first."""
    page = screener.company_page(slug)
    # Some months list two entries (an analyst meet beside the earnings call); keep the
    # first, which screener lists as the main call, so each call label is unique.
    seen: set[str] = set()
    concalls = [c for c in screener.concalls(page) if c.transcript_url
                and not (c.label in seen or seen.add(c.label))][:n]
    calls = []
    for i, c in enumerate(reversed(concalls), 1):
        progress("calls", f"Reading the {c.label} call ({i} of {len(concalls)})")
        path = screener.transcript_text(sym, c)
        ex = extract.extract(path.read_text(), sym, c.label)
        fy, q = fiscal.reported_quarter(fiscal.parse_label(c.label))
        calls.append({"call": c.label, "quarter": fiscal.label(fy, q), "file": path.name,
                      "promises": ex.promises, "rejected": len(ex.rejected)})
    (OUT / "promises").mkdir(parents=True, exist_ok=True)
    (OUT / "promises" / f"{sym}.json").write_text(json.dumps(calls, indent=1))
    return calls


# ---- headline numbers -----------------------------------------------------------

# Row names differ by business model. Lenders have no "Operating Profit"; screener's
# "Financing Profit" is after provisions and often negative, so it's left out, not relabelled.
HEADLINE = [
    ("Revenue", ["Sales", "Revenue"]),
    ("EBITDA", ["Operating Profit"]),
    ("PBT", ["Profit before tax"]),
    ("PAT", ["Net Profit"]),
]


def headline(fin: financials.Financials) -> dict | None:
    """Latest quarter's headline numbers, YoY and QoQ, straight from reported results, plus
    what the app needs to say whether growth and margins are improving or slowing: last
    quarter's YoY, and EBITDA margin now / a year ago / last quarter."""
    lq = fin.latest_quarter()
    if not lq:
        return None
    fy, q = lq
    def back(fy, q):
        return (fy, q - 1) if q > 1 else (fy - 1, 4)
    prev = back(fy, q)
    year_ago, prev_year_ago = (fy - 1, q), (prev[0] - 1, prev[1])

    def chg(a, b):
        return None if a is None or not b else (a / b - 1) * 100 if b > 0 else None

    rows = []
    for label, names in HEADLINE:
        name = next((n for n in names if fin.q(fy, q, n) is not None), None)
        if not name:
            continue
        v = fin.q(fy, q, name)
        rows.append({"label": label, "value": v,
                     "qoq": chg(v, fin.q(*prev, name)), "yoy": chg(v, fin.q(*year_ago, name)),
                     "prev_yoy": chg(fin.q(*prev, name), fin.q(*prev_year_ago, name))})

    def margin(p):
        e, s = fin.q(*p, "Operating Profit"), fin.q(*p, "Sales")
        return None if e is None or not s else e / s * 100
    m = {"label": "EBITDA margin", "now": margin((fy, q)), "year_ago": margin(year_ago), "prev": margin(prev)}
    def profit(p):
        return {"ebitda": fin.q(*p, "Operating Profit"), "pbt": fin.q(*p, "Profit before tax"), "pat": fin.q(*p, "Net Profit")}
    notes = financials.oneoff_notes(profit((fy, q)), profit(year_ago))
    return {"quarter": fiscal.label(fy, q), "rows": rows, "notes": notes, "margin": m if m["now"] is not None else None,
            "unit": "₹ cr", "source": "screener.in, consolidated where published"}


# ---- the ledger -----------------------------------------------------------------

def filed_results(sym: str) -> dict | None:
    """The newest results figures read from the company's own filing, from the push side's
    digest (portfolio companies only)."""
    p = ROOT / "web" / "public" / "data" / "filings.json"
    if not p.exists():
        return None
    got = [f for f in json.loads(p.read_text())["items"] if f["symbol"] == sym and f["type"] == "results"
           and f.get("numbers")]
    return max(got, key=lambda f: f["at"])["numbers"] if got else None


def build_ledger(sym: str, slug: str, meta: dict, calls: list[dict], progress: Progress = _quiet) -> dict:
    screener_fin = financials.load(sym, slug)
    fin = screener_fin.with_filing(filed_results(sym))  # verdicts and pace count the newest results
    page = screener.company_page(slug)
    urls = {c.label: c.transcript_url for c in screener.concalls(page)}
    texts = {c["call"]: screener.transcript_path(c["file"]).read_text() for c in calls}
    managers = {p["speaker"] for c in calls for p in c["promises"]}

    threads = []
    if calls:
        progress("link", "Following each promise across calls")
        linked = link.link(sym, calls)
        for n, t in enumerate(linked):
            stmts = resolve.statements(t["ids"], calls)
            events = resolve.changes(stmts)
            last = stmts[-1]
            ev = None
            later = [(c["call"], texts[c["call"]]) for c in calls[last["call_index"] + 1:]]
            if later and resolve.needs_evidence(last, fin):
                progress("verdicts", f"Checking what they said later: {t['title']}")
                ev = evidence.check(sym, {"title": t["title"], "latest": last, "first_call": stmts[0]["call"],
                                          "last_call": last["call"]}, later, managers)
            heard = resolve.parse_period(calls[-1].get("quarter"))
            v = resolve.verdict(stmts, events, fin, len(calls), ev, heard.end if heard else None)
            period = resolve.parse_period(last.get("deadline") or last.get("period"))
            threads.append({
                "id": f"{sym}-{n}",
                "title": t["title"],
                "kind": last["kind"],
                "metric": last["metric"],
                "scope": last.get("scope"),
                "basis": last.get("basis"),
                "period": period.label if period else (last.get("period") or "multi-year"),
                "target": resolve.fmt_range(last.get("low"), last.get("high"), last.get("unit")),
                "direction": last.get("direction"),
                "moved": any(e["type"] in MOVES for e in events),
                "events": events,
                **v,
                # what refresh_pace needs to re-read progress without the model
                **({"check": {k: last.get(k) for k in resolve.CHECK_FIELDS}} if v.get("pace") else {}),
                "statements": [{
                    "call": s["call"], "quarter": s["quarter"], "quote": s["quote"], "speaker": s["speaker"],
                    "page": s["page"], "target": resolve.fmt_range(s.get("low"), s.get("high"), s.get("unit")),
                    "period": s.get("period"), "deadline": s.get("deadline"), "condition": s.get("condition"),
                    "url": urls.get(s["call"]),
                } for s in stmts],
            })

    # The headline record counts what can fairly be scored: numbers and milestones.
    counted = [t for t in threads if t["kind"] in ("hard", "milestone")]
    status = Counter(t["status"] for t in counted)
    lq = fin.latest_quarter()
    ledger = {
        "symbol": sym, **meta,
        "built": date.today().isoformat(),
        "latest_call": calls[-1]["call"] if calls else None,
        "latest_results": fiscal.label(*lq) if lq else None,
        "headline": headline(screener_fin),
        "calls": [{"call": c["call"], "quarter": c["quarter"], "url": urls.get(c["call"]),
                   "promises": len(c["promises"])} for c in calls],
        "record": {
            "kept": status["delivered"] + status["beat"],
            "missed": status["missed"],
            "resolved": status["delivered"] + status["beat"] + status["missed"],
            "moved": sum(t["moved"] for t in counted),
            "quiet": status["gone_quiet"],
            "open": status["tracking"] + status["open"] + status["on_track"] + status["behind"]
                    + status["delayed"] + status["cant_verify"],
            "by_status": dict(status),
        },
        "threads": threads,
    }
    enrich(ledger, slug, progress)
    return ledger


def enrich(ledger: dict, slug: str, progress: Progress = _quiet) -> dict:
    """Add the numbers snapshot and the bull/bear case (both read the finished record)."""
    progress("case", "Reading the annual report, summing up the numbers and the case each way")
    try:
        ledger["business"] = business.build(ledger["name"], slug)
    except Exception:  # a missing or unreadable annual report shouldn't sink the record
        ledger["business"] = None
    ledger["snapshot"] = snapshot.build(slug)
    try:  # 3 months of closes for the header sparkline (portfolio prices also refresh daily)
        rows, asof = push.eod(ledger["symbol"], "1y")
        ledger["price"], ledger["price_asof"] = [[d, c] for d, c, _ in rows], asof
    except Exception:
        ledger["price"], ledger["price_asof"] = None, None
    ledger["case"] = snapshot.case(ledger["name"], ledger, ledger["snapshot"])
    return ledger


def research(sym: str, progress: Progress = _quiet) -> dict:
    sym = sym.upper()
    progress("company", "Finding the company")
    slug, meta = company_meta(sym)
    progress("calls", "Looking for earnings-call transcripts")
    calls = extract_calls(sym, slug, progress=progress)
    ledger = build_ledger(sym, slug, meta, calls, progress)
    LEDGERS.mkdir(parents=True, exist_ok=True)
    (LEDGERS / f"{sym}.json").write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
    progress("done", "Done")
    return ledger


def cached(sym: str) -> dict | None:
    p = LEDGERS / f"{sym.upper()}.json"
    return json.loads(p.read_text()) if p.exists() else None
