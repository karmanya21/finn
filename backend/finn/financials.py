"""Reported numbers by fiscal period, from screener's consolidated tables.

Everything here is arithmetic on published figures, so a verdict built on it can
always show its working: which quarters, which line, what sum.
"""

import re
from dataclasses import dataclass, field, replace

from finn import fiscal
from finn.sources import screener


# A filing's headline rows, in screener's row names. No tax or depreciation: measures that
# need them stop at the last quarter screener has.
FILED_ROWS = {"Revenue": "Sales", "EBITDA": "Operating Profit", "PBT": "Profit before tax", "PAT": "Net Profit"}


@dataclass
class Financials:
    symbol: str
    quarters: dict[tuple[int, int], dict[str, float]] = field(default_factory=dict)  # (fy, q) -> row -> value
    annual: dict[int, dict[str, float]] = field(default_factory=dict)  # fy -> row -> value
    balance: dict[int, dict[str, float]] = field(default_factory=dict)  # fy (Mar year-end) -> row -> value
    filed: set[tuple[int, int]] = field(default_factory=set)  # quarters read from a results filing, not screener

    def with_filing(self, numbers: dict | None) -> "Financials":
        """A copy with the quarter a results filing reported, when it's the one right after
        screener's latest. Promise progress then counts the results the app already shows
        (BEL's Q2FY27 filing was out while progress still read Q1FY27)."""
        m = re.fullmatch(r"Q([1-4])FY(\d{2})", (numbers or {}).get("quarter") or "")
        if not m or "filing" not in (numbers.get("source") or "") or not self.quarters:
            return self
        fy, q = max(self.quarters)
        if (int(m.group(2)), int(m.group(1))) != ((fy, q + 1) if q < 4 else (fy + 1, 1)):
            return self
        rows = {FILED_ROWS[r["label"]]: r["value"] for r in numbers["rows"] if r["label"] in FILED_ROWS}
        nxt = (int(m.group(2)), int(m.group(1)))
        return replace(self, quarters={**self.quarters, nxt: rows}, filed=self.filed | {nxt})

    # ---- quarters -------------------------------------------------------
    def latest_quarter(self) -> tuple[int, int] | None:
        return max(self.quarters) if self.quarters else None

    def q(self, fy: int, q: int, row: str) -> float | None:
        return self.quarters.get((fy, q), {}).get(row)

    def span(self, fy: int, qs: list[int], row: str) -> float | None:
        vals = [self.q(fy, q, row) for q in qs]
        return None if any(v is None for v in vals) else sum(vals)

    def reported_quarters(self, fy: int) -> list[int]:
        return [q for q in (1, 2, 3, 4) if (fy, q) in self.quarters]

    # ---- metrics over a set of quarters -----------------------------------
    def sales(self, fy: int, qs: list[int]) -> float | None:
        return self.span(fy, qs, "Sales")

    def growth(self, fy: int, qs: list[int]) -> float | None:
        cur, prev = self.sales(fy, qs), self.sales(fy - 1, qs)
        return None if not cur or not prev else (cur / prev - 1) * 100

    def ebitda_margin(self, fy: int, qs: list[int]) -> float | None:
        op, s = self.span(fy, qs, "Operating Profit"), self.sales(fy, qs)
        return None if op is None or not s else op / s * 100

    def ebit_margin(self, fy: int, qs: list[int]) -> float | None:
        op, dep, s = (self.span(fy, qs, r) for r in ("Operating Profit", "Depreciation", "Sales"))
        return None if None in (op, dep) or not s else (op - dep) / s * 100

    def tax_rate(self, fy: int, qs: list[int]) -> float | None:
        pbt = self.span(fy, qs, "Profit before tax")
        taxes = [self.q(fy, q, "Tax %") for q in qs]
        pbts = [self.q(fy, q, "Profit before tax") for q in qs]
        if not pbt or None in taxes or None in pbts:
            return None
        return sum(t * p for t, p in zip(taxes, pbts)) / pbt

    def pat(self, fy: int, qs: list[int]) -> float | None:
        return self.span(fy, qs, "Net Profit")

    # ---- balance sheet ------------------------------------------------------
    def borrowings(self, fy: int) -> float | None:
        return self.balance.get(fy, {}).get("Borrowings")

    def net_debt(self, fy: int) -> float | None:
        """Borrowings less cash and equivalents (investments excluded: some are strategic)."""
        b = self.borrowings(fy)
        cash = self.balance.get(fy, {}).get("Cash Equivalents")
        return None if b is None or cash is None else b - cash

    def roe(self, fy: int) -> float | None:
        """Net profit / average equity (share capital + reserves)."""
        def equity(y):
            b = self.balance.get(y, {})
            return None if "Reserves" not in b else (b.get("Equity Capital") or 0) + b["Reserves"]
        np_ = self.annual.get(fy, {}).get("Net Profit")
        e1, e0 = equity(fy), equity(fy - 1)
        if np_ is None or e1 is None:
            return None
        avg = (e1 + e0) / 2 if e0 is not None else e1
        return np_ / avg * 100


FY_COLUMN = re.compile(r"^Mar (\d{4})$")  # a full fiscal year; not "Mar 2026 9m" or TTM


def load(symbol: str, slug: str | None = None) -> Financials:
    page = screener.company_page(slug or symbol)
    f = Financials(symbol)
    for row, by_col in screener.table(page, "quarters").items():
        for col, v in by_col.items():
            if v is not None and col[:3] in fiscal.MONTHS:
                f.quarters.setdefault(fiscal.screener_quarter(col), {})[row] = v
    for section, target in (("profit-loss", f.annual), ("balance-sheet", f.balance)):
        for row, by_col in screener.table(page, section).items():
            for col, v in by_col.items():
                m = FY_COLUMN.match(col)
                if v is None or not m:
                    continue  # skip TTM, half-year and "9m" columns
                target.setdefault(int(m.group(1)) % 100, {})[row] = v
    try:  # cash sits inside "Other Assets"; screener serves the breakdown separately
        cash = screener.schedule(page, "Other Assets").get("Cash Equivalents", {})
        for col, v in cash.items():
            if v is not None and (m := FY_COLUMN.match(col)):
                f.balance.setdefault(int(m.group(1)) % 100, {})["Cash Equivalents"] = v
    except Exception:
        pass  # net debt falls back to "unknown"; gross borrowings still work
    return f


def oneoff_notes(now: dict, year_ago: dict) -> list[dict]:
    """Short notes for the PAT line when a quarter's profit isn't comparable.
    now / year_ago: {"ebitda", "pbt", "pat"} in Rs crore (any may be None).
    PAT above PBT means a tax credit; PBT well above EBITDA means a one-off gain
    (other income or an exceptional item). Either inflates that quarter's profit."""
    def flags(q):
        e, b, t = q.get("ebitda"), q.get("pbt"), q.get("pat")
        out = []
        if b is not None and t is not None and b > 0 and t > b * 1.05:  # small tax credits are noise
            out.append(f"a tax credit (PAT ₹{t:,.0f} cr above PBT ₹{b:,.0f} cr)")
        if e is not None and b is not None and e > 0 and b > e * 1.15:
            out.append(f"a one-off gain (PBT ₹{b:,.0f} cr above EBITDA ₹{e:,.0f} cr)")
        return out
    notes = []
    if f := flags(year_ago):
        notes.append({"row": "PAT", "text": f"Year-ago quarter had {' and '.join(f)}, so YoY understates the business."})
    if f := flags(now):
        notes.append({"row": "PAT", "text": f"This quarter includes {' and '.join(f)}."})
    return notes
