"""Score promise extraction against the hand-labelled gold set in eval/gold/.

    python -m scripts.eval_extraction --prefilter-only   # no LLM: did gold survive the cut?
    python -m scripts.eval_extraction                    # full: precision / recall / fields
    python -m scripts.eval_extraction --model gemini-flash-latest

Gold items marked optional are borderline (subsidiary targets, policies, soft talk):
missing one costs no recall, and predicting one isn't a false positive.
"""

import argparse
import json
import re

from finn.config import ROOT, TEXT
from finn.promises import extract, prefilter

GOLD = ROOT / "eval" / "gold"


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9.%]+", norm(s)))


def matches(phrase: str, quote: str) -> bool:
    p, q = norm(phrase), norm(quote)
    if p in q or (len(q) > 25 and q in p):
        return True
    pt = tokens(phrase)
    return len(pt & tokens(quote)) >= 0.8 * len(pt)


def as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def field_errors(g: dict, p: dict) -> list[str]:
    errs = []
    for f in ("kind", "metric", "period", "basis", "change"):
        if f in g and p.get(f) not in as_list(g[f]):
            errs.append(f"{f}: {p.get(f)} != {g[f]}")
    for f in ("low", "high"):
        if f in g:
            ok = [g[f]] + ([g[f"alt_{f}"]] if f"alt_{f}" in g else [])
            if p.get(f) is None or all(abs(p[f] - v) > 0.5 for v in ok):
                errs.append(f"{f}: {p.get(f)} != {g[f]}")
    return errs


def transcript(company: str, call: str):
    return next((TEXT / "transcripts").glob(f"{company}_{call.replace(' ', '')}_*.txt"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefilter-only", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--single-pass", action="store_true")
    args = ap.parse_args()

    tot = {"core": 0, "kept": 0, "found": 0, "preds": 0, "fp": 0, "dups": 0, "field_ok": 0, "tokens": 0}
    for gf in sorted(GOLD.glob("*.json")):
        gold = json.loads(gf.read_text())
        text = transcript(gold["company"], gold["call"]).read_text()
        kept_text = prefilter.render(prefilter.excerpts(text))
        core = [g for g in gold["promises"] if not g.get("optional")]
        print(f"\n=== {gold['company']} {gold['call']}")

        lost = [g["id"] for g in core if not any(matches(m, kept_text) for m in g["match"])]
        tot["core"] += len(core)
        tot["kept"] += len(core) - len(lost)
        print(f"pre-filter kept {len(core) - len(lost)}/{len(core)} core promises" +
              (f"; lost: {lost}" if lost else ""))
        if args.prefilter_only:
            continue

        ex = extract.extract(text, gold["company"], gold["call"], model=args.model,
                             focused_pass=not args.single_pass)
        tot["tokens"] += ex.excerpt_tokens
        tot["preds"] += len(ex.promises)
        hit: dict[str, list[dict]] = {}
        for p in ex.promises:
            # One sentence can carry several promises ("order inflow 10-12%, revenue 12-15%,
            # ROE 16-17%"), so pick the matching gold item whose fields agree best.
            cands = [g for g in gold["promises"] if any(matches(m, p["quote"]) for m in g["match"])]
            g = min(cands, default=None, key=lambda g: (
                len(field_errors(g, p)), g["id"] in hit, bool(g.get("optional"))))
            if g is None:
                tot["fp"] += 1
                print(f"  ? unmatched [{p['kind']}/{p['metric']}/{p['period']}] {p['quote'][:110]}")
            else:
                hit.setdefault(g["id"], []).append(p)
        for g in core:
            if g["id"] not in hit:
                print(f"  ✗ missed {g['id']}")
                continue
            tot["found"] += 1
            errs = field_errors(g, hit[g["id"]][0])
            tot["field_ok"] += not errs
            print(f"  ✓ {g['id']}" + (f"  fields: {'; '.join(errs)}" if errs else ""))
        tot["dups"] += sum(len(v) - 1 for v in hit.values())
        for p in ex.rejected:
            print(f"  ⚠ rejected (quote not verbatim): {p.get('quote', '')[:100]}")

    print(f"\nPre-filter recall: {tot['kept']}/{tot['core']}")
    if not args.prefilter_only:
        print(f"Extraction recall: {tot['found']}/{tot['core']}  "
              f"fields fully right: {tot['field_ok']}/{tot['found']}")
        print(f"Predictions: {tot['preds']}  unmatched (review by hand): {tot['fp']}  "
              f"duplicates: {tot['dups']}  excerpt tokens in: {tot['tokens']}")


if __name__ == "__main__":
    main()
