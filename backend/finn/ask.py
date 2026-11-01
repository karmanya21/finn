"""Ask FINN about one company, answered only from that company's own record.

The record is laid out as numbered sources: each promise with its quotes, the latest
results, the snapshot numbers, the business mix, recent filings and the price history.
The model may only answer from those and must cite a source for every sentence. Code then
judges each sentence as it does a bull/bear point: cited sources must exist, every number
must appear in what it cites, and nothing may read as advice to buy or sell. A sentence that
fails is dropped; if none survive, the answer is that the record doesn't say.
"""

import json
import re
from datetime import date

from finn.config import ROOT
from finn.figures import THREAD_ID, near, nums
from finn.llm.client import complete_json

WEB_DATA = ROOT / "web" / "public" / "data"

SYSTEM = """You answer a question about one listed Indian company for an investor, using ONLY the
sources given. Each source starts with its id in square brackets.

Rules:
- Every sentence must cite the ids of the sources it relies on. Use only facts and numbers
  that appear in those sources, written the way they appear there.
- If the sources don't answer the question, set answerable to false and say in one sentence
  what the record does cover that is closest to the question, citing it.
- No outside knowledge, no forecasts of your own, and never advice on whether to buy, sell or
  hold. Describe what management promised and what happened.
- At most four short sentences. Plain English. Don't mention source ids in the text."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "sentences": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "sources"]}},
    },
    "required": ["answerable", "sentences"],
}

ADVICE_Q = re.compile(r"\b(should i (?:buy|sell|hold|invest)|worth (?:buying|investing)|good (?:buy|investment)|"
                      r"buy or sell|target price|multibagger|invest in (?:it|this))\b", re.I)
ADVICE_A = re.compile(r"\b(you should|i recommend|we recommend|(?:a )?(?:good|strong) (?:buy|investment)|"
                      r"(?:buy|sell|hold) (?:the|this) (?:stock|share)s?|is (?:a )?(?:buy|sell))\b", re.I)


def _pct(a: float, b: float) -> str:
    return f"{(a / b - 1) * 100:+.1f}%"


def sources(ledger: dict, filings: list[dict], prices: list[list] | None) -> dict[str, dict]:
    """id -> {label, text, kind, ref}: everything an answer may draw on, as plain text."""
    out: dict[str, dict] = {}
    r = ledger.get("record") or {}
    if r:
        out["record"] = {"kind": "record", "label": "Promise record", "text": (
            f"Of {r.get('kept', 0) + r.get('missed', 0) + r.get('quiet', 0) + r.get('open', 0)} numbers and milestones "
            f"tracked: {r.get('kept', 0)} kept, {r.get('missed', 0)} missed, {r.get('quiet', 0)} gone quiet, "
            f"{r.get('open', 0)} still open; {r.get('moved', 0)} had targets moved. Calls read: "
            f"{', '.join(c['call'] for c in ledger.get('calls', []))}.")}

    newer = [f for f in filings if f["type"] == "results" and f.get("numbers")]
    h = newer[0]["numbers"] if newer else ledger.get("headline")
    if h:
        rows = "; ".join(f"{x['label']} ₹{x['value']:,.0f} cr"
                         + (f", {x['yoy']:+.1f}% YoY" if x.get("yoy") is not None else "")
                         + (f", {x['qoq']:+.1f}% QoQ" if x.get("qoq") is not None else "") for x in h["rows"])
        m = h.get("margin") or {}
        margin = f"; EBITDA margin {m['now']:.1f}%" + (f" vs {m['year_ago']:.1f}% a year ago" if m.get("year_ago") else "") \
            if m.get("now") is not None else ""
        notes = " ".join(n["text"] for n in h.get("notes") or [])
        out["results"] = {"kind": "results", "label": f"{h['quarter']} results",
                          "text": f"{h['quarter']} results: {rows}{margin}. {notes}".strip()}

    for t in ledger.get("threads", []):
        if t["kind"] == "soft":
            continue
        said = " | ".join(f"{s['call']} call, {s['speaker']}: \"{s['quote'][:300]}\"" for s in t["statements"])
        moves = "; ".join(f"{e['type']} on the {e['call']} call from {e['from']} to {e['to']}"
                          for e in t["events"] if e["type"] != "reaffirmed")
        pace = t.get("pace")
        pace_s = f" Progress: {pace['so_far_label']} at {pace['so_far']:.1f}% ({pace['status'].replace('_', ' ')})." if pace else ""
        out[t["id"]] = {"kind": "promise", "label": t["title"], "ref": t["id"], "text": (
            f"Promise '{t['title']}' ({t['kind']}, {t['period']}): target {t['target'] or 'none stated'}; "
            f"status {t['status'].replace('_', ' ')}. {t.get('detail') or ''}{pace_s} "
            f"{('Moves: ' + moves + '.') if moves else ''} Said: {said}").strip()}

    for m in (ledger.get("snapshot") or {}).get("metrics", []):
        out[f"m-{m['key']}"] = {"kind": "number", "label": m["label"],
                                "text": f"{m['label']}: {m.get('value')} {m.get('unit', '')}. {m.get('reading', '')}"}

    b = ledger.get("business")
    if b:
        segs = ", ".join(f"{s['name']} {s['share_pct']}%" for s in b.get("segments", []) if s.get("share_pct") is not None)
        out["business"] = {"kind": "business", "label": "What the company does",
                           "text": f"{b.get('what', '')} Segments ({b.get('year') or 'latest year'}): {segs}."}

    for i, f in enumerate([f for f in filings if not f.get("routine")][:12], 1):
        out[f"F{i}"] = {"kind": "filing", "label": f"{f['label']}, {f['at'][:10]}", "ref": f["id"],
                        "text": f"Filing on {f['at'][:10]} ({f['label']}): {f.get('summary') or f['subject']}"}

    if prices and len(prices) > 1:
        last_d, last = prices[-1][0], prices[-1][1]
        # the same windows as the app's chart: the last n trading days, first close to last
        changes = ", ".join(f"{k} {_pct(last, prices[-n:][0][1])}" for k, n in
                            (("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 260)) if len(prices) >= min(n, 200))
        closes = [p[1] for p in prices[-260:]]
        out["price"] = {"kind": "price", "label": "Share price", "text": (
            f"Share price ₹{last:,.2f} at the {last_d} close. Change: {changes}. "
            f"Range over the period: high ₹{max(closes):,.2f}, low ₹{min(closes):,.2f}.")}
    return out


def check(raw: dict, srcs: dict[str, dict]) -> list[dict]:
    """The sentences code can stand behind, with their sources."""
    kept = []
    for s in raw.get("sentences", []):
        ids = [i for i in s.get("sources", []) if i in srcs]
        text = re.sub(r"\s*\[[^\]]*\]", "", THREAD_ID.sub(lambda m: srcs.get(m.group(), {}).get("label", ""), s.get("text", ""))).strip()
        if not ids or not text or ADVICE_A.search(text):
            continue
        pool = nums(" ".join(srcs[i]["text"] for i in ids))
        if any(not near(v, pool) for v in nums(text)):
            continue  # a number the cited sources don't contain
        kept.append({"text": text, "sources": ids})
    return kept


def ask(symbol: str, question: str, ledger: dict) -> dict:
    filings = []
    p = WEB_DATA / "filings.json"
    if p.exists():
        filings = [f for f in json.loads(p.read_text())["items"] if f["symbol"] == symbol]
    prices = None
    q = WEB_DATA / "prices.json"
    if q.exists():
        prices = json.loads(q.read_text())["series"].get(symbol)
    if not prices and ledger.get("price"):
        prices = ledger["price"]

    srcs = sources(ledger, filings, prices)
    record = "\n".join(f"[{k}] {v['text']}" for k, v in srcs.items())
    prompt = f"Company: {ledger.get('name', symbol)} ({symbol}). Today is {date.today():%d %b %Y}.\n\nSources:\n{record}\n\nQuestion: {question.strip()}"
    raw = complete_json(SYSTEM, prompt, SCHEMA)
    sentences = check(raw, srcs)
    out = sentences or [{"text": "The record doesn't answer that.", "sources": []}]
    if ADVICE_Q.search(question):  # said by code, whatever the model wrote
        out = [{"text": "FINN doesn't say whether to buy or sell. It shows what management promised and what happened.",
                "sources": []}] + sentences
    used = {i for s in sentences for i in s["sources"]}
    return {
        "answerable": bool(sentences) and raw.get("answerable", True),
        "sentences": out,
        "dropped": len(raw.get("sentences", [])) - len(sentences),
        "sources": {i: {k: srcs[i][k] for k in ("kind", "label") if k in srcs[i]} | ({"ref": srcs[i]["ref"]} if "ref" in srcs[i] else {})
                    for i in used},
    }
