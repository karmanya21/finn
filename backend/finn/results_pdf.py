"""Headline results read from the company's own results filing, for the day or two before
screener tabulates a new quarter.

The model only transcribes the statement's lines as printed (this quarter, last quarter,
a year ago). Code checks every number appears in the filing, converts the unit to crore,
and does all the arithmetic: EBITDA = PBT + finance costs + depreciation - other income.
"""

import re

from finn.llm.client import complete_json
from finn.promises.extract import _numbers
from finn.sources import bse

LINES = ["revenue", "other_income", "finance_costs", "depreciation", "pbt", "pat"]
COLS = ["current", "previous_quarter", "year_ago"]
TO_CRORE = {"crore": 1, "lakh": 0.01, "million": 0.1, "billion": 100, "thousand": 0.0001}

SCHEMA = {
    "type": "object",
    "properties": {
        "quarter_end": {"type": ["string", "null"], "description": "Quarter-end date of the 'current' column, e.g. 30-Sep-2026"},
        "unit": {"type": "string", "enum": list(TO_CRORE), "description": "The unit the statement is printed in"},
        "basis": {"type": "string", "enum": ["consolidated", "standalone"]},
        **{line: {"type": "object", "properties": {c: {"type": ["number", "null"]} for c in COLS},
                  "required": COLS} for line in LINES},
    },
    "required": ["quarter_end", "unit", "basis", *LINES],
}

SYSTEM = """You transcribe a quarterly results statement filed by an Indian listed company.
Use the CONSOLIDATED statement if the filing has both; otherwise standalone.
For each line give three columns exactly as printed: the quarter just ended ("current"), the preceding quarter, and the same quarter a year ago. Not half-year or year-to-date columns.
- revenue: Revenue from operations (not total income).
- other_income, finance_costs, depreciation (depreciation and amortisation expense), pbt (profit before tax), pat (net profit for the period).
Copy numbers as printed, without converting units. Use null if a line or column isn't there. Do not compute anything."""


def _agrees_with_history(rev: dict, quarter: str, fin) -> bool:
    import re
    from finn import fiscal
    m = re.match(r"Q([1-4])FY(\d{2})", quarter)
    if not m:
        return False
    q, fy = int(m.group(1)), int(m.group(2))
    prev = (fy, q - 1) if q > 1 else (fy - 1, 4)
    checks = [(rev["previous_quarter"], fin.q(*prev, "Sales")), (rev["year_ago"], fin.q(fy - 1, q, "Sales"))]
    known = [(a, b) for a, b in checks if a is not None and b]
    return bool(known) and all(abs(a / b - 1) <= 0.03 for a, b in known)


def _pct(a, b):
    return None if a is None or not b or b <= 0 else (a / b - 1) * 100


def _statement_pages(pdfs: list[str]) -> str:
    """The pages that hold the results statement, consolidated first."""
    scored = []
    for url in pdfs[:3]:
        try:
            pages = bse.pdf_pages(url)
        except Exception:
            continue
        for i, t in enumerate(pages):
            low = t.lower()
            if "revenue from operations" not in low or "profit before" not in low:
                continue
            scored.append((("consolidated" in low) * 2 + ("quarter ended" in low or "three months ended" in low), url, i, t))
    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(f"[{url.rsplit('/', 1)[-1]} page {i + 1}]\n{t[:7000]}" for _, url, i, t in scored[:2])


def from_filing(company: str, pdfs: list[str], quarter: str, fin=None) -> dict | None:
    """A headline dict in the same shape as research.headline(), or None if it can't be read
    reliably. With `fin` (screener's history), last quarter's and the year-ago quarter's
    revenue must match screener's within 3%: that catches the wrong basis or wrong column."""
    text = _statement_pages(pdfs)
    if len(text.strip()) < 200:
        return None
    out = complete_json(SYSTEM, f"Company: {company}\n\n{text}", SCHEMA)

    printed = set(_numbers(text))
    # Some statements print thousands separators as dots ("2.56.921" = 2,56,921).
    printed |= {float(m.replace(".", "")) for m in re.findall(r"\b\d{1,3}(?:\.\d{2,3})+\b", text)}
    k = TO_CRORE[out["unit"]]
    vals: dict[str, dict[str, float | None]] = {}
    for line in LINES:
        vals[line] = {}
        for c in COLS:
            v = out[line].get(c)
            # every figure must be printed in the filing (sign aside; the model may round
            # "2,56,921.15" to 256921), or it's dropped
            ok = v is not None and any(abs(abs(v) - n) <= max(0.51, abs(v) * 0.0005) for n in printed)
            vals[line][c] = v * k if ok else None

    def ebitda(c):
        parts = [vals[x][c] for x in ("pbt", "finance_costs", "depreciation", "other_income")]
        return None if None in parts else parts[0] + parts[1] + parts[2] - parts[3]

    rows = []
    for label, get in (("Revenue", lambda c: vals["revenue"][c]), ("EBITDA", ebitda),
                       ("PBT", lambda c: vals["pbt"][c]), ("PAT", lambda c: vals["pat"][c])):
        cur = get("current")
        if cur is None:
            continue
        rows.append({"label": label, "value": cur, "qoq": _pct(cur, get("previous_quarter")),
                     "yoy": _pct(cur, get("year_ago")), "prev_yoy": None})
    if not any(r["label"] == "Revenue" for r in rows):
        return None
    if fin is not None and not _agrees_with_history(vals["revenue"], quarter, fin):
        return None

    def margin(c):
        e, r = ebitda(c), vals["revenue"][c]
        return None if e is None or not r else e / r * 100
    m = {"label": "EBITDA margin", "now": margin("current"), "year_ago": margin("year_ago"),
         "prev": margin("previous_quarter")}
    def profit(c):
        return {"ebitda": ebitda(c), "pbt": vals["pbt"][c], "pat": vals["pat"][c]}
    from finn.financials import oneoff_notes
    return {"quarter": quarter, "rows": rows, "notes": oneoff_notes(profit("current"), profit("year_ago")),
            "margin": m if m["now"] is not None else None, "unit": "₹ cr",
            "source": f"the company's results filing (statutory {out['basis']}); screener hasn't tabulated this quarter yet"}
