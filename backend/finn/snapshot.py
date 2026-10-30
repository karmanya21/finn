"""A quick numbers snapshot from screener's tables, and a bull/bear case built only from
it and the promise record.

The snapshot is arithmetic on reported numbers with a plain-English reading written by
code. The case is the one place a model writes prose about a company, so it's fenced in:
it sees only the record and these numbers, and every point must cite one of them or it
is dropped.
"""

import re

from finn import config
from finn.llm.client import complete_json
from finn.sources import screener

LENDER_ROWS = ("Financing Profit", "Financing Margin %")


def _ranges(page) -> dict[str, dict[str, float]]:
    out = {}
    for t in page.select("table.ranges-table"):
        cells = [c.get_text(" ", strip=True) for c in t.select("th, td")]
        if not cells:
            continue
        vals = {}
        for k, v in zip(cells[1::2], cells[2::2]):
            m = re.match(r"(-?\d+(?:\.\d+)?)%", v)
            if m:
                vals[k.rstrip(":")] = float(m.group(1))
        out[cells[0]] = vals
    return out


def _years(row: dict[str, float | None], n: int = 5) -> list[tuple[str, float]]:
    """Last n fiscal years (Mar columns), oldest first, as ('FY26', value)."""
    pts = [(f"FY{c[-2:]}", v) for c, v in row.items() if re.fullmatch(r"Mar \d{4}", c) and v is not None]
    return pts[-n:]


def build(slug: str) -> dict:
    page = screener.company_page(slug)
    pl = screener.table(page, "profit-loss")
    cf = screener.table(page, "cash-flow")
    bs = screener.table(page, "balance-sheet")
    ratios = screener.table(page, "ratios")
    rng = _ranges(page)
    lender = any(r in pl for r in LENDER_ROWS)
    snap: dict = {"lender": lender, "metrics": []}

    def add(key, label, value, unit, reading, series=None):
        snap["metrics"].append({"key": key, "label": label, "value": value, "unit": unit,
                                "reading": reading, "series": series or []})

    # Trajectory
    sales, profit = rng.get("Compounded Sales Growth", {}), rng.get("Compounded Profit Growth", {})
    def pct(v):
        return "n/a" if v is None else f"{v:g}%"
    if sales.get("5 Years") is not None:
        s5, s3, st = sales.get("5 Years"), sales.get("3 Years"), sales.get("TTM")
        trend = ("accelerating" if s3 is not None and s3 > s5 + 2 else
                 "slowing" if s3 is not None and s3 < s5 - 2 else "steady")
        add("sales_growth", "Revenue growth", s5, "% CAGR 5y",
            f"Revenue compounded {pct(s5)} a year over 5 years, {pct(s3)} over 3 ({trend}); last 12 months {pct(st)}.")
    if profit.get("5 Years") is not None:
        p5, p3, pt = profit.get("5 Years"), profit.get("3 Years"), profit.get("TTM")
        add("profit_growth", "Profit growth", p5, "% CAGR 5y",
            f"Profit compounded {pct(p5)} a year over 5 years, {pct(p3)} over 3; last 12 months {pct(pt)}.")

    # Margins. Screener's "Financing Margin %" for lenders is after provisions and swings
    # negative; it isn't what an investor means by a bank's margin, so lenders skip this.
    mrow = None if lender else pl.get("OPM %")
    if mrow:
        ys = _years(mrow)
        if len(ys) >= 2:
            first, last = ys[0][1], ys[-1][1]
            move = last - first
            word = "expanded" if move > 1 else "compressed" if move < -1 else "held"
            name = "Financing margin" if lender else "Operating margin"
            add("margin", name, last, "%", f"{name} {word} from {first:g}% ({ys[0][0]}) to {last:g}% ({ys[-1][0]}).",
                [{"x": x, "y": y} for x, y in ys])

    # Cash conversion: how much of reported profit arrived as operating cash (non-lenders)
    cfo, np_ = cf.get("Cash from Operating Activity"), pl.get("Net Profit")
    if not lender and cfo and np_:
        c5, n5 = [v for _, v in _years(cfo)], [v for _, v in _years(np_)]
        if len(c5) == len(n5) and n5 and sum(n5) > 0:
            ratio = sum(c5) / sum(n5) * 100
            word = "strong" if ratio >= 90 else "adequate" if ratio >= 70 else "weak"
            add("cash_conversion", "Cash conversion", round(ratio), "% of profit, 5y",
                f"Over 5 years, operating cash flow was {ratio:.0f}% of net profit ({word}).")

    # Debt: net of cash, the way investors and managements talk about leverage
    eq, res, bor = bs.get("Equity Capital"), bs.get("Reserves"), bs.get("Borrowings")
    cash = {}
    try:
        cash = screener.schedule(page, "Other Assets").get("Cash Equivalents", {})
    except Exception:
        pass
    if not lender and bor and res:
        ys = _years(bor)
        cs = dict(_years(cash, 10)) if cash else {}
        net = [(x, y - cs[x]) for x, y in ys if x in cs]
        e_last = (_years(eq)[-1][1] if eq else 0) + _years(res)[-1][1]
        series, label = (net, "Net debt") if len(net) == len(ys) and net else (ys, "Gross borrowings")
        if series and e_last:
            last, first = series[-1][1], series[0][1]
            ratio = last / e_last
            dir_ = "up" if last > first + abs(first) * 0.1 else "down" if last < first - abs(first) * 0.1 else "flat"
            def amt(v):
                return f"₹{-v:,.0f} cr net cash" if v < 0 and label == "Net debt" else f"₹{v:,.0f} cr"
            reading = (f"{amt(last)} in {series[-1][0]}" if last < 0 and label == "Net debt" else
                       f"{label} {amt(last)} in {series[-1][0]}, {ratio:.2f}× equity") + \
                      f"; {dir_} from {amt(first)} in {series[0][0]}."
            add("debt", label if last >= 0 else "Net cash", round(ratio, 2), "x equity", reading,
                [{"x": x, "y": y} for x, y in series])
    elif lender:
        add("debt", "Debt", None, "", "A lender borrows to lend, so debt-to-equity isn't a risk signal here; see return on equity.")

    # Returns
    rrow = ratios.get("ROCE %") or ratios.get("ROE %")
    if rrow:
        ys = _years(rrow)
        if ys:
            name = "ROCE" if "ROCE %" in ratios else "ROE"
            add("returns", name, ys[-1][1], "%",
                f"{name} {ys[-1][1]:g}% in {ys[-1][0]} (from {ys[0][1]:g}% in {ys[0][0]}).",
                [{"x": x, "y": y} for x, y in ys])
    return snap


# ---- bull / bear -------------------------------------------------------------------

CASE_SCHEMA = {
    "type": "object",
    "properties": {
        side: {"type": "array", "items": {"type": "object", "properties": {
            "figure": {"type": "string", "description": "The key number with a short label saying what it measures: 'FY26 revenue growth 11.8%', 'Net debt ₹4,358 cr', 'Op. margin 28% → 14%', 'Kept 3 of 4'. Never a bare number."},
            "point": {"type": "string", "description": "One sentence, at most 22 words, saying what the figure means"},
            "refs": {"type": "array", "items": {"type": "string"}, "description": "Promise ids (e.g. LT-3) or metric keys used"},
        }, "required": ["figure", "point", "refs"]}}
        for side in ("bull", "bear")
    },
    "required": ["bull", "bear"],
}

CASE_SYSTEM = """Write a tight bull case and bear case for an investor, using ONLY the promise record and numbers given.
- 2 or 3 points each side. Each point leads with a figure (the number it rests on, labelled so it reads on its own) and one sentence on what it means. No point without a number.
- Every point must cite in refs the promise ids and/or metric keys it rests on. No outside knowledge, no news, no industry claims.
- The promise record is evidence about management credibility: kept promises and raised guidance support the bull case; misses, cuts, delays and promises gone quiet support the bear case.
- If the evidence for a side is thin, give fewer points rather than inventing."""


def case(company: str, ledger: dict, snap: dict) -> dict:
    threads = [t for t in ledger.get("threads", []) if t["kind"] != "soft"]
    lines = []
    for t in threads[:40]:
        bits = [t["id"], t["title"], f"target {t['target']}" if t["target"] else "", t["period"], t["status"]]
        if t.get("detail"):
            bits.append(t["detail"])
        if t.get("pace"):
            p = t["pace"]
            bits.append(f"{p['so_far_label']} at {p['so_far']:.1f} ({p['status']})")
        moves = [f"{e['type']} {e['from']}→{e['to']}" for e in t["events"] if e["type"] != "reaffirmed"]
        if moves:
            bits.append("moves: " + ", ".join(moves))
        lines.append(" | ".join(b for b in bits if b))
    metrics = [f"{m['key']} | {m['label']}: {m['reading']}" for m in snap["metrics"]]
    if not lines and not metrics:
        return {"bull": [], "bear": []}
    prompt = (f"Company: {company}\n\nPROMISE RECORD (id | promise | target | period | status | detail):\n"
              + ("\n".join(lines) or "none (no earnings calls)")
              + "\n\nNUMBERS (key | reading):\n" + "\n".join(metrics))
    out = complete_json(CASE_SYSTEM, prompt, CASE_SCHEMA, model=config.MODEL_STRONG, fallback=config.MODEL_CHEAP)
    valid = {t["id"] for t in threads} | {m["key"] for m in snap["metrics"]}
    names = {t["id"]: t["title"] for t in threads} | {m["key"]: m["label"] for m in snap["metrics"]}
    clean = {}
    for side in ("bull", "bear"):
        clean[side] = [{"figure": _labelled(p.get("figure", ""), [r for r in p["refs"] if r in valid], names),
                        "point": p["point"], "refs": [r for r in p["refs"] if r in valid]}
                       for p in out.get(side, []) if any(r in valid for r in p["refs"])][:3]
    return clean


def _labelled(figure: str, refs: list[str], names: dict[str, str]) -> str:
    """A bare "11.8%" means nothing on its own: attach what it cites ("11.8% · FY26 revenue
    growth"). Attached, not prefixed: the figure may be a change in the thing, not its level."""
    if not figure or re.search(r"[A-Za-z]{3,}", figure.replace("cr", "")) or not refs or not names.get(refs[0]):
        return figure
    return f"{figure} · {names[refs[0]]}"
