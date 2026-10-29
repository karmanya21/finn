"""Build promise records for the whole portfolio and the research set.

    python -m scripts.build_all              # everything (slow the first time; cached after)
    python -m scripts.build_all INFY LT      # a subset

Portfolio ledgers go to web/public/data/ (the push side reads them); research-set ledgers
are cached for the pull API so those searches open instantly. A company that fails (data
source down, model quota) is reported and skipped; re-running picks up where it left off.
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from finn import research
from finn.portfolio import PORTFOLIO, RESEARCH

from scripts.build_ledger import WEB_DATA


WORKERS = 2  # free-tier calls are slow, so overlap companies; more than 2 trips screener and Gemini limits


def build_one(c, portfolio: set[str]) -> None:
    t0 = time.time()
    try:
        ledger = research.research(c.symbol, lambda step, detail: None)
    except Exception as e:  # keep going; report at the end of the line
        print(f"{c.symbol:<11} FAILED  {type(e).__name__}: {str(e)[:120]}", flush=True)
        return
    if c.symbol in portfolio:
        (WEB_DATA / f"{c.symbol}.json").write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
    r = ledger["record"]
    print(f"{c.symbol:<11} {time.time() - t0:5.0f}s  calls={len(ledger['calls'])} "
          f"threads={len(ledger['threads'])} kept={r['kept']}/{r['resolved']} moved={r['moved']} "
          f"quiet={r['quiet']}", flush=True)


def main(symbols: list[str]) -> None:
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    wanted = [c for c in PORTFOLIO + RESEARCH if not symbols or c.symbol in symbols]
    portfolio = {c.symbol for c in PORTFOLIO}
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda c: build_one(c, portfolio), wanted))
    write_index()


def write_index() -> None:
    """index.json lists portfolio companies that have a built ledger, in portfolio order."""
    index = []
    for c in PORTFOLIO:
        p = WEB_DATA / f"{c.symbol}.json"
        if p.exists():
            l = json.loads(p.read_text())
            index.append({k: l.get(k) for k in ("symbol", "name", "sector", "size", "latest_call",
                                                 "latest_results", "record")})
    (WEB_DATA / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    print(f"index: {len(index)} companies")


if __name__ == "__main__":
    main(sys.argv[1:])
