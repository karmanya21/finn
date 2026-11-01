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

""" + (RULES := """Rules:
- Every sentence must cite the ids of the sources it relies on. Use only facts and numbers
  that appear in those sources, written the way they appear there.
- Answer the question asked in the first sentence: a period or date for "when", a figure for
  "how much", the reason the sources give for "why" (management's own explanation, a one-off,
  a note on the results). Then add what helps.
- If the sources answer part of it, answer that part and say in one clause what the record
  doesn't show. Set answerable to false only when no source mentions the subject of the
  question at all, and then return no sentences. Don't add caveats about things not asked.
- When the record lacks the reason or detail asked for, still give the facts it has on that
  subject (what, how much, when), then say the reason isn't in the record. Asked why a company
  is raising money: give the amount and route from the filing, then say the record doesn't give
  the purpose. Asked why a stock fell: give the fall and its date from the share price, and any
  filing that day.
- Each promise says when it is due ("due FY27"); for "when" about a promise, that is the answer.
- "FINN's note" is FINN's own reading of the numbers, not something management said. Only
  promises quote management.
- A question may follow on from the earlier conversation; read "it", "that", "why?" in that light.
- No outside knowledge, no forecasts of your own, and never advice on whether to buy, sell or
  hold. Describe what management promised and what happened.
- At most four short sentences. Plain English. Call the sources "the record", never "the
  provided sources". Write periods as the sources do (FY27, Q2FY27, H1FY27), money as ₹, and
  falls as "down 2.4%", not "down -2.4%". Don't mention source ids in the text.""")

PORTFOLIO_SYSTEM = """You answer an investor's question about their portfolio of listed Indian companies,
using ONLY the sources given. Each source starts with its id in square brackets. There is one
summary source per company (its id is the ticker); companies the question names also have their
full record, with ids like "BEL:results" or promise ids like "BEL-3".

- Name the companies you talk about. When the question asks which or how many, go through the
  summaries and list every company that fits, not just the first.
- Compare like with like: the same period, the same measure.
- Give each company's verdict (behind, missed, kept, raised…) in its own clause, citing that
  company, and only a verdict its summary states.

""" + RULES + """

At most five short sentences for a portfolio question."""

COVERS = "It covers management's promises from the earnings calls, the latest results, recent filings and the share price."

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


KIND = {"hard": "number", "milestone": "milestone", "directional": "direction", "soft": "ambition"}


def tidy(text: str) -> str:
    """Glyph slips the model makes: a superscript digit for the rupee sign ("⁷2,569 cr"), and
    a sign doubled by a word ("down -2.4%")."""
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹\u2024\u2027\u00b7\u20a8\ufffd](?=\d)", "₹", text)  # "⁷2,569", "․7,404", "₨"
    text = re.sub(r"\b(down|fell|declined|dropped|decreased|lower|fall|decline|drop|decrease)( by)? -(?=\d)", r"\1\2 ", text, flags=re.I)
    return re.sub(r"(?<![\d.])-(\d[\d.,]*%) (lower|less|down|weaker)", r"\1 \2", text, flags=re.I)


# Results lines and the words that name them. Margin before EBITDA: "EBITDA margin" is a margin.
LINE_OF = {"Revenue": "revenue", "EBITDA": "ebitda", "PBT": "pbt", "PAT": "pat"}
LINE_WORDS = [("margin", r"\bmargin"), ("pbt", r"\bPBT\b|profit before tax"), ("revenue", r"\brevenue|\bsales\b|turnover"),
              ("ebitda", r"\bEBITDA\b"), ("pat", r"\bPAT\b|net profit|profit after tax|\bprofit\b")]


def mislabelled(text: str, lines: dict[str, list[float]], noted: list[float] = ()) -> bool:
    """A clause that names one results line but carries another line's numbers ("PBT was ₹414 cr"
    when 414 is PAT): the numbers exist in the record, just not where the sentence puts them."""
    results = [v for vs in lines.values() for v in vs]
    for clause in re.split(r",(?!\d{2})|;|\band\b|\bwhile\b|\bwith\b|\bbut\b", text):  # not "2,569"
        named = next((k for k, pat in LINE_WORDS if re.search(pat, clause, re.I)), None)
        # numbers from FINN's note (year-ago figures behind a one-off) may sit with any line
        if not named or named not in lines:
            continue
        # Only results figures can be misplaced: a guidance figure ("1.5–3%") in a clause that
        # names revenue isn't a results number at all, and the source check has vouched for it.
        for v in nums(clause):
            if same(v, results) and not same(v, lines[named]) and not same(v, list(noted)):
                return True
    return False


def same(v: float, pool: list[float]) -> bool:
    """v is one of these figures, to display rounding: tighter than `near`, whose ±0.5 slack
    would read a 1.5% guide as the 1.9% QoQ it happens to sit beside."""
    return any(abs(abs(v) - abs(p)) <= max(0.06, abs(p) * 0.002) for p in pool)


ABSENT = re.compile(r"\b(?:record|it|they|management)\b[^.]{0,20}\b(?:does not|doesn't|did not|didn't|has not|hasn't|do not|don't)\b", re.I)
MGMT = re.compile(r"\bmanagement\b", re.I)
# A verdict a sentence pins on a company, and what its own source must say for it to stand.
VERDICTS = [(r"\bbehind\b", r"\bbehind\b"), (r"\bahead\b", r"\bahead\b"), (r"\bmiss(?:ed|es)?\b", r"\bmissed\b"),
            (r"\bkept\b|\bdelivered\b", r"\bkept\b|\bdelivered\b"), (r"\bbeat\b", r"\bbeat\b"),
            (r"\bgone quiet\b|\bwent quiet\b", r"\bgone quiet\b"), (r"\bdelayed\b", r"\bdelayed\b"),
            (r"\bcut\b|\blowered\b", r"\blowered\b|\bcut\b"), (r"\braised\b", r"\braised\b")]
MGMT_SAID = re.compile(r"\bmanagement (?:has )?(?:explained|said|stated|noted|attributed this to)(?: that)?", re.I)
MGMT_WHY = re.compile(r"\bmanagement(?:'s|’s) (?:own )?(?:explanation|reason)", re.I)


def mentions(text: str, src: dict) -> bool:
    """The sentence names this company, by ticker or by name."""
    name = NAME_TAIL.sub("", src["label"]).strip()
    return bool(re.search(rf"(?<![A-Za-z]){re.escape(src.get('ref', ''))}(?![A-Za-z])", text)) or name.lower() in text.lower()


def _pct(a: float, b: float) -> str:
    return f"{(a / b - 1) * 100:+.1f}%"


def _day(iso: str) -> str:
    """'2026-10-30' -> '30 Oct 2026': how answers write dates, so their numbers check out."""
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {d:%b %Y}"


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
        notes = " ".join(f"FINN's note: {n['text']}" for n in h.get("notes") or [])
        lines = {LINE_OF.get(x["label"], x["label"].lower()): [v for v in (x["value"], x.get("yoy"), x.get("qoq")) if v is not None]
                 for x in h["rows"]}
        if m.get("now") is not None:
            lines["margin"] = [v for v in (m["now"], m.get("year_ago"), m.get("prev")) if v is not None]
        out["results"] = {"kind": "results", "label": f"{h['quarter']} results", "lines": lines, "noted": nums(notes),
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
            f"Promise '{t['title']}'. Due {t['period']}. A {KIND[t['kind']]}, target {t['target'] or 'none stated'}; "
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
        out[f"F{i}"] = {"kind": "filing", "label": f"{f['label']}, {_day(f['at'])}", "ref": f["id"],
                        "text": f"Filing on {_day(f['at'])} ({f['label']}): {f.get('summary') or f['subject']}"}

    if prices and len(prices) > 1:
        last_d, last = prices[-1][0], prices[-1][1]
        # the same windows as the app's chart: the last n trading days, first close to last
        changes = ", ".join(f"{k} {_pct(last, prices[-n:][0][1])}" for k, n in
                            (("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 260)) if len(prices) >= min(n, 200))
        year = prices[-260:]
        closes = [p[1] for p in year]
        months = {}
        for d, c, *_ in year:
            months[d[:7]] = (d, c)  # the last close of each month
        month_end = ", ".join(f"{date.fromisoformat(d):%b %Y} ₹{c:,.2f}" for d, c in months.values())
        days = sorted(((year[i][1] / year[i - 1][1] - 1) * 100, year[i][0]) for i in range(1, len(year)))
        big = "; ".join(f"{_day(d)} {m:+.1f}%" for m, d in days[:3] + days[-3:][::-1] if abs(m) >= 3)
        recent = "; ".join(f"{_day(year[i][0])} ₹{year[i][1]:,.2f} ({(year[i][1] / year[i - 1][1] - 1) * 100:+.1f}%)"
                           for i in range(max(1, len(year) - 5), len(year)))
        out["price"] = {"kind": "price", "label": "Share price", "text": (
            f"Share price ₹{last:,.2f} at the {_day(last_d)} close. Change: {changes}. "
            f"Over the period: high ₹{max(closes):,.2f}, low ₹{min(closes):,.2f}. Month-end closes: {month_end}."
            + (f" Biggest one-day moves: {big}." if big else "") + f" Last five sessions: {recent}.")}
    return out


def check(raw: dict, srcs: dict[str, dict]) -> list[dict]:
    """The sentences code can stand behind, with their sources."""
    kept = []
    for s in raw.get("sentences", []):
        ids = [i for i in s.get("sources", []) if i in srcs]
        text = tidy(re.sub(r"\s*\[[^\]]*\]", "", THREAD_ID.sub(lambda m: srcs.get(m.group(), {}).get("label", ""), s.get("text", ""))).strip())
        if not text or ADVICE_A.search(text):
            continue
        if not ids:  # only a plain "the record doesn't say" may stand without a source
            if ABSENT.search(text) and not nums(text):
                kept.append({"text": text, "sources": []})
            continue
        # Only promises quote management. A sentence putting FINN's note or a filing summary
        # in management's mouth is reworded, or dropped if it can't be.
        if not any(srcs[i]["kind"] == "promise" for i in ids) and MGMT.search(text):
            text = MGMT_SAID.sub("The record notes that", text)
            text = MGMT_WHY.sub("The record's explanation", text)
            if MGMT.search(text):
                continue
            text = text[0].upper() + text[1:]
        pool = nums(" ".join(srcs[i]["text"] for i in ids))
        if any(not near(v, pool) for v in nums(text)):
            continue  # a number the cited sources don't contain
        # A company named in the sentence must have, in its own source, any verdict the sentence
        # gives it: "Infosys and BEL are behind pace" fails when only BEL is behind.
        if any(re.search(said, text, re.I) and not re.search(proof, srcs[i]["text"], re.I)
               for i in ids if srcs[i]["kind"] == "company" and mentions(text, srcs[i])
               for said, proof in VERDICTS):
            continue
        with_lines = [i for i in ids if srcs[i].get("lines")]
        if len(with_lines) == 1 and mislabelled(text, srcs[with_lines[0]]["lines"], srcs[with_lines[0]].get("noted", [])):
            continue  # one company's results: each number must sit under the line its clause names
        kept.append({"text": text, "sources": ids})
    return kept


def _digest() -> list[dict]:
    p = WEB_DATA / "filings.json"
    return json.loads(p.read_text())["items"] if p.exists() else []


def _prices() -> dict:
    p = WEB_DATA / "prices.json"
    return json.loads(p.read_text())["series"] if p.exists() else {}


def _earlier(history: list[dict] | None) -> str:
    return "".join(f"\nQ: {h['q'][:300]}\nA: {h['a'][:600]}" for h in (history or [])[-2:]
                   if isinstance(h, dict) and h.get("q") and h.get("a"))


def _answer(system: str, head: str, srcs: dict[str, dict], question: str, history: list[dict] | None,
            covers: str = COVERS) -> dict:
    """Ask the model with these sources, keep what code can stand behind, say the rest in code."""
    record = "\n".join(f"[{k}] {v['text']}" for k, v in srcs.items())
    earlier = _earlier(history)
    prompt = (f"{head} Today is {date.today():%d %b %Y}.\n\nSources:\n{record}\n\n"
              + (f"Earlier in this conversation:{earlier}\n\n" if earlier else "") + f"Question: {question.strip()}")
    raw = complete_json(system, prompt, SCHEMA)
    # Nothing in the record bears on it: say so in code, not with whatever the model padded with.
    sentences = check(raw, srcs) if raw.get("answerable", True) else []
    if sentences:
        out = sentences
    elif raw.get("answerable", True) and raw.get("sentences"):  # an answer came back but none of it checked out
        out = [{"text": "FINN couldn't check an answer to that against the record, so it won't guess. Try asking it another way.", "sources": []}]
    else:
        out = [{"text": f"The record doesn't cover that. {covers}", "sources": []}]
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


def ask(symbol: str, question: str, ledger: dict, history: list[dict] | None = None) -> dict:
    """`history`: the last exchanges in this conversation, [{q, a}], so "why?" has a referent."""
    filings = [f for f in _digest() if f["symbol"] == symbol]
    prices = _prices().get(symbol) or ledger.get("price")
    srcs = sources(ledger, filings, prices)
    return _answer(SYSTEM, f"Company: {ledger.get('name', symbol)} ({symbol}).", srcs, question, history)


# ---- the whole portfolio ---------------------------------------------------------------

LIVE = ("tracking", "on_track", "behind", "delayed", "open")
NAME_TAIL = re.compile(r"\b(ltd|limited|industries|technologies|technology|company|corporation|india|enterprises)\b\.?", re.I)


def brief(ledger: dict, filings: list[dict], prices: list[list] | None) -> dict:
    """One company in a paragraph: enough to answer "which of my companies…" across a portfolio."""
    full = sources(ledger, filings, prices)
    r = ledger.get("record") or {}
    bits = [f"{ledger['name']} ({ledger['symbol']}), {ledger.get('sector') or 'sector not given'}. "
            f"Promise record: {r.get('kept', 0)} kept, {r.get('missed', 0)} missed, {r.get('quiet', 0)} gone quiet, "
            f"{r.get('open', 0)} open; {r.get('moved', 0)} moved. Latest call: {ledger.get('latest_call') or 'none'}."]
    res = full.get("results")
    if res:
        bits.append(res["text"])
    if full.get("price"):
        bits.append(full["price"]["text"].split(" Over the period:")[0].rstrip(".") + ".")
    hard = [t for t in ledger.get("threads", []) if t["kind"] in ("hard", "milestone")]
    live = sorted((t for t in hard if t["status"] in LIVE), key=lambda t: (t.get("pace") or {}).get("status") != "behind")[:6]
    if live:
        bits.append("Running promises: " + "; ".join(
            f"'{t['title']}' due {t['period']}, target {t['target'] or 'none stated'}, {t['status'].replace('_', ' ')}"
            + (f" ({t['pace']['so_far_label']} at {t['pace']['so_far']:.1f}%, {t['pace']['status'].replace('_', ' ')})" if t.get("pace") else "")
            for t in live) + ".")
    missed = [t for t in hard if t["status"] == "missed"][:3]
    if missed:
        bits.append("Missed: " + "; ".join(f"'{t['title']}' (target {t['target'] or 'none stated'}, {t.get('detail') or ''})".replace(", )", ")")
                                           for t in missed) + ".")
    call = ledger.get("latest_call")
    moved = [(t, e) for t in hard for e in t["events"] if e["call"] == call and e["type"] != "reaffirmed"][:3]
    if moved:
        bits.append(f"Moved on the {call} call: " + "; ".join(f"'{t['title']}' {e['type']} from {e['from']} to {e['to']}" for t, e in moved) + ".")
    recent = [f for f in filings if not f.get("routine")][:2]
    if recent:
        bits.append("Recent filings: " + "; ".join(f"{_day(f['at'])} {f['label']}: {f.get('summary') or f['subject']}" for f in recent))
    out = {"kind": "company", "label": ledger["name"], "ref": ledger["symbol"], "text": " ".join(bits)}
    if res:
        out |= {"lines": res["lines"], "noted": res.get("noted", [])}
    return out


def named(question: str, ledgers: dict[str, dict]) -> list[str]:
    """Companies the question names, by ticker or by name, at most three."""
    q = question.lower()
    found = []
    for sym, l in ledgers.items():
        name = NAME_TAIL.sub("", l["name"]).strip().lower()
        if re.search(rf"(?<![a-z]){re.escape(sym.lower())}(?![a-z])", q) or (len(name) >= 3 and name in q):
            found.append(sym)
    return found[:3]


def ask_portfolio(question: str, ledgers: dict[str, dict], history: list[dict] | None = None) -> dict:
    """`ledgers`: symbol -> record, for every company in the reader's portfolio the server has."""
    digest, prices = _digest(), _prices()
    srcs: dict[str, dict] = {}
    for sym, l in ledgers.items():
        srcs[sym] = brief(l, [f for f in digest if f["symbol"] == sym], prices.get(sym) or l.get("price"))
    # Companies the question (or the conversation so far) names get their full record too.
    for sym in named(question + " " + _earlier(history), ledgers):
        l = ledgers[sym]
        short = NAME_TAIL.sub("", l["name"]).strip()
        for k, v in sources(l, [f for f in digest if f["symbol"] == sym], prices.get(sym) or l.get("price")).items():
            key = k if THREAD_ID.fullmatch(k) else f"{sym}:{k}"
            srcs[key] = v | {"label": v["label"] if v["kind"] == "promise" else f"{short} · {v['label']}"}
    return _answer(PORTFOLIO_SYSTEM, f"The investor's portfolio: {len(ledgers)} companies.", srcs, question, history,
                   covers="It covers each company's promises, latest results, recent filings and share price.")
