"""The filings digest: every exchange announcement across the portfolio, classified,
ranked by importance, routine collapsed, and read against the promise records.

Classification is rules first (BSE's own subcategories map onto the brief's categories),
the cheap model only for vague "General"/"Others" filings and for reading the PDF of
material ones. Importance is computed in code, so the ranking is explainable.
"""

import re
from collections import Counter

from finn.llm.client import complete_json
from finn.sources import bse

# ---- categories ------------------------------------------------------------------

LABEL = {
    "results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "management_change": "Management",
    "litigation": "Legal", "regulatory": "Regulatory", "insider_trading": "Insider trade", "earnings_call": "Earnings call",
    "investor_meetings": "Investor meet", "credit_rating": "Credit rating", "dividend": "Dividend",
    "bonus_split": "Bonus / split", "capex": "Capex", "press_release": "Update", "postal_ballot": "Postal ballot",
    "agm_egm": "AGM / EGM", "pledging": "Pledge", "unclassified": "Routine",
}

# Base importance (0-100). Routine (< ROUTINE) is collapsed in the app.
WEIGHT = {
    "results": 90, "M&A": 85, "management_change": 75, "litigation": 70, "regulatory": 70, "fund_raise": 70,
    "capex": 65, "credit_rating": 60, "pledging": 60, "insider_trading": 50, "bonus_split": 45,
    "press_release": 35, "dividend": 35, "earnings_call": 20, "agm_egm": 15, "postal_ballot": 15,
    "investor_meetings": 10, "unclassified": 5,
}
ROUTINE = 30

SUBCATEGORY = [  # (regex on "category / subcategory / subject", category); first match wins
    # Drug regulators: inspections, warning letters, import alerts. For a pharma company
    # this is among the most material news there is, whatever BSE files it under.
    (r"USFDA|US ?FDA|\bFDA\b|Warning Letter|Form 483|Import Alert|Establishment Inspection|"
     r"\bEIR\b|\bOAI\b|\bVAI\b|EU ?GMP|MHRA|WHO ?GMP", "regulatory"),
    # Newspaper copies of results are paperwork, whatever their subject says; so is the
    # notice that a board *will* meet to consider results.
    (r"Newspaper|Publication of Financial Results|Advertisement|Board Meeting Intimation|"
     r"Intimation of Board Meeting", "unclassified"),
    # A notice that a call or meeting *will* happen, whatever results it says it's about.
    (r"Investor Meet - Intimation|Intimation of (Earnings |Conference |Analyst)|Schedule of (Analyst|Investor|Earnings)|"
     r"Earnings Call Intimation|Conference Call (Intimation|with Investors)", "investor_meetings"),
    (r"Financial Results|Result\b|Outcome of Board Meeting.*Results|Results? For The Quarter", "results"),
    (r"Change in Management|Change in Directorate|Resignation|Cessation|Retirement|Appointment", "management_change"),
    (r"Acquisition|Joint Venture|Amalgamation|Merger|Demerger|Scheme of Arrangement|Disinvestment|Restructuring|Divest", "M&A"),
    (r"Qualified Institutional|Preferential Issue|Rights Issue|Issue of Securities|Buy ?back|Commercial Paper|"
     r"Debentures|NCD|Fund Rais|Capital Raise|Enabling Resolution", "fund_raise"),
    (r"Allotment of ESOP|ESPS|ESOS", "unclassified"),
    (r"Credit Rating", "credit_rating"),
    (r"Pledge|Encumbrance", "pledging"),
    (r"Trading Window", "unclassified"),
    (r"Insider Trading|SAST|Reg\.? ?29|Reg\.? ?7\(2\)", "insider_trading"),
    (r"Bonus|Split|Sub-division", "bonus_split"),
    (r"Dividend|Record Date", "dividend"),
    (r"Earnings Call|Audio|Video Recording|Transcript", "earnings_call"),
    (r"Analyst|Investor Meet|Investor Conference|Investor Presentation", "investor_meetings"),
    (r"Postal Ballot", "postal_ballot"),
    (r"AGM|EGM|General Meeting|Scrutinizer", "agm_egm"),
    # A board outcome can be anything (dividend, fund raise, appointment): read the PDF.
    (r"Outcome of (the )?Board Meeting|Board Meeting Outcome", "vague"),
    (r"Board Meeting(?! Outcome)|Intimation", "unclassified"),
    (r"Award of Order|Receipt of Order|Bagging|Order Win|Monthly Business|Memorandum of Understanding|"
     r"Agreement|Press Release|Media Release|Launch", "press_release"),
    (r"Capacity|Plant|Commission|Capex|Expansion", "capex"),
    (r"Penalty|Show Cause|Litigation|Demand Order|Tax Demand|GST|Court|Tribunal|NCLT|SEBI Order|Notice from", "litigation"),
    (r"Newspaper|Certificate|Compliance|Reg\. 74|Half Yearly Report|Monitoring Agency|Loss of Share|"
     r"Duplicate|Shareholding Pattern|Closure", "unclassified"),
]
VAGUE = re.compile(r"^(Company Update|Others) / (General|)$")

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(LABEL)},
        "summary": {"type": "string", "description": "One plain sentence, at most 22 words, with the key fact or number"},
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "change": {"type": "string", "enum": ["appointed", "resigned", "retired", "ceased", "redesignated"]},
                    "effective": {"type": ["string", "null"]},
                    "profile": {"type": ["string", "null"], "description": "Background from the filing's brief profile, at most 40 words; null if none"},
                },
                "required": ["name", "role", "change", "effective", "profile"],
            },
        },
        "downgrade": {"type": "boolean", "description": "Credit rating lowered or outlook cut"},
    },
    "required": ["category", "summary", "people", "downgrade"],
}

SUMMARY_SYSTEM = """You read one Indian stock-exchange filing and summarise it for an investor.
- category: what the filing is really about (results, M&A, fund_raise, management_change, litigation, regulatory (drug-regulator inspections, warning letters, approvals), insider_trading, earnings_call, investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, agm_egm, pledging, unclassified for routine paperwork).
- summary: the key fact in one sentence: who, what, how much. No filler like "The company has informed".
- people: for appointments, resignations, retirements: each person, their role, the change, effective date, and the background given in the filing's brief profile (prior roles, employers, qualifications). Empty list otherwise.
Use only the filing text. Do not add outside knowledge."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "category": {"type": "string", "enum": list(LABEL)}},
        "required": ["id", "category"]}}},
    "required": ["labels"],
}

CLASSIFY_SYSTEM = """Classify Indian stock-exchange announcements by their subject line into one category each:
results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, investor_meetings,
credit_rating, dividend, bonus_split, capex (new plant/capacity), press_release (order wins, launches,
partnerships, awards, business updates), postal_ballot, agm_egm, pledging, unclassified (routine paperwork,
newspaper ads, compliance certificates, clarifications of no substance)."""


def rule_category(a: dict) -> str | None:
    text = f"{a['category']} / {a['subcategory']} / {a['subject']} {a['headline']}"
    if VAGUE.match(f"{a['category']} / {a['subcategory']}"):
        text = f"{a['subject']} {a['headline']}"  # the subject may still be decisive
    for pat, cat in SUBCATEGORY:
        if re.search(pat, text, re.I):
            return None if cat == "vague" else cat
    return None if VAGUE.match(f"{a['category']} / {a['subcategory']}") else "unclassified"


def classify(company: str, items: list[dict]) -> None:
    """Fill item['type'] for each filing: rules, then one cheap call for the vague rest."""
    vague = []
    for a in items:
        a["type"] = rule_category(a)
        if a["type"] is None:
            vague.append(a)
    if vague:
        lines = "\n".join(f"{a['id']} | {a['subject']} | {a['headline'][:160]}" for a in vague)
        out = complete_json(CLASSIFY_SYSTEM, f"Company: {company}\n\n{lines}", CLASSIFY_SCHEMA)
        got = {x["id"]: x["category"] for x in out["labels"]}
        for a in vague:
            a["type"] = got.get(a["id"], "press_release")


def summarise(company: str, a: dict) -> dict | None:
    """Read a material filing's PDF: a one-line summary and, for appointments, the profile."""
    if not a.get("pdf"):
        return None
    try:
        text = bse.pdf_text(a["pdf"])
    except Exception:  # a missing attachment costs this filing its summary, nothing more
        return None
    if len(text.strip()) < 80:  # scanned image or empty attachment
        return None
    prompt = f"Company: {company}\nFiling: {a['subject']}\n\n{text[:9000]}"
    return complete_json(SUMMARY_SYSTEM, prompt, SUMMARY_SCHEMA)


# ---- promise-awareness -----------------------------------------------------------

# Words too generic to link a filing to a promise ("facility inspection" at one plant isn't
# the inspection promised at another).
STOP = set("the and for with from that this will our are was has have into over company "
           "limited ltd business india update announcement regulation lodr "
           "facility facilities inspection plant plants site sites unit units".split())


def _words(s: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP}


def related_promises(a: dict, ledger: dict, limit: int = 3) -> list[str]:
    """Open milestone/number threads this filing plausibly touches (shared topical words)."""
    if not ledger:
        return []
    fw = _words(f"{a['subject']} {a['headline']} {a.get('summary') or ''}")
    threads = ledger.get("threads", [])
    open_ = [t for t in threads if t["kind"] in ("milestone", "hard") and t["status"] not in ("said", "lapsed")]
    words = {t["id"]: _words(f"{t['title']} {t.get('scope') or ''}") for t in threads}
    # A word in many of this company's promises ("cement" at UltraTech, "project" at Tata
    # Power, "order" at BEL) says nothing about which one a filing touches.
    df = Counter(w for ws in words.values() for w in ws)
    hits = []
    for t in open_:
        tw = {w for w in words[t["id"]] if df[w] < 3}
        n = len(fw & tw)
        if n >= 2 or (n == 1 and t["kind"] == "milestone" and len(tw) <= 3):
            hits.append((n, t["id"]))
    return [i for _, i in sorted(hits, reverse=True)[:limit]]


def results_promises(ledger: dict) -> list[str]:
    """Threads the latest results speak to: numbers checked or paced against reported financials."""
    out = []
    for t in ledger.get("threads", []):
        if t["kind"] == "hard" and t.get("basis") == "financials" and t["status"] in (
                "tracking", "delivered", "beat", "missed"):
            out.append(t["id"])
    return out[:4]


def speaker_promises(name: str, ledger: dict) -> list[str]:
    """Threads where this person made the statement: what leaves with them."""
    parts = [p for p in re.findall(r"[A-Za-z]+", name) if len(p) > 2]
    if not parts:
        return []
    last = parts[-1].lower()
    first = parts[0].lower()
    out = []
    for t in ledger.get("threads", []):
        if t["kind"] == "soft":
            continue
        for s in t["statements"]:
            sp = s["speaker"].lower()
            if last in sp and (first in sp or len(parts) == 1 or sp.split()[0].rstrip(".") == first[0]):
                out.append(t["id"])
                break
    return out


# Which way a regulator's outcome points. Positive patterns first: "zero observations"
# also contains "observations".
REG_GOOD = re.compile(r"\b(zero|nil|no)\s+(form\s*)?(483\s*)?observations?|\bEIR\b|establishment inspection report|"
                      r"\bVAI\b|voluntary action indicated|\bNAI\b|no action indicated|close[d-]?\s?out|"
                      r"approv(al|ed)|compliance status|successfully completed", re.I)
REG_BAD = re.compile(r"warning letter|\bOAI\b|official action indicated|import alert|untitled letter|"
                     r"suspen(?:d|ded|sion)|revok\w*|revocation|cancell?(?:ed|ation) of (?:its |the )?licen[cs]e|"
                     r"show[- ]cause|penalt(?:y|ies)|debar\w*|ban(?:ned)? (?:on|from)|"
                     r"\b\d+\s+(form\s*)?483|observations?\s+(were|was|have been)\s+issued|issued\s+(a\s+)?form\s*483", re.I)


def regulatory_tone(text: str) -> str | None:
    if REG_GOOD.search(text):
        return "positive"
    if REG_BAD.search(text):
        return "negative"
    return None
