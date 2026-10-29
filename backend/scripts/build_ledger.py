"""Stage 2: promises -> threads -> verdicts -> one JSON ledger per company for the app.

    python -m scripts.build_ledger            # spike companies
    python -m scripts.build_ledger INFY

Reads data/out/promises/{SYMBOL}.json (from extract_all) and writes web/public/data/.
"""

import json
import sys

from finn import research
from finn.config import OUT, ROOT
from finn.portfolio import SPIKE

WEB_DATA = ROOT / "web" / "public" / "data"


def main(symbols: list[str]) -> None:
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    index = []
    for sym in symbols:
        slug, meta = research.company_meta(sym)
        calls = json.loads((OUT / "promises" / f"{sym}.json").read_text())
        ledger = research.build_ledger(sym, slug, meta, calls)
        (WEB_DATA / f"{sym}.json").write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
        r = ledger["record"]
        index.append({k: ledger[k] for k in ("symbol", "name", "sector", "size", "latest_call",
                                             "latest_results", "record")})
        print(f"{sym:<9} threads={len(ledger['threads']):>3}  kept={r['kept']} missed={r['missed']} "
              f"moved={r['moved']} quiet={r['quiet']} open={r['open']}  {r['by_status']}")
    (WEB_DATA / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:] or SPIKE)
