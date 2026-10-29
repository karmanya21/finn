"""Survey data coverage: for each company, how many concall transcripts and
quarters of results screener has. Decides which companies can carry a promise record.

    python -m scripts.survey            # all companies
    python -m scripts.survey INFY LT    # a subset
"""

import json
import sys

from finn.config import OUT
from finn.portfolio import PORTFOLIO, RESEARCH
from finn.sources import screener


def main(symbols: list[str]) -> None:
    companies = [c for c in PORTFOLIO + RESEARCH if not symbols or c.symbol in symbols]
    rows = []
    for c in companies:
        try:
            page = screener.company_page(c.slug)
            calls = screener.concalls(page)
            qr = screener.quarterly_results(page)
            with_tx = [k.label for k in calls if k.transcript_url]
            quarters = list(next(iter(qr.values()), {}).keys())
            rows.append({"symbol": c.symbol, "transcripts": len(with_tx),
                         "latest": with_tx[:6], "quarters": len(quarters),
                         "last_quarter": quarters[-1] if quarters else None,
                         "rows": list(qr.keys())})
            print(f"{c.symbol:<11} transcripts={len(with_tx):>2}  latest={with_tx[:3]}  "
                  f"quarters={len(quarters)} last={rows[-1]['last_quarter']}")
        except Exception as e:  # keep surveying the rest
            rows.append({"symbol": c.symbol, "error": str(e)})
            print(f"{c.symbol:<11} ERROR {e}")
    (OUT / "survey.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
