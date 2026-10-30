"""Audit every built output for things that should never be true.

    python -m scripts.audit            # all companies; report + data/out/audit.json
    python -m scripts.audit LT INFY    # a subset
    python -m scripts.audit --strict   # exit 1 if any error (for build scripts / CI)

The regression tests pin the specific mistakes we've seen. This checks the *classes*
of mistake, on real output, so a company nobody has looked at still gets checked.
Errors are invariants that must never fail; warnings are suspicious and need a look.
"""

import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime

from finn import financials, fiscal
from finn.config import OUT, TEXT
from finn.filings import STOP as LINK_STOP
from finn.filings import _words as link_words
from finn.portfolio import PORTFOLIO, RESEARCH, by_symbol
from finn.promises import prefilter
from finn.figures import PERIOD_TOKEN, THREAD_ID, near, nums
from finn.promises.extract import _norm, _numbers
from finn.snapshot import case_sources
from finn.promises.link import PER_YEAR, dimension
from finn.promises.resolve import _comparable, parse_period

from scripts.build_ledger import WEB_DATA

STATUSES = {"delivered", "beat", "missed", "tracking", "on_track", "behind", "delayed", "open",
            "cant_verify", "gone_quiet", "lapsed", "said"}
BOILERPLATE = re.compile(r"^(please find|pursuant|we wish|this is to inform|in compliance|enclosed|attached|kindly)|"
                         r"^(general|others?|press release ?/? ?media release|announcement under regulation 30.*)$", re.I)


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, check, sym, msg, ref=None):
        self.items.append({"level": level, "check": check, "symbol": sym, "ref": ref, "message": msg})

    def error(self, *a, **k): self.add("error", *a, **k)
    def warn(self, *a, **k): self.add("warning", *a, **k)
    def info(self, *a, **k): self.add("info", *a, **k)


# ---- promises -------------------------------------------------------------------------

def cleaned(text: str) -> str:
    """The transcript as the pipeline reads it: speaker turns without page headers."""
    return " ".join(" ".join(t.lines) for t in prefilter.turns(text))


def transcript_index(sym: str) -> dict[str, str]:
    """call label -> transcript text, via the promises file that recorded each call's file."""
    p = OUT / "promises" / f"{sym}.json"
    if not p.exists():
        return {}
    out = {}
    for c in json.loads(p.read_text()):
        f = TEXT / "transcripts" / c["file"]
        if f.exists():
            out[c["call"]] = f.read_text()
    return out


def check_promises(sym: str, l: dict, r: Report) -> None:
    texts = transcript_index(sym)
    asks = {call: prefilter.questioners(prefilter.turns(t)) for call, t in texts.items()}
    clean = {call: _norm(cleaned(t)) for call, t in texts.items()}
    fy = int(m.group(1)) if (m := re.search(r"FY(\d{2})", l.get("latest_results") or "")) else None
    latest_q = None
    if m2 := re.match(r"Q([1-4])FY(\d{2})", l.get("latest_results") or ""):
        latest_q = (int(m2.group(2)), int(m2.group(1)))
    seen_keys = defaultdict(list)

    for t in l["threads"]:
        tid, stmts = t["id"], t["statements"]
        if t["status"] not in STATUSES:
            r.error("schema", sym, f"unknown status {t['status']!r}", tid)
        if not stmts:
            r.error("schema", sym, "thread with no statements", tid)
            continue

        for s in stmts:
            # target figures must be in their own quote (the HUL / Tata Power class)
            tgt = [float(x) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", (s.get("target") or "").replace(",", ""))]
            if tgt and not all(near(v, _numbers(s["quote"])) for v in tgt):
                r.error("target-not-in-quote", sym, f"target {s['target']} not in quote: “{s['quote'][:90]}”", tid)
            # quote really is in the transcript
            txt = clean.get(s["call"])
            if txt and _norm(s["quote"])[:120] not in txt:
                r.error("quote-not-in-transcript", sym, f"{s['call']}: “{s['quote'][:80]}”", tid)
            # said by management, not by the analyst asking
            if s["speaker"] in asks.get(s["call"], set()):
                r.warn("speaker-asks-questions", sym, f"{s['speaker']} mostly asks questions on the {s['call']} call: "
                       f"“{s['quote'][:70]}”", tid)
            # a "promise" about a period already reported when it was said
            p = parse_period(s.get("deadline") or s.get("period"))
            if p and t["kind"] != "soft":
                rq = fiscal.reported_quarter(fiscal.parse_label(s["call"]))
                if p.end < rq:
                    r.warn("promise-about-the-past", sym, f"{s['call']} statement about {p.label}, already "
                           f"reported ({fiscal.label(*rq)}): “{s['quote'][:70]}”", tid)

        # moves must be between comparable statements, and plausible
        for i, ev in enumerate(t["events"]):
            if ev["type"] in ("reaffirmed", "deferred", "advanced"):
                continue
            a, b = stmts[i], stmts[i + 1]
            pa = {"low": _first(a["target"]), "high": _last(a["target"]), "unit": _unit(a["target"]),
                  "period": a.get("period"), "quote": a["quote"]}
            pb = {"low": _first(b["target"]), "high": _last(b["target"]), "unit": _unit(b["target"]),
                  "period": b.get("period"), "quote": b["quote"]}
            if not _comparable(pa, pb):
                r.error("move-between-incomparable", sym, f"{ev['type']} {ev['from']} → {ev['to']}", tid)
            ma, mb = _mid(pa), _mid(pb)
            if ma and mb and (mb / ma > 3 or ma / mb > 3):
                r.warn("implausible-move", sym, f"{ev['type']} {ev['from']} → {ev['to']} (×{max(mb/ma, ma/mb):.1f})", tid)

        units = {dimension(re.sub(r"[\d,.\s≥≤–~]+", " ", x["target"])) for x in stmts if x.get("target")} - {None}
        if len(units) > 1:
            r.warn("thread-mixes-units", sym, f"{t['title']}: targets {[x['target'] for x in stmts if x.get('target')]}", tid)
        # one thread, one commitment: a per-year target (growth, margin, capex) for two
        # different years is a linking error. Stock targets ("capacity by FY28", later
        # "by FY29") are one promise slipping, and long-range plans are one horizon.
        periods = {s.get("period") for s in stmts if s.get("period") and s.get("target")}
        years = {pp.fy for p in periods if (pp := parse_period(p))
                 and pp.fy - fiscal.fy_of(fiscal.parse_label(stmts[0]["call"])) < 2}
        if t["kind"] == "hard" and t["metric"] in PER_YEAR and len(years) > 1:
            r.warn("thread-mixes-years", sym, f"{t['title']}: statements for {sorted(periods)}", tid)
        if (m := re.search(r"FY(\d{2})", t["title"])) and (n := re.search(r"FY(\d{2})", t["period"] or "")) \
                and m.group(1) != n.group(1):
            r.warn("title-period-mismatch", sym, f"title says FY{m.group(1)}, period is {t['period']}", tid)

        # verdicts only once the period is over
        if t["status"] in ("delivered", "beat", "missed") and t.get("basis") == "financials":
            pp = parse_period(t["period"])
            if pp and latest_q and pp.end > latest_q:
                r.error("verdict-before-period-ends", sym, f"{t['status']} for {t['period']} with results only "
                        f"through {l['latest_results']}", tid)
        if t["status"] in ("delivered", "beat") and "multi" in (t["period"] or "").lower():
            r.error("multi-year-kept-early", sym, f"{t['title']} marked {t['status']}", tid)

        # evidence figures must be in the evidence quote (the L&T "20% CAGR" class)
        if t.get("basis") == "management" and t.get("detail") and "no figure given" not in t["detail"]:
            q = (t.get("evidence") or {}).get("quote") or ""
            allowed = nums(q) + [v for v in (_first(t["target"]), _last(t["target"])) if v is not None]
            bad = [v for v in nums(t["detail"]) if not near(v, allowed)]
            if bad:
                r.error("evidence-figure-not-in-quote", sym, f"“{t['detail']}” has {bad} not in “{q[:80]}”", tid)

        if t.get("pace"):
            pc = t["pace"]
            if abs(pc.get("so_far", 0)) > 150 or abs(pc.get("needs", 0) or 0) > 200:
                r.warn("implausible-pace", sym, f"{pc}", tid)

        if t["kind"] == "hard" and t["status"] not in ("said", "lapsed"):
            key = (t["metric"], t["period"], (t.get("scope") or "company").lower())
            seen_keys[key].append(t)
    for key, ts in seen_keys.items():
        # same metric, year and scope with overlapping targets in the same unit: one
        # commitment split in two (ROA beside ROE, or R&D beside capex, is fine)
        ts = [t for t in ts if any(_overlap(t, u) for u in ts if u is not t)]
        if len(ts) > 1 and key[0] not in ("other",):
            r.warn("duplicate-threads", sym, f"{len(ts)} threads for {key[0]} {key[1]} ({key[2]}): "
                   + "; ".join(f"{x['id']} {x['title']} {x['target']}" for x in ts))


def _overlap(a: dict, b: dict) -> bool:
    if _unit(a["target"]) != _unit(b["target"]):
        return False
    lo_a, hi_a = _first(a["target"]), _last(a["target"])
    lo_b, hi_b = _first(b["target"]), _last(b["target"])
    if None in (lo_a, hi_a, lo_b, hi_b):
        return False
    return lo_a <= hi_b * 1.05 and lo_b <= hi_a * 1.05


def _first(target: str):
    v = re.findall(r"\d[\d,]*(?:\.\d+)?", (target or "").replace(",", ""))
    return float(v[0]) if v else None


def _last(target: str):
    v = re.findall(r"\d[\d,]*(?:\.\d+)?", (target or "").replace(",", ""))
    return float(v[-1]) if v else None


def _unit(target: str) -> str:
    t = target or ""
    return "%" if "%" in t else "Rs crore" if "cr" in t else "x" if "×" in t else re.sub(r"[\d,.\s≥≤–₹]", "", t)


def _mid(p):
    vals = [v for v in (p["low"], p["high"]) if v]
    return sum(vals) / len(vals) if vals else None


# ---- results ------------------------------------------------------------------------------

def check_results(sym: str, h: dict | None, r: Report, lender: bool, where: str) -> None:
    if not h:
        return
    row = {x["label"]: x for x in h["rows"]}
    rev = (row.get("Revenue") or {}).get("value")
    for k in ("PBT", "PAT", "EBITDA"):
        v = (row.get(k) or {}).get("value")
        if rev and v and v > rev:
            r.error("profit-above-revenue", sym, f"{where} {h['quarter']}: {k} ₹{v:,.0f} cr > revenue ₹{rev:,.0f} cr "
                    "(wrong column or unit?)")
    for k, lim in (("Revenue", 150), ("EBITDA", 400), ("PAT", 400)):
        y = (row.get(k) or {}).get("yoy")
        if y is not None and abs(y) > lim:
            r.warn("extreme-yoy", sym, f"{where} {h['quarter']}: {k} {y:+.0f}% YoY")
    m = (h.get("margin") or {}).get("now")
    if m is not None and not -30 <= m <= 75:
        r.warn("margin-out-of-range", sym, f"{where} {h['quarter']}: EBITDA margin {m:.1f}%")
    if lender and "EBITDA" in row:
        r.error("lender-ebitda", sym, f"{where}: a lender shouldn't show EBITDA")


def check_filing_results_vs_screener(sym: str, f: dict, fin, r: Report) -> None:
    """A figure read from a filing must agree with screener once screener has the quarter."""
    h = f.get("numbers")
    if not h or "filing" not in (h.get("source") or ""):
        return
    m = re.match(r"Q([1-4])FY(\d{2})", h["quarter"])
    if not m or not fin:
        return
    s = fin.q(int(m.group(2)), int(m.group(1)), "Sales")
    rev = next((x["value"] for x in h["rows"] if x["label"] == "Revenue"), None)
    if s and rev and abs(rev / s - 1) > 0.02:
        r.error("filing-vs-screener", sym, f"{h['quarter']} revenue read from the filing ₹{rev:,.0f} cr vs screener ₹{s:,.0f} cr")
    elif s:
        r.info("filing-now-on-screener", sym, f"{h['quarter']} is now on screener and agrees; the filing read can retire")


# ---- filings ------------------------------------------------------------------------------

def check_filings(digest: dict, ledgers: dict, r: Report) -> None:
    built = datetime.fromisoformat(digest["built"][:19])
    for sym in digest.get("failed", []):
        n = sum(f["symbol"] == sym for f in digest["items"])
        r.warn("filings-fetch-failed", sym, f"this run's fetch failed; showing {n} filings from the last good build")
    material = [f for f in digest["items"] if not f["routine"]]
    for f in digest["items"]:
        sym, text = f["symbol"], f"{f['subject']} {f['headline']} {f.get('summary') or ''}"
        lender = bool((ledgers.get(sym, {}).get("snapshot") or {}).get("lender"))
        if re.search(r"USFDA|US ?FDA|warning letter|form 483|import alert", text, re.I) and f["type"] != "regulatory":
            r.warn("category-conflict", sym, f"mentions a drug regulator but filed as {f['label']}: {f['subject'][:70]}", f["id"])
        if lender and re.search(r"\bNCDs?\b|debenture|commercial paper", text, re.I) and not f["routine"]:
            r.warn("category-conflict", sym, f"lender debt paper not routine: {(f.get('summary') or f['subject'])[:70]}", f["id"])
        if re.search(r"intimation", f["subject"], re.I) and f["type"] in ("results", "earnings_call") and not f["routine"]:
            r.warn("category-conflict", sym, f"an intimation filed as {f['label']}: {f['subject'][:70]}", f["id"])
        if f["type"] == "regulatory" and not f.get("tone"):
            r.info("regulatory-no-direction", sym, f"can't tell if good or bad: {(f.get('summary') or f['subject'])[:80]}", f["id"])
        if f["routine"]:
            continue
        shown = (f.get("summary") or "").strip()
        if not shown or len(shown) < 25 or BOILERPLATE.search(shown):
            shown = re.sub(r"^Announcement under Regulation 30 \(LODR\)\s*-\s*", "", f["subject"], flags=re.I)
        if BOILERPLATE.search(shown.strip()) or len(shown) < 12:
            r.warn("unclear-headline", sym, f"material {f['label']} filing shows only “{shown}”", f["id"])
        if f["type"] == "results" and not f.get("numbers"):
            age = (built - datetime.fromisoformat(f["at"][:19])).days
            (r.warn if age > 2 else r.info)("results-without-figures", sym, f"{f['subject'][:60]} ({age}d old)", f["id"])
        if f["type"] == "management_change" and not f.get("people"):
            r.warn("management-change-no-people", sym, f"no one named: {f['subject'][:70]}", f["id"])
        for pid in f.get("promises", []):
            t = next((t for t in ledgers.get(sym, {}).get("threads", []) if t["id"] == pid), None)
            if t and f["type"] != "results":
                shared = link_words(f"{f['subject']} {f['headline']} {f.get('summary') or ''}") & \
                         link_words(f"{t['title']} {t.get('scope') or ''}")
                if len(shared) == 1:
                    w = next(iter(shared))
                    common = sum(w in link_words(x["title"]) for x in ledgers.get(sym, {}).get("threads", []))
                    if common >= 3:  # "revenue", "cement": in many of this company's promises
                        r.warn("weak-promise-link", sym, f"filing linked to “{t['title']}” on the common word "
                               f"'{w}' only (in {common} of its promises)", f["id"])
    # the same event twice
    by = defaultdict(list)
    for f in material:
        by[(f["symbol"], f["type"], f["at"][:10])].append(f)
    for (sym, typ, day), fs in by.items():
        for i, a in enumerate(fs):
            for b in fs[i + 1:]:
                wa, wb = set(re.findall(r"[a-z]{4,}", (a.get("summary") or a["subject"]).lower())), \
                         set(re.findall(r"[a-z]{4,}", (b.get("summary") or b["subject"]).lower()))
                if wa and wb and len(wa & wb) / len(wa | wb) > 0.6:
                    r.warn("duplicate-filings", sym, f"{typ} on {day} twice: {a['id']} / {b['id']}")


# ---- prices, snapshot, business, case --------------------------------------------------

def check_prices(prices: dict, r: Report) -> None:
    series = prices.get("series", {})
    last = max((s[-1][0] for s in series.values() if s), default=None)
    for c in PORTFOLIO:
        s = series.get(c.symbol)
        if not s:
            r.error("prices-missing", c.symbol, "no price series")
            continue
        if last and (date.fromisoformat(last) - date.fromisoformat(s[-1][0])).days > 3:
            r.warn("prices-stale", c.symbol, f"last price {s[-1][0]} vs {last} for others")
        for n, ((d0, c0, _), (d1, c1, v1)) in enumerate(zip(s, s[1:]), 1):
            # A real crash trades heavily (Kaynes, 14 May 2026: ~9x normal volume); an
            # unadjusted split or bonus moves the price on an ordinary day.
            usual = sorted(v for _, _, v in s[max(0, n - 20):n] if v)
            heavy = usual and v1 and v1 > 3 * usual[len(usual) // 2]
            if c0 and abs(c1 / c0 - 1) > 0.2 and not heavy:
                r.warn("price-jump", c.symbol, f"{d1}: {((c1 / c0) - 1) * 100:+.0f}% in a day on normal volume "
                       "(split or bonus not adjusted?)")
        zero = [d for d, _, v in s[-10:] if not v]
        if zero:
            r.warn("zero-volume", c.symbol, f"no volume on {', '.join(zero)}")


def check_snapshot_and_case(sym: str, l: dict, r: Report) -> None:
    for m in (l.get("snapshot") or {}).get("metrics", []):
        v = m.get("value")
        if v is None:
            continue
        if m["key"] == "returns" and not -50 <= v <= 120:
            r.warn("snapshot-out-of-range", sym, f"{m['label']} {v}%")
        if m["key"] == "cash_conversion" and not -100 <= v <= 300:
            r.warn("snapshot-out-of-range", sym, f"cash conversion {v}% of profit")
    b = l.get("business") or {}
    shares = [s["share_pct"] for s in b.get("segments", []) if s.get("share_pct") is not None]
    if shares and sum(shares) > 105:
        r.warn("business-mix-over-100", sym, f"segment shares sum to {sum(shares):.0f}%")

    case = l.get("case") or {}
    threads = {t["id"]: t for t in l["threads"]}
    metrics = {m["key"]: m for m in (l.get("snapshot") or {}).get("metrics", [])}
    for side in ("bull", "bear"):
        for p in case.get(side, []):
            if not p.get("refs"):
                r.error("case-without-source", sym, f"{side}: {p['point'][:80]}")
                continue
            sources = [case_sources(ref, threads, metrics) for ref in p["refs"]]
            fig = p.get("figure", "")
            if THREAD_ID.search(fig) or THREAD_ID.search(p.get("point", "")) or re.search(r"\b[a-z]+_[a-z_]+\b", fig + p.get("point", "")):
                r.error("case-shows-internal-id", sym, f"{side} “{fig}”: an internal id shows in the app", p["refs"][0])
            if not nums(fig) and not PERIOD_TOKEN.search(fig):
                r.warn("case-figure-without-number", sym, f"{side} “{fig}”", p["refs"][0])
            pool = nums(" ".join(sources))
            bad = [v for v in nums(fig) if not near(v, pool)]
            if bad:
                r.error("case-figure-not-in-sources", sym, f"{side} “{p.get('figure')}”: {bad} not in what it cites", p["refs"][0])


def check_schema(sym: str, l: dict, r: Report) -> None:
    for k in ("symbol", "name", "calls", "threads", "record"):
        if k not in l:
            r.error("schema", sym, f"missing field {k!r}")
    for k in ("kept", "missed", "resolved", "moved", "quiet", "open"):
        if not isinstance((l.get("record") or {}).get(k), int):
            r.error("schema", sym, f"record.{k} missing or not a number")
    if len(l.get("calls", [])) < 3 and l.get("calls"):
        r.info("thin-record", sym, f"only {len(l['calls'])} earnings calls read")
    if not l.get("business"):
        r.info("no-business-snapshot", sym, "no annual-report snapshot")


# ---- run ----------------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    r = Report()
    ledgers = {}
    for c in PORTFOLIO + RESEARCH:
        if wanted and c.symbol not in wanted:
            continue
        p = WEB_DATA / f"{c.symbol}.json"
        if not p.exists():
            r.error("missing-record", c.symbol, "no built record")
            continue
        l = ledgers[c.symbol] = json.loads(p.read_text())
        lender = bool((l.get("snapshot") or {}).get("lender"))
        check_schema(c.symbol, l, r)
        check_promises(c.symbol, l, r)
        check_results(c.symbol, l.get("headline"), r, lender, "record")
        check_snapshot_and_case(c.symbol, l, r)

    digest = json.loads((WEB_DATA / "filings.json").read_text()) if (WEB_DATA / "filings.json").exists() else None
    if digest:
        fins = {}
        for f in digest["items"]:
            if wanted and f["symbol"] not in wanted:
                continue
            if f["type"] == "results" and f.get("numbers"):
                lender = bool((ledgers.get(f["symbol"], {}).get("snapshot") or {}).get("lender"))
                check_results(f["symbol"], f["numbers"], r, lender, "filing")
                if "filing" in (f["numbers"].get("source") or ""):
                    if f["symbol"] not in fins:
                        c = by_symbol(f["symbol"])
                        fins[f["symbol"]] = financials.load(f["symbol"], c.slug if c else None)
                    check_filing_results_vs_screener(f["symbol"], f, fins[f["symbol"]], r)
        check_filings({**digest, "items": [f for f in digest["items"] if not wanted or f["symbol"] in wanted],
                       "failed": [s for s in digest.get("failed", []) if not wanted or s in wanted]}, ledgers, r)
    if (WEB_DATA / "prices.json").exists() and not wanted:
        check_prices(json.loads((WEB_DATA / "prices.json").read_text()), r)

    # quarters: is anyone stuck behind the rest?
    qs = [l.get("latest_results") for l in ledgers.values() if l.get("latest_results")]
    if qs:
        common = max(set(qs), key=qs.count)
        for sym, l in ledgers.items():
            q = l.get("latest_results")
            if q and q != common and _qord(q) < _qord(common):
                r.info("results-behind-peers", sym, f"latest results {q}; most companies are on {common}")

    (OUT / "audit.json").write_text(json.dumps(r.items, indent=1, ensure_ascii=False))
    print_report(r, len(ledgers))
    return 1 if strict and any(i["level"] == "error" for i in r.items) else 0


def _qord(q):
    m = re.match(r"Q([1-4])FY(\d{2})", q)
    return int(m.group(2)) * 10 + int(m.group(1)) if m else 0


def print_report(r: Report, n: int) -> None:
    levels = {"error": 0, "warning": 0, "info": 0}
    for i in r.items:
        levels[i["level"]] += 1
    print(f"Audited {n} companies: {levels['error']} errors, {levels['warning']} warnings, {levels['info']} notes\n")
    by = defaultdict(list)
    for i in r.items:
        by[(i["level"], i["check"])].append(i)
    order = {"error": 0, "warning": 1, "info": 2}
    for (level, check), items in sorted(by.items(), key=lambda kv: (order[kv[0][0]], -len(kv[1]))):
        syms = sorted({i["symbol"] for i in items})
        print(f"[{level.upper()}] {check}: {len(items)} ({', '.join(syms[:8])}{'…' if len(syms) > 8 else ''})")
        for i in items[:4]:
            print(f"    {i['symbol']:<10} {i['ref'] or '':<14} {i['message'][:150]}")
        if len(items) > 4:
            print(f"    … {len(items) - 4} more in data/out/audit.json")
    print()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
