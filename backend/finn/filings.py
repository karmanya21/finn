"""The filings digest: every exchange announcement across the portfolio, classified,
ranked by importance, routine collapsed, and read against the promise records.

Classification is rules first (BSE's own subcategories map onto the brief's categories),
the cheap model only for vague "General"/"Others" filings and for reading the PDF of
material ones. Importance is computed in code, so the ranking is explainable.
"""

import re

from finn.llm.client import complete_json
from finn.sources import bse

# ---- categories ------------------------------------------------------------------

LABEL = {
    "results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "management_change": "Management",
    "litigation": "Litigation", "insider_trading": "Insider trade", "earnings_call": "Earnings call",
    "investor_meetings": "Investor meet", "credit_rating": "Credit rating", "dividend": "Dividend",
    "bonus_split": "Bonus / split", "capex": "Capex", "press_release": "Update", "postal_ballot": "Postal ballot",
    "agm_egm": "AGM / EGM", "pledging": "Pledge", "unclassified": "Routine",
}

# Base importance (0-100). Routine (< ROUTINE) is collapsed in the app.
WEIGHT = {
    "results": 90, "M&A": 85, "management_change": 75, "litigation": 70, "fund_raise": 70,
    "capex": 65, "credit_rating": 60, "pledging": 60, "insider_trading": 50, "bonus_split": 45,
    "press_release": 35, "dividend": 35, "earnings_call": 20, "agm_egm": 15, "postal_ballot": 15,
    "investor_meetings": 10, "unclassified": 5,
}
ROUTINE = 30

SUBCATEGORY = [  # (regex on "category / subcategory / subject", category); first match wins
    # Newspaper copies of results are paperwork, whatever their subject says; so is the
    # notice that a board *will* meet to consider results.
    (r"Newspaper|Publication of Financial Results|Advertisement|Board Meeting Intimation|"
     r"Intimation of Board Meeting|Investor Meet - Intimation", "unclassified"),
    (r"Financial Results|Result\b|Outcome of Board Meeting.*Results|Results? For The Quarter", "results"),
    (r"Change in Management|Change in Directorate|Resignation|Cessation|Retirement|Appointment", "management_change"),
    (r"Acquisition|Joint Venture|Amalgamation|Merger|Demerger|Scheme of Arrangement|Disinvestment|Restructuring|Divest", "M&A"),
    (r"Qualified Institutional|Preferential Issue|Rights Issue|Issue of Securities|Buy ?back|Commercial Paper|Debentures|NCD", "fund_raise"),
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
- category: what the filing is really about (results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, agm_egm, pledging, unclassified for routine paperwork).
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
        # The subject may still be decisive ("Board Meeting Outcome for Financial Results").
        for pat, cat in SUBCATEGORY:
            if re.search(pat, f"{a['subject']} {a['headline']}", re.I):
                return cat
        return None
    for pat, cat in SUBCATEGORY:
        if re.search(pat, text, re.I):
            return cat
    return "unclassified"


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

STOP = set("the and for with from that this will our are was has have into over company "
           "limited ltd business india update announcement regulation lodr".split())


def _words(s: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP}


def related_promises(a: dict, ledger: dict, limit: int = 3) -> list[str]:
    """Open milestone/number threads this filing plausibly touches (shared topical words)."""
    if not ledger:
        return []
    fw = _words(f"{a['subject']} {a['headline']} {a.get('summary') or ''}")
    hits = []
    for t in ledger.get("threads", []):
        if t["kind"] not in ("milestone", "hard") or t["status"] in ("said", "lapsed"):
            continue
        tw = _words(f"{t['title']} {t.get('scope') or ''}")
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
