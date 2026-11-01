"""Build the push side's data: the filings digest and end-of-day prices for the portfolio.

The app filters the digest by window (24h / 7d / 30d) and evaluates price and volume
alerts against thresholds the user sets, so both files carry the raw material, not
pre-baked alerts.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

import requests

from finn import financials, fiscal, results_pdf
from finn.filings import (LABEL, ROUTINE, WEIGHT, classify, regulatory_tone, related_promises,
                          results_promises, speaker_promises, summarise)
from finn.portfolio import PORTFOLIO
from finn.sources import bse

# The roles that alert: who runs the company and who signs its numbers.
DEBT_PAPER = re.compile(r"\bNCDs?\b|non[- ]convertible|debentures?|commercial paper|\bbonds?\b|"
                        r"tier[- ]ii|perpetual|subordinated", re.I)
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
                "senior": False, "tone": None}

        if kind == "results" and ledger:
            q = _results_quarter(a)
            h = ledger.get("headline")
            if h and q and h["quarter"] == q:
                item["numbers"] = h
            item["promises"] = results_promises(ledger)
            item["summary"] = f"{q} results" if q else None
            item["quarter"] = q
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

        # A lender raises debt every week; an NCD or CP allotment is funding, not news.
        lender = bool(((ledger or {}).get("snapshot") or {}).get("lender"))
        if kind == "fund_raise" and routine_debt(f"{a['subject']} {a['headline']} {item['summary'] or ''}", lender):
            kind = item["type"] = "unclassified"
            item["label"] = LABEL[kind]
            score = WEIGHT[kind]

        if item["promises"] and kind != "results":
            score += 10  # touches something management promised
        if any(p["promises"] for p in item["people"]):
            score += 10  # the person leaving made promises we're tracking
        if kind == "regulatory":  # read the subject and summary, not the BSE headline (often boilerplate)
            item["tone"] = regulatory_tone(f"{a['subject']} {item['summary'] or ''}")
        item["pdf"] = bse.RESOLVED.get(item["pdf"], item["pdf"])
        item["importance"] = min(score, 100)
        item["routine"] = item["importance"] < ROUTINE
        out.append(item)
    folded = _fold_results(out)
    for i in folded:  # screener can lag a new quarter by a day or two: read the filing itself
        if i["type"] == "results" and not i["numbers"] and i.get("quarter"):
            try:
                pdfs = [x for x in [i["pdf"], *[d["pdf"] for d in i.get("also", [])]] if x]
                i["numbers"] = results_pdf.from_filing(c.name, pdfs, i["quarter"], financials.load(c.symbol, c.slug))
            except Exception as e:
                print(f"  results {c.symbol}: couldn't read the filing ({type(e).__name__})", flush=True)
    return _fold_repeats(folded)


def _fold_repeats(items: list[dict]) -> list[dict]:
    """The same event is often filed twice (a Reg 30 disclosure and a press release, or a
    correction). Same company, same type, within two days, naming the same people or with
    the same subject, or (one filing per document: the board outcome and the record-date
    notice) the same figures in the summary: keep the most important one."""
    keep: list[dict] = []
    for i in sorted(items, key=lambda x: -x["importance"]):
        names = {p["name"].lower().replace(".", "").replace("mr ", "").replace("ms ", "").strip() for p in i["people"]}
        dup = next((k for k in keep if k["type"] == i["type"] and k["type"] != "unclassified"
                    and abs((datetime.fromisoformat(k["at"]) - datetime.fromisoformat(i["at"])).days) <= 2
                    and ((names and names & {p["name"].lower().replace(".", "").replace("mr ", "").replace("ms ", "").strip()
                                             for p in k["people"]})
                         or (not names and k["subject"] == i["subject"] and k["type"] in ("dividend", "management_change"))
                         or _same_figures(k, i))), None)
        if dup is None:
            keep.append(i)
        elif i["pdf"]:
            dup.setdefault("also", []).append({"label": "Also filed", "pdf": i["pdf"]})
    return sorted(keep, key=lambda x: x["at"], reverse=True)


def _same_figures(a: dict, b: dict) -> bool:
    """Two summaries of one event: the same amounts (₹11,700 cr QIP and ₹5,800 cr
    warrants), or mostly the same words (an NCLT order filed as the order and as a Reg 30
    notice), whatever the subject lines say."""
    sa, sb = a.get("summary") or "", b.get("summary") or ""
    fa, fb = ({float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", x)} for x in (sa, sb))
    wa, wb = (set(re.findall(r"[a-z]{4,}", x.lower())) for x in (sa, sb))
    return (len(fa) >= 2 and fa == fb) or (bool(wa and wb) and len(wa & wb) / len(wa | wb) > 0.6)


def routine_debt(text: str, lender: bool) -> bool:
    """A lender raises debt every week (NCDs, CP, bonds): funding, not news. For anyone
    else only commercial paper is routine; a bond or NCD issue can matter."""
    paper = DEBT_PAPER.search(text)
    return bool(paper) and (lender or bool(re.search(r"commercial paper", paper.group(0), re.I)))


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


def build_digest(ledgers: dict[str, dict], days: int = 30, previous: dict | None = None) -> dict:
    """A company that fails (source down, model quota) keeps its filings from the last build
    that are still inside the window, rather than vanishing: Trent lost all 9 when the
    Gemini allowance ran out mid-run."""
    items, failed = [], []
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    for c in PORTFOLIO:
        try:
            got = digest_company(c, ledgers.get(c.symbol), days)
            items += got
            print(f"  filings {c.symbol:<11} {len(got):>3}  material {sum(not i['routine'] for i in got)}", flush=True)
        except Exception as e:
            failed.append(c.symbol)
            kept = [i for i in (previous or {}).get("items", []) if i["symbol"] == c.symbol and i["at"] >= since]
            items += kept
            print(f"  filings {c.symbol:<11} FAILED {type(e).__name__}: {str(e)[:100]}; kept {len(kept)} from last build",
                  flush=True)
    items.sort(key=lambda i: i["at"], reverse=True)
    return {"built": datetime.now().isoformat(timespec="seconds"), "days": days, "items": items,
            "failed": failed}


# ---- prices ------------------------------------------------------------------------

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


def eod(symbol: str, days: str = "6mo") -> tuple[list[list], int | None]:
    """([[YYYY-MM-DD, close, volume], ...] oldest first, time of the last trade as a Unix
    timestamp) from Yahoo's chart API (NSE). The last row is intraday if the market was open."""
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
        rows.append([datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d"), round(close, 2), int(vol)])
    return adjust_splits(rows), res.get("meta", {}).get("regularMarketTime")


SPLIT_RATIOS = (1 / 2, 2 / 3, 1 / 3, 3 / 4, 1 / 4, 1 / 5, 4 / 5, 1 / 10)


def adjust_splits(rows: list[list]) -> list[list]:
    """Yahoo sometimes leaves an NSE split or bonus unadjusted: Trent fell from 4,279 to 2,865
    (exactly 2/3, a 1:2 bonus) on 1 Jan 2026 on ordinary volume. A drop of 20%+ to a simple
    split fraction, on volume under 3x its 20-day median, rescales everything before it."""
    out = [list(r) for r in rows]
    for i in range(1, len(out)):
        r = out[i][1] / out[i - 1][1] if out[i - 1][1] else 1
        if r > 0.8:
            continue
        vols = sorted(x[2] for x in out[max(0, i - 20):i])
        if vols and out[i][2] > 3 * vols[len(vols) // 2]:
            continue  # heavy volume: a real crash, not paperwork
        f = next((f for f in SPLIT_RATIOS if abs(r / f - 1) < 0.01), None)
        if f:
            for row in out[:i]:
                row[1], row[2] = round(row[1] * f, 2), int(row[2] / f)
    return out


def build_prices(previous: dict | None = None) -> dict:
    """A company whose fetch fails keeps its last series (and says how old it is) rather
    than vanishing from the app: Yahoo 404'd Eternal for one run."""
    out, asof = {}, {}
    previous = previous or {}
    for c in PORTFOLIO:
        try:
            out[c.symbol], asof[c.symbol] = eod(c.symbol, "1y")  # a year, for the company page chart
        except Exception as e:
            print(f"  prices {c.symbol:<11} FAILED {e}", flush=True)
            if c.symbol in previous.get("series", {}):
                out[c.symbol] = previous["series"][c.symbol]
                asof[c.symbol] = previous.get("asof", {}).get(c.symbol)
        time.sleep(0.3)
    return {"built": datetime.now(IST).isoformat(timespec="seconds"), "source": "Yahoo Finance (NSE)",
            "series": out, "asof": asof}


def write(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
