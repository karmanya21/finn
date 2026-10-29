"""Download and extract the latest N concall transcripts per company.

    python -m scripts.fetch_transcripts            # spike companies, 6 calls each
    python -m scripts.fetch_transcripts INFY -n 8
"""

import argparse

from finn.portfolio import SPIKE, by_symbol
from finn.sources import screener


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", default=SPIKE)
    ap.add_argument("-n", type=int, default=6)
    args = ap.parse_args()
    for sym in args.symbols:
        company = by_symbol(sym)
        page = screener.company_page(company.slug if company else sym)
        calls = [c for c in screener.concalls(page) if c.transcript_url][: args.n]
        for call in calls:
            path = screener.transcript_text(sym, call)
            print(f"{sym:<10} {call.label:<9} {path.stat().st_size // 1024:>4} KB  {path.name}")


if __name__ == "__main__":
    main()
