"""Can this machine read BSE's announcements feed? Run it on a new server before relying on it.

    python -m scripts.check_bse      # prints OK (exit 0) or BLOCKED (exit 1)

Uses the pipeline's own HTTP client on purpose: BSE refuses some clients (Python's urllib,
curl) that it accepts from `requests`, so only the real code gives a trustworthy answer.
"""

import sys
from datetime import date, timedelta

from finn.sources import bse


def main() -> None:
    until = date.today()
    since = until - timedelta(days=7)
    try:
        r = bse.SESSION.get(bse.API, timeout=30, params={
            "pageno": 1, "strCat": -1, "strPrevDate": f"{since:%Y%m%d}", "strScrip": "500209",
            "strSearch": "P", "strToDate": f"{until:%Y%m%d}", "strType": "C", "subcategory": -1})
        r.raise_for_status()
        rows = r.json().get("Table") or []
    except Exception as e:
        print(f"BLOCKED: {e}")
        sys.exit(1)
    print(f"OK: BSE answered with {len(rows)} Infosys filings from the last 7 days.")


if __name__ == "__main__":
    main()
