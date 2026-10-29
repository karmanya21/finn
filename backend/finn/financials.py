"""Reported numbers by fiscal period, from screener's consolidated tables.

Everything here is arithmetic on published figures, so a verdict built on it can
always show its working: which quarters, which line, what sum.
"""

from dataclasses import dataclass, field

from finn import fiscal
from finn.sources import screener


@dataclass
class Financials:
    symbol: str
    quarters: dict[tuple[int, int], dict[str, float]] = field(default_factory=dict)  # (fy, q) -> row -> value
    annual: dict[int, dict[str, float]] = field(default_factory=dict)  # fy -> row -> value
    balance: dict[int, dict[str, float]] = field(default_factory=dict)  # fy (Mar year-end) -> row -> value

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
                if v is None or not col.startswith("Mar "):
                    continue  # skip TTM and half-year columns
                target.setdefault(int(col[-2:]), {})[row] = v
    return f
