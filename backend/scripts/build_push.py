"""Build the push side's data for the app: filings digest (30 days) and EOD prices.

    python -m scripts.build_push            # both
    python -m scripts.build_push prices     # just prices

Run after build_all so filings can be read against each company's promise record.
"""

import json
import sys

from finn import financials, push, research
from finn.portfolio import PORTFOLIO, by_symbol
from finn.promises import resolve

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
        refresh_progress(ledgers)
    from scripts import audit  # every build ends with the invariant checks
    audit.main([])


def refresh_progress(ledgers: dict[str, dict]) -> None:
    """Results filed today should move promise progress today, not at the next full rebuild
    (which this daily job doesn't run). No model calls: only the pace on running promises."""
    changed = False
    for sym, l in ledgers.items():
        nums = research.filed_results(sym)
        if not nums or nums["quarter"] == l.get("latest_results") or not any(t.get("check") for t in l["threads"]):
            continue
        try:
            c = by_symbol(sym)
            fin = financials.load(sym, c.slug if c else None).with_filing(nums)
        except Exception as e:
            print(f"  progress {sym:<11} FAILED {type(e).__name__}: {str(e)[:100]}", flush=True)
            continue
        if not fin.filed:
            continue  # screener caught up, or the filing isn't the next quarter
        n = resolve.refresh_pace(l, fin)
        (WEB_DATA / f"{sym}.json").write_text(json.dumps(l, indent=1, ensure_ascii=False))
        changed = True
        print(f"  progress {sym:<11} {n} promise(s) re-read with {nums['quarter']} from the filing", flush=True)
    if changed:
        from scripts.build_all import write_index
        write_index()


if __name__ == "__main__":
    main(sys.argv[1:])
