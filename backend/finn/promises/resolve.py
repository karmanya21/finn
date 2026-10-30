"""Threads -> verdicts. Pure code: no model decides whether a promise was kept.

Verdict sources, in order of trust:
  1. financials  - reported numbers (screener consolidated), arithmetic shown;
  2. management  - their own later statements (evidence.py), labelled as self-reported;
  3. silence     - period over, nothing reported: gone quiet.
"""

import re
from dataclasses import dataclass

from finn import fiscal
from finn.financials import Financials

TOL = 0.25  # percentage points: rounding slack for growth and margins
COMPANY_SCOPES = {"company", "group", "consolidated", "overall", "total", "firm"}

PERIOD = re.compile(r"(?:(Q[1-4]|H[12])\s*)?FY\s*'?(\d{2})", re.I)


@dataclass
class Period:
    fy: int
    quarters: list[int]

    @property
    def end(self) -> tuple[int, int]:
        return self.fy, self.quarters[-1]

    @property
    def label(self) -> str:
        part = {(1, 2): "H1", (3, 4): "H2"}.get(tuple(self.quarters))
        if len(self.quarters) == 1:
            part = f"Q{self.quarters[0]}"
        return f"{part or ''}FY{self.fy:02d}"


def parse_period(s: str | None) -> Period | None:
    if not s or "multi" in s.lower():
        return None
    m = PERIOD.search(s)
    if not m:
        return None
    part, fy = (m.group(1) or "").upper(), int(m.group(2))
    qs = {"H1": [1, 2], "H2": [3, 4]}.get(part) or ([int(part[1])] if part.startswith("Q") else [1, 2, 3, 4])
    return Period(fy, qs)


def fmt_range(low, high, unit) -> str:
    unit = (unit or "").strip()
    if unit.lower() in ("count", "number", "nos", "people", "employees", "units"):
        unit = ""
    pct = unit in ("%", "percent")
    u = "%" if pct else "×" if unit.lower() in ("x", "times") else (f" {unit}" if unit else "")
    def n(x):
        return f"{x:,.0f}" if abs(x) >= 100 else f"{x:g}"
    if low is None and high is None:
        return ""
    if re.search(r"(rs|inr|₹).*crore|crore", unit, re.I):  # "Rs crore" -> ₹200–300 cr
        body = fmt_range(low, high, "")
        return re.sub(r"(\d[\d,.]*)", r"₹\1", body, count=1) + " cr"
    if high is None:
        return f"≥{n(low)}{u}"
    if low is None:
        return f"≤{n(high)}{u}"
    return f"{n(low)}{u}" if low == high else f"{n(low)}–{n(high)}{u}"


def _mid(p: dict) -> float | None:
    vals = [v for v in (p.get("low"), p.get("high")) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _order(period: Period | None) -> tuple[int, int] | None:
    return period.end if period else None


# ---- statements and changes ------------------------------------------------------

def statements(thread_ids: list[str], calls: list[dict]) -> list[dict]:
    """First statement of the thread in each call, oldest first."""
    out, seen_calls = [], set()
    for tid in sorted(thread_ids, key=lambda i: tuple(map(int, i[1:].split("p")))):
        c, j = map(int, tid[1:].split("p"))
        if c in seen_calls:
            continue
        seen_calls.add(c)
        out.append({**calls[c]["promises"][j], "call": calls[c]["call"], "call_index": c,
                    "quarter": calls[c]["quarter"]})
    return out


def changes(stmts: list[dict]) -> list[dict]:
    events = []
    for prev, cur in zip(stmts, stmts[1:]):
        ev = {"call": cur["call"], "from": fmt_range(prev.get("low"), prev.get("high"), prev.get("unit")),
              "to": fmt_range(cur.get("low"), cur.get("high"), cur.get("unit"))}
        d0 = _order(parse_period(prev.get("deadline") or prev.get("period")))
        d1 = _order(parse_period(cur.get("deadline") or cur.get("period")))
        m0, m1 = _mid(prev), _mid(cur)
        if not _comparable(prev, cur):
            m0 = m1 = None
        if cur["kind"] == "milestone" and d0 and d1 and d0 != d1:
            ev.update(type="deferred" if d1 > d0 else "advanced",
                      **{"from": fiscal.label(*d0), "to": fiscal.label(*d1)})
        elif m0 is not None and m1 is not None and abs(m1 - m0) > 1e-9:
            ev["type"] = "raised" if m1 > m0 else "lowered"
        elif m0 is not None and m1 is not None and None not in (
                prev.get("low"), prev.get("high"), cur.get("low"), cur.get("high")) and \
                (prev.get("low"), prev.get("high")) != (cur.get("low"), cur.get("high")):
            # Only real bands can narrow or widen; "20,000" vs "at least 20,000" is the
            # model reading the same words two ways, not management changing the target.
            w0 = (prev.get("high") or m0) - (prev.get("low") or m0)
            w1 = (cur.get("high") or m1) - (cur.get("low") or m1)
            ev["type"] = "narrowed" if w1 < w0 else "widened"
        else:
            ev["type"] = "reaffirmed"
        events.append(ev)
    return events


REVISION = re.compile(r"\b(revis\w*|chang\w*|new (?:\w+ ){0,3}guidance|from \S+ to|earlier|last guidance|previous\w*|narrow\w*|"
                      r"lower\w*|rais\w*|cut|reduc\w*|increas\w*|upgrad\w*|downgrad\w*)\b", re.I)


def _comparable(prev: dict, cur: dict) -> bool:
    """Can a change between these two statements be read as a raise or a cut?"""
    if _unit(prev) != _unit(cur):
        return False  # "15%" and "₹1,000 cr" aren't the same target
    p0, p1 = parse_period(prev.get("period")), parse_period(cur.get("period"))
    if p0 and p1 and (p0.fy, p0.quarters) != (p1.fy, p1.quarters):
        return False  # "1.3 GW in H2" and "2.7 GW this year" cover different spans
    revision = REVISION.search(cur.get("quote") or "")
    shared = {cur.get("low"), cur.get("high")}
    if not revision and any(v is not None and v not in shared and re.search(rf"(?<![\d.]){re.escape(f'{v:,.0f}' if v >= 1000 else f'{v:g}')}(?![\d])",
                                       cur.get("quote") or "")
           for v in (prev.get("low"), prev.get("high"))):
        # The old number is still in the new quote: restated (perhaps beside another
        # figure the model latched on to, as in "R&D 1,600 crores, capex 1,000 crores").
        return False
    bands = [(p.get("low"), p.get("high")) for p in (prev, cur)]
    if any((lo is None) != (hi is None) for lo, hi in bands) and \
            {b for pair in bands for b in pair if b is not None} & {bands[0][0], bands[0][1]} & {bands[1][0], bands[1][1]}:
        return False  # "15–17.5%" then "more than 15%": an open bound sharing an edge isn't a cut
    return True


def _unit(p: dict) -> str:
    u = (p.get("unit") or "").strip().lower()
    return "%" if u in ("%", "percent") else "cr" if "crore" in u else u


# ---- financial checks ----------------------------------------------------------

def _company_scope(p: dict) -> bool:
    s = (p.get("scope") or "company").lower()
    return any(w in s for w in COMPANY_SCOPES)


def _measure(p: dict):
    """(name, fn(fin, fy, qs) -> value) when reported financials can check this promise."""
    m, label = p.get("metric"), f"{p.get('metric_label', '')} {p.get('quote', '')}".lower()
    if not _company_scope(p) or _unit(p) not in ("%", ""):
        return None  # "2x the market" isn't a growth rate to compare with reported growth
    if m == "revenue_growth" and p.get("basis") != "constant_currency":
        return "revenue growth", lambda f, fy, qs: f.growth(fy, qs)
    if m == "ebitda_margin":
        return "EBITDA margin", lambda f, fy, qs: f.ebitda_margin(fy, qs)
    if m == "operating_margin":
        return "EBIT margin", lambda f, fy, qs: f.ebit_margin(fy, qs)
    if m == "other" and "tax rate" in label:
        return "tax rate", lambda f, fy, qs: f.tax_rate(fy, qs)
    return None


def _band_verdict(v: float, low, high) -> str:
    if low is not None and v < low - TOL:
        return "missed"
    if high is not None and v > high + TOL:
        return "beat"
    return "delivered"


def _value_verdict(v: float, p: dict, lower_is_better: bool, ended: bool) -> str:
    """A number management reported, judged in code against the promised band.
    Percentages get TOL points of slack; amounts get 5% (capex guided ~1,100, spent 1,125 = kept)."""
    low, high = p.get("low"), p.get("high")
    pct = (p.get("unit") or "").strip() in ("%", "percent", "bps")
    ref = next(x for x in (high, low) if x is not None)
    tol = TOL if pct else abs(ref) * 0.05
    if lower_is_better:
        cap = high if high is not None else low
        if v <= cap + tol:
            if not ended:
                return "on_track"
            return "beat" if low is not None and v < low - tol else "delivered"
        return "missed" if ended else "behind"
    if low is not None and v < low - tol:
        return "missed" if ended else "behind"
    if high is not None and v > high + tol:
        return "beat"
    return "delivered" if ended else "on_track"


def _pace_growth(fin: Financials, period: Period, low, high, done: list[int]) -> dict:
    rest = [q for q in period.quarters if q not in done]
    ytd = fin.growth(period.fy, done)
    out = {"so_far": ytd, "so_far_label": _span_label(period.fy, done)}
    prev_rest, prev_done, cur_done = (fin.sales(period.fy - 1, rest), fin.sales(period.fy - 1, done),
                                      fin.sales(period.fy, done))
    if low is not None and None not in (prev_rest, prev_done, cur_done) and prev_rest:
        need = ((prev_done + prev_rest) * (1 + low / 100) - cur_done) / prev_rest - 1
        out.update(needs=need * 100, needs_label=_span_label(period.fy, rest), needs_for=low)
    return out


def _span_label(fy: int, qs: list[int]) -> str:
    if qs == [1, 2]:
        return f"H1FY{fy:02d}"
    if qs == [3, 4]:
        return f"H2FY{fy:02d}"
    if len(qs) == 1:
        return f"Q{qs[0]}FY{fy:02d}"
    if qs == [1, 2, 3, 4]:
        return f"FY{fy:02d}"
    return f"Q{qs[0]}–Q{qs[-1]}FY{fy:02d}"


def _debt_pair(fin: Financials, fy: int) -> tuple[float | None, float | None, str]:
    """(this year, last year, label): net debt when cash is known, else gross borrowings."""
    n1, n0 = fin.net_debt(fy), fin.net_debt(fy - 1)
    if n1 is not None and n0 is not None:
        return n1, n0, "Net debt"
    return fin.borrowings(fy), fin.borrowings(fy - 1), "Gross borrowings"


def financial_check(p: dict, fin: Financials) -> dict | None:
    period = parse_period(p.get("period"))
    meas = _measure(p)
    latest = fin.latest_quarter()
    if not latest:
        return None

    # Multi-year revenue CAGR: can't be settled for years, but this year's growth so far
    # against the plan's annual rate is a fair, checkable pace.
    if not period and p["kind"] == "hard" and meas and meas[0] == "revenue growth":
        done = fin.reported_quarters(latest[0])
        g = fin.growth(latest[0], done) if done else None
        if g is None:
            return None
        status = {"delivered": "on_pace", "beat": "ahead", "missed": "behind"}[
            _band_verdict(g, p.get("low"), p.get("high"))]
        return {"verdict": None, "basis": "financials",
                "pace": {"so_far": g, "so_far_label": _span_label(latest[0], done), "status": status,
                         "measure": "revenue growth", "note": "against the plan's annual growth rate"}}
    if not period:
        return None

    # Directional checks we can settle with numbers.
    if p["kind"] == "directional":
        if p.get("metric") == "revenue_growth" and period.quarters == [3, 4] and latest >= period.end:
            h1, h2 = fin.growth(period.fy, [1, 2]), fin.growth(period.fy, [3, 4])
            if h1 is not None and h2 is not None:
                return {"verdict": "delivered" if h2 > h1 else "missed", "basis": "financials",
                        "actual": h2, "detail": f"H2 growth {h2:+.1f}% vs H1 {h1:+.1f}%"}
        if p.get("metric") == "debt" and p.get("direction") == "down" and latest >= (period.fy, 4):
            b1, b0, label = _debt_pair(fin, period.fy)
            if b1 is not None and b0 is not None:
                return {"verdict": "delivered" if b1 < b0 else "missed", "basis": "financials",
                        "detail": f"{label} ₹{b0:,.0f} cr → ₹{b1:,.0f} cr"}
        return None

    # "Reduce net debt by Rs 200-300 crore this year": borrowings at year end settle it.
    if p["kind"] == "hard" and p.get("metric") == "debt" and p.get("direction") == "down" \
            and "crore" in (p.get("unit") or "").lower() and len(period.quarters) == 4:
        if latest < (period.fy, 4):
            return None
        b1, b0, label = _debt_pair(fin, period.fy)
        if b1 is None or b0 is None:
            return None
        cut = b0 - b1
        low = p.get("low") if p.get("low") is not None else p.get("high")
        return {"verdict": "delivered" if low is not None and cut >= low else "missed",
                "basis": "financials", "actual": cut,
                "detail": f"{label} ₹{b0:,.0f} cr → ₹{b1:,.0f} cr "
                          f"({'down' if cut >= 0 else 'up'} ₹{abs(cut):,.0f} cr)"}

    if p["kind"] != "hard" or not meas:
        return None
    name, fn = meas
    done = [q for q in period.quarters if (period.fy, q) <= latest and (period.fy, q) in fin.quarters]
    if not done:
        return None
    v = fn(fin, period.fy, done)
    if v is None:
        return None
    if len(done) == len(period.quarters):
        return {"verdict": _band_verdict(v, p.get("low"), p.get("high")), "basis": "financials",
                "actual": v, "detail": f"Reported {name} {v:.1f}% for {_span_label(period.fy, done)}"}
    pace = {"so_far": v, "so_far_label": _span_label(period.fy, done)}
    if name == "revenue growth":
        pace = _pace_growth(fin, period, p.get("low"), p.get("high"), done)
    pace["status"] = {"delivered": "on_pace", "beat": "ahead", "missed": "behind"}[
        _band_verdict(pace["so_far"], p.get("low"), p.get("high"))]
    pace["measure"] = name
    return {"verdict": None, "basis": "financials", "pace": pace}


def reference(p: dict, fin: Financials) -> str | None:
    """Context for promises we can't check exactly (constant-currency guidance)."""
    period = parse_period(p.get("period"))
    if p.get("metric") == "revenue_growth" and p.get("basis") == "constant_currency" and period:
        latest = fin.latest_quarter()
        done = [q for q in period.quarters if latest and (period.fy, q) <= latest]
        g = fin.growth(period.fy, done) if done else None
        if g is not None:
            return (f"Guided in constant currency; reported ₹ revenue grew {g:+.1f}% in "
                    f"{_span_label(period.fy, done)}, which includes currency moves, so it isn't comparable.")
    return None


# ---- the verdict ---------------------------------------------------------------

def needs_evidence(p: dict, fin: Financials) -> bool:
    """Ask later calls when the financials can't give a final verdict. Constant-currency
    guidance included: management usually reports the cc outcome on the next call."""
    fc = financial_check(p, fin)
    return p["kind"] != "soft" and not (fc and fc.get("verdict"))


def verdict(stmts: list[dict], events: list[dict], fin: Financials, n_calls: int,
            evidence: dict | None) -> dict:
    p = stmts[-1]
    period = parse_period(p.get("deadline") or p.get("period"))
    latest = fin.latest_quarter()
    ended = bool(period and latest and latest >= period.end)
    silent_calls = n_calls - 1 - p["call_index"]

    if p["kind"] == "soft":
        return {"status": "said", "basis": None}

    fc = financial_check(p, fin)
    if fc and fc.get("verdict"):
        return {"status": fc["verdict"], "basis": "financials", "detail": fc["detail"]}

    progress_claim = evidence and evidence.get("status") in ("on_track", "behind")
    if fc and fc.get("pace") and progress_claim:
        evidence = None  # reported numbers beat management's own read of its progress

    if evidence and evidence.get("status") not in (None, "no_update"):
        s = {"exceeded": "beat", "delayed": "delayed"}.get(evidence["status"], evidence["status"])
        if evidence.get("value") is not None and (p.get("low") is not None or p.get("high") is not None) \
                and s in ("delivered", "beat", "missed", "on_track", "behind"):
            s = _value_verdict(evidence["value"], p, bool(evidence.get("lower_is_better")), ended)
        if not period and s in ("delivered", "beat"):
            s = "on_track"  # a five-year target can't be kept after one year
        if s == "on_track" and ended:
            s = "open"
        out = {"status": s, "basis": "management", "detail": evidence.get("reported"),
               "evidence": {k: evidence.get(k) for k in ("quote", "call", "new_deadline")}}
        if fc and fc.get("pace"):
            out["pace"] = fc["pace"]
        return out

    ref = reference(p, fin)
    if ref:
        return {"status": "cant_verify", "basis": None, "detail": ref}

    if fc and fc.get("pace"):
        return {"status": "tracking", "basis": "financials", "pace": fc["pace"]}

    # Silence. Only numbers and milestones can "go quiet"; a passing directional remark
    # whose period ended unremarked has merely lapsed, which isn't worth an accusation.
    quiet = "gone_quiet" if p["kind"] in ("hard", "milestone") else "lapsed"
    if ended and silent_calls >= 1:
        return {"status": quiet, "basis": None,
                "detail": f"Period ended; no update in later calls (last said {p['call']})"}
    # Dropped: repeated, then dropped. A one-off remark that hasn't come due yet stays open;
    # it may simply be restated in other words, and silence isn't a verdict until it's due.
    if silent_calls >= 2 and len(stmts) >= 2:
        return {"status": quiet, "basis": None,
                "detail": f"Said {len(stmts)} times, then not mentioned since {p['call']}"}
    return {"status": "open", "basis": None}
