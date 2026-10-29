"""Indian fiscal calendar: FY27 runs Apr 2026 - Mar 2027; Q1 = Apr-Jun."""

import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def fy_of(d: date) -> int:
    """Two-digit FY containing a date: 2026-10-29 -> 27."""
    return (d.year + 1 if d.month >= 4 else d.year) % 100


def quarter_of(d: date) -> tuple[int, int]:
    """(fy, q) containing a date."""
    return fy_of(d), ((d.month - 4) % 12) // 3 + 1


def parse_label(label: str) -> date:
    """Screener's 'Oct 2026' -> date(2026, 10, 1)."""
    m, y = label.split()
    return date(int(y), MONTHS[m[:3]], 1)


def reported_quarter(call: date) -> tuple[int, int]:
    """The quarter whose results a call discusses: the one that ended before the call month."""
    fy, q = quarter_of(call)
    # Calls happen in the first ~2 months after quarter end.
    return (fy, q - 1) if q > 1 else (fy - 1, 4)


def label(fy: int, q: int | None = None) -> str:
    return f"Q{q}FY{fy:02d}" if q else f"FY{fy:02d}"


def screener_quarter(col: str) -> tuple[int, int]:
    """Screener's quarterly column 'Sep 2026' (quarter-end month) -> (27, 2)."""
    return quarter_of(parse_label(col))


PERIOD = re.compile(r"^(?:(Q[1-4]|H[12]))?FY(\d{2})$")
