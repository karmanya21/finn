"""Build the push side's data: the filings digest and end-of-day prices for the portfolio.

The app filters the digest by window (24h / 7d / 30d) and evaluates price and volume
alerts against thresholds the user sets, so both files carry the raw material, not
pre-baked alerts.
"""

import json
import re
import time
from datetime import datetime

import requests

from finn import fiscal
from finn.filings import (LABEL, ROUTINE, WEIGHT, classify, related_promises, results_promises,
                          speaker_promises, summarise)
from finn.portfolio import PORTFOLIO
from finn.sources import bse

# The roles that alert: who runs the company and who signs its numbers.
SENIOR = re.compile(r"\b(CEO|CFO|MD|Managing Director|Chief Executive Officer|Chief Financial Officer|"
                    r"(?<!Vice )Chair(?:man|person|woman)?)\b", re.I)
# "September 30, 2026", "30th September, 2026", "30.09.2026"
QUARTER_END = re.compile(r"(March|June|Sept?(?:ember)?|December)\s*,?\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?(\d{4})", re.I)


def _results_quarter(a: dict) -> str:
    """The quarter a results filing reports: from its subject, else from when it was filed
    (results land within weeks of quarter end, so the filing date pins the quarter)."""
    m = QUARTER_END.search(f"{a['subject']} {a['headline']}")
    if m:
        return fiscal.label(*fiscal.screener_quarter(f"{m.group(1)[:3].title()} {m.group(2)}"))
    return fiscal.label(*fiscal.reported_quarter(datetime.fromisoformat(a["at"]).date()))


def digest_company(c, ledger: dict | None, days: int = 30, summarise_min: int = 35) -> list[dict]:
    code = bse.scrip_code(c.slug)
    if not code:
        return []
    items = bse.announcements(code, days)
    classify(c.name, items)
    out = []
    for a in items:
        kind = a["type"]
        score = WEIGHT[kind] + (5 if a["critical"] else 0)
        if kind == "earnings_call" and re.search(r"transcript", f"{a['subcategory']} {a['subject']}", re.I):
            score = 35  # a new transcript is where FINN re-reads the promises; notices and audio links aren't
        item = {"id": f"{c.symbol}-{a['id']}", "symbol": c.symbol, "name": c.name, "at": a["at"],
                "type": kind, "label": LABEL[kind], "subject": a["subject"], "headline": a["headline"],
                "pdf": a["pdf"], "summary": None, "people": [], "promises": [], "numbers": None,
                "senior": False}

        if kind == "results" and ledger:
            q = _results_quarter(a)
            h = ledger.get("headline")
            if h and q and h["quarter"] == q:
                item["numbers"] = h
            item["promises"] = results_promises(ledger)
            item["summary"] = f"{q} results" if q else None
        elif score >= summarise_min:
            s = summarise(c.name, a)
            if s:
                item["summary"] = s["summary"]
                if s["category"] != kind and s["category"] in WEIGHT and kind == "press_release":
                    kind = item["type"] = s["category"]
                    item["label"] = LABEL[kind]
                    score = WEIGHT[kind]
                if s.get("downgrade"):
                    score += 20
                for p in s.get("people", []):
                    leaving = p["change"] in ("resigned", "retired", "ceased")
                    item["people"].append({**p, "promises": speaker_promises(p["name"], ledger or {}) if leaving else []})
                if kind == "management_change":
                    # Company-level roles only; "SVP & Chief Executive - <unit>" is a promotion.
                    senior = any(SENIOR.search(p["role"]) and "vice president" not in p["role"].lower()
                                 and p["change"] != "redesignated" for p in s.get("people", []))
                    item["senior"] = senior
                    score += 10 if senior else -20
            if ledger:
                item["promises"] = related_promises({**a, "summary": item["summary"]}, ledger)

        if item["promises"] and kind != "results":
            score += 10  # touches something management promised
        if any(p["promises"] for p in item["people"]):
            score += 10  # the person leaving made promises we're tracking
        item["pdf"] = bse.RESOLVED.get(item["pdf"], item["pdf"])
        item["importance"] = min(score, 100)
        item["routine"] = item["importance"] < ROUTINE
        out.append(item)
    return _fold_repeats(_fold_results(out))


def _fold_repeats(items: list[dict]) -> list[dict]:
    """The same event is often filed twice (a Reg 30 disclosure and a press release, or a
    correction). Same company, same type, within two days, naming the same people or with
    the same subject: keep the most important one."""
    keep: list[dict] = []
    for i in sorted(items, key=lambda x: -x["importance"]):
        names = {p["name"].lower().replace(".", "").replace("mr ", "").replace("ms ", "").strip() for p in i["people"]}
        dup = next((k for k in keep if k["type"] == i["type"] and k["type"] != "unclassified"
                    and abs((datetime.fromisoformat(k["at"]) - datetime.fromisoformat(i["at"])).days) <= 2
                    and ((names and names & {p["name"].lower().replace(".", "").replace("mr ", "").replace("ms ", "").strip()
                                             for p in k["people"]})
                         or (not names and k["subject"] == i["subject"] and k["type"] in ("dividend", "management_change")))), None)
        if dup is None:
            keep.append(i)
        elif i["pdf"]:
            dup.setdefault("also", []).append({"label": "Also filed", "pdf": i["pdf"]})
    return sorted(keep, key=lambda x: x["at"], reverse=True)


def _fold_results(items: list[dict]) -> list[dict]:
    """One results card per quarter. Companies file the results, the board outcome, the
    investor deck and a press release within a day or two; the first card carries the
    numbers, the rest become its 'also filed' links."""
    res = sorted([i for i in items if i["type"] == "results"], key=lambda i: i["at"])
    if len(res) < 2:
        return items
    groups: list[list[dict]] = []
    for i in res:
        if groups and (datetime.fromisoformat(i["at"]) - datetime.fromisoformat(groups[-1][0]["at"])).days <= 3:
            groups[-1].append(i)
        else:
            groups.append([i])
    drop = set()
    for g in groups:
        lead = max(g, key=lambda i: (i["numbers"] is not None, "Financial Results" in i["subject"], i["at"]))
        lead["also"] = [{"label": _doc_kind(i["subject"]), "pdf": i["pdf"]} for i in g if i is not lead and i["pdf"]]
        # Headline the card with whichever summary in the group actually states figures.
        with_numbers = [i["summary"] for i in g if i["summary"] and re.search(r"\d", i["summary"])
                        and re.search(r"revenue|profit|PAT|EBITDA|turnover|income", i["summary"], re.I)]
        if with_numbers:
            lead["summary"] = with_numbers[0]
        drop |= {i["id"] for i in g if i is not lead}
    return [i for i in items if i["id"] not in drop]


def _doc_kind(subject: str) -> str:
    s = subject.lower()
    return ("Investor presentation" if "presentation" in s else "Press release" if "press" in s
            else "Board outcome" if "outcome" in s else "Auditor's report" if "auditor" in s else "Results filing")


def build_digest(ledgers: dict[str, dict], days: int = 30) -> dict:
    items, failed = [], []
    for c in PORTFOLIO:
        try:
            got = digest_company(c, ledgers.get(c.symbol), days)
            items += got
            print(f"  filings {c.symbol:<11} {len(got):>3}  material {sum(not i['routine'] for i in got)}", flush=True)
        except Exception as e:
            failed.append(c.symbol)
            print(f"  filings {c.symbol:<11} FAILED {type(e).__name__}: {str(e)[:100]}", flush=True)
    items.sort(key=lambda i: i["at"], reverse=True)
    return {"built": datetime.now().isoformat(timespec="seconds"), "days": days, "items": items,
            "failed": failed}


# ---- prices ------------------------------------------------------------------------

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


def eod(symbol: str, days: str = "6mo") -> list[list]:
    """[[YYYY-MM-DD, close, volume], ...] oldest first, from Yahoo's chart API (NSE)."""
    r = requests.get(YAHOO.format(requests.utils.quote(f"{symbol}.NS")),
                     params={"range": days, "interval": "1d"}, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for ts, close, vol in zip(res["timestamp"], q["close"], q["volume"]):
        if close is None or vol is None:
            continue
        rows.append([datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), round(close, 2), int(vol)])
    return rows


def build_prices() -> dict:
    out = {}
    for c in PORTFOLIO:
        try:
            out[c.symbol] = eod(c.symbol)
        except Exception as e:
            print(f"  prices {c.symbol:<11} FAILED {e}", flush=True)
        time.sleep(0.3)
    return {"built": datetime.now().isoformat(timespec="seconds"), "source": "Yahoo Finance (NSE, end of day)",
            "series": out}


def write(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
