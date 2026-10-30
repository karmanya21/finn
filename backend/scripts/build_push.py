"""Build the push side's data for the app: filings digest (30 days) and EOD prices.

    python -m scripts.build_push            # both
    python -m scripts.build_push prices     # just prices

Run after build_all so filings can be read against each company's promise record.
"""

import json
import sys

from finn import push
from finn.portfolio import PORTFOLIO

from scripts.build_ledger import WEB_DATA


def main(what: list[str]) -> None:
    if not what or "prices" in what:
        old = WEB_DATA / "prices.json"
        prices = push.build_prices(json.loads(old.read_text()) if old.exists() else None)
        push.write(WEB_DATA / "prices.json", prices)
        print(f"prices: {len(prices['series'])} companies")
    if not what or "filings" in what:
        ledgers = {}
        for c in PORTFOLIO:
            p = WEB_DATA / f"{c.symbol}.json"
            if p.exists():
                ledgers[c.symbol] = json.loads(p.read_text())
        old = WEB_DATA / "filings.json"
        digest = push.build_digest(ledgers, previous=json.loads(old.read_text()) if old.exists() else None)
        if len(digest["failed"]) > len(PORTFOLIO) // 2:
            # The source is down or blocking us: an empty digest would read as "nothing
            # happened". Keep the last good one and say so loudly (an Actions annotation).
            print(f"::error::Filings fetch failed for {len(digest['failed'])} of {len(PORTFOLIO)} companies; "
                  "kept the previous digest.")
            (WEB_DATA.parent.parent.parent / ".filings-stale").write_text(", ".join(digest["failed"]))
            return
        push.write(WEB_DATA / "filings.json", digest)
        n = len(digest["items"])
        print(f"filings: {n} items, {sum(not i['routine'] for i in digest['items'])} material")
    from scripts import audit  # every build ends with the invariant checks
    audit.main([])


if __name__ == "__main__":
    main(sys.argv[1:])
