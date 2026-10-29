"""Stage 1: extract promises from every downloaded transcript of the given companies.

    python -m scripts.extract_all             # spike companies
    python -m scripts.extract_all INFY

Writes data/out/promises/{SYMBOL}.json: one entry per call, oldest first.
"""

import json
import sys

from finn import fiscal
from finn.config import OUT, TEXT
from finn.portfolio import SPIKE
from finn.promises import extract


def calls_for(symbol: str) -> list[tuple[str, str]]:
    """(screener label, path) for each downloaded transcript, oldest first."""
    out = []
    for p in (TEXT / "transcripts").glob(f"{symbol}_*.txt"):
        tag = p.stem.split("_")[1]  # "Oct2026"
        label = f"{tag[:3]} {tag[3:]}"
        out.append((label, str(p)))
    return sorted(out, key=lambda c: fiscal.parse_label(c[0]))


def main(symbols: list[str]) -> None:
    dest = OUT / "promises"
    dest.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        calls = []
        for label, path in calls_for(sym):
            text = open(path).read()
            ex = extract.extract(text, sym, label)
            fy, q = fiscal.reported_quarter(fiscal.parse_label(label))
            calls.append({"call": label, "quarter": fiscal.label(fy, q), "file": path.split("/")[-1],
                          "promises": ex.promises, "rejected": len(ex.rejected)})
            print(f"{sym:<9} {label:<9} {len(ex.promises):>3} promises  ({len(ex.rejected)} rejected)")
        (dest / f"{sym}.json").write_text(json.dumps(calls, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or SPIKE)
