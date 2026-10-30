"""Transcript excerpts -> structured promises, via the cheap model.

The model only reads and structures; it never judges whether a promise was kept.
Every returned quote is checked against the source text, and anything that isn't
a verbatim substring is rejected rather than trusted.
"""

import re
from dataclasses import dataclass

from finn import fiscal
from finn.llm.client import complete_json
from finn.promises import prefilter

KINDS = ["hard", "directional", "milestone", "soft"]
METRICS = [
    "revenue_growth", "revenue", "ebitda_margin", "operating_margin", "ebitda", "pat",
    "pat_growth", "order_inflow", "order_book", "capex", "capacity", "volume_growth",
    "debt", "working_capital", "cash_flow", "deal_wins", "asset_quality", "loan_growth", "roe",
    "other",
]
CHANGES = ["new", "reaffirmed", "raised", "lowered", "narrowed", "widened",
           "deferred", "advanced", "unclear"]

SCHEMA = {
    "type": "object",
    "properties": {
        "promises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string", "description": "Verbatim words from the excerpt, 1-2 sentences"},
                    "speaker": {"type": "string"},
                    "page": {"type": "integer"},
                    "kind": {"type": "string", "enum": KINDS},
                    "metric": {"type": "string", "enum": METRICS},
                    "metric_label": {"type": "string", "description": "Short human label, e.g. 'Revenue growth (cc)'"},
                    "scope": {"type": "string", "description": "'company' or the segment/business/plant name"},
                    "basis": {"type": "string", "enum": ["reported", "constant_currency", "adjusted", "unclear"]},
                    "low": {"type": ["number", "null"]},
                    "high": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"], "description": "%, bps, Rs crore, USD bn, MTPA, GW, count, x, ..."},
                    "direction": {"type": ["string", "null"], "enum": ["up", "down", "flat", None]},
                    "period": {"type": "string", "description": "FY27, H2FY27, Q3FY27, FY28, or 'multi-year'"},
                    "deadline": {"type": ["string", "null"], "description": "For milestones: when it should be done, e.g. Q4FY27"},
                    "condition": {"type": ["string", "null"]},
                    "change": {"type": "string", "enum": CHANGES},
                },
                "required": ["quote", "speaker", "page", "kind", "metric", "metric_label", "scope",
                             "basis", "low", "high", "unit", "direction", "period", "deadline",
                             "condition", "change"],
            },
        }
    },
    "required": ["promises"],
}

SYSTEM = """You extract management's forward-looking commitments from Indian listed-company earnings-call excerpts.

A promise is something MANAGEMENT says the COMPANY will achieve in a FUTURE period. Classify each:
- hard: a number or range for a metric and period ("revenue growth of 1-2% in constant currency for FY27", "EBITDA margin 18-20% this year").
- directional: a stated direction for a metric and period without a number ("margins will improve in H2", "a softer first half with a pickup in the second half" = revenue growth up in H2, "net debt will decline this year").
- milestone: a dated event the company controls: plant or project commissioning, a transaction closing (divestment, acquisition, merger, stake sale), a launch, a capacity going live ("commission the Dahej plant by Q3 FY27", "both transactions are expected to close in Q1 FY27"). Easy to overlook; look for them.
- soft: an aspiration, long-range ambition, or hedged hope ("we aspire to be a $5 bn company", "we hope demand recovers").

Rules:
- Only management speakers. Analysts and journalists ask questions; never attribute their words. If management confirms an analyst's number ("yes, that's right"), quote management's words.
- Skip past results, current-quarter facts, industry or macro forecasts, and statements about clients or the market that are not the company's own target.
- The promise must be about the company's OWN performance or actions. Skip events others decide: government tenders and RFPs, client awards and decisions, regulatory approvals, fare or tariff revisions by authorities.
- Skip routine corporate housekeeping: investor days, AGMs, dividend and record dates, salary-hike timing, leadership succession.
- One metric and one period per promise. Split "revenue 15% and margin 8.5%" into two.
- quote must be copied exactly, character for character, from the excerpt. Keep it short (the key sentence).
- Periods use the Indian fiscal year: FY27 = April 2026 to March 2027. Resolve "this year", "next year", "second half" relative to the call date given.
- metric: use the measure management names. "Operating margin" and "EBIT margin" are operating_margin; only "EBITDA margin" is ebitda_margin.
- "stable at 7.8%" or "maintain margins at 21%" is hard (a number is given): low = high = that number, direction flat.
- low/high: for a point target set both equal. For "at least X" set low only; "up to X" set high only. Growth and margins are in % (unit "%"). Use null when there is no number.
- change: compare with the wording ("we are raising", "revised", "narrowed", "maintain", "pushed to") — use "new" when nothing indicates a revision, "reaffirmed" for maintain/reiterate.
- If there are no promises, return an empty list. Do not invent."""

# A second, narrow pass with the same cheap model. In the eval the general pass caught
# hard numbers reliably but dropped dated milestones and direction-only statements from
# run to run; a focused prompt is cheaper than a stronger model and doesn't depend on
# free-tier capacity for the bigger models.
FOCUS = SYSTEM + """

THIS PASS: return ONLY milestone and directional promises; another pass handles hard numeric guidance and soft aspirations. Read every excerpt for:
- dates or periods attached to commissioning, plant or capacity start-up, project completion, transaction closing (divestment, acquisition, JV), launches ("within this quarter", "during the current financial year", "by H1FY27", "this calendar year");
- a metric's expected direction in a period ("softer first half, pickup in the second half", "net debt will decline this year", "margins should improve in the coming quarters")."""


def _dedupe(promises: list[dict]) -> list[dict]:
    seen, out = set(), []
    for p in promises:
        k = (_norm(p.get("quote", "")), p.get("metric"), p.get("period"))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


@dataclass
class Extraction:
    promises: list[dict]
    rejected: list[dict]  # quote not found verbatim in the source
    excerpt_tokens: int


def _norm(s: str) -> str:
    """Compare on words and numbers only; models drop or add commas and quote marks."""
    s = re.sub(r"[^\w%.]+", " ", s.lower())
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)  # keep decimal points, drop full stops
    return re.sub(r"\s+", " ", s).strip()


CALENDAR = re.compile(r"^CY\s?'?(\d{2})$", re.I)
THIRD_PARTY = re.compile(r"\b(RFP|RFQ|tender\w*|bidders?|shortlist\w*|government|ministry|"
                         r"regulator\w*|tariff order|fare revision|bidding|winner|customer to issue)\b", re.I)


def _normalize(p: dict) -> dict:
    """Deterministic fixes for mistakes the model repeats despite the prompt."""
    # Calendar-year deadlines -> the fiscal quarter they end in (Dec 2026 = Q3FY27).
    for f in ("period", "deadline"):
        if p.get(f) and (m := CALENDAR.match(p[f].strip())):
            p[f] = fiscal.label(int(m.group(1)) + 1, 3)
    # Milestones others control (a government tender, an RFP) aren't the company's to
    # keep; the prompt says so, but the model still lets some through. Keep as context.
    if p.get("kind") == "milestone" and THIRD_PARTY.search(p.get("quote", "")):
        p["kind"] = "soft"
    # The quote names the margin; trust it over the model's label.
    q = p.get("quote", "")
    if p.get("metric") == "ebitda_margin" and re.search(r"operating margin|\bEBIT margin", q, re.I) \
            and not re.search(r"EBITDA", q):
        p["metric"] = "operating_margin"
    # A target only counts if its number is in its own quote. "We remain with this
    # guidance" carries no number; the model borrowing one from context made fake cuts.
    nums = _numbers(q)
    for f in ("low", "high"):
        if p.get(f) is not None and not _supported(p[f], nums):
            p[f] = None
    # One sentence, several targets: "R&D investments more than INR1,600 crores, capex more
    # than INR1,000 crores". The number must sit in the clause naming this metric (BEL).
    for f in ("low", "high"):
        if p.get(f) is not None and _from_another_clause(p[f], p.get("metric_label") or "", q):
            p[f] = None
    if p.get("kind") == "hard" and p.get("low") is None and p.get("high") is None:
        p["kind"] = "directional"
    # "EBITDA per ton of INR1,000" read as ₹1,000 crore (UltraTech)
    if re.search(r"crore|\bcr\b", p.get("unit") or "", re.I) and re.search(r"per (?:metric )?ton|per tonne|/t\b", q, re.I) \
            and not re.search(r"crore|\bcr\b|billion|\bbn\b|lakh", q, re.I):
        p["unit"] = "Rs per ton"
    # "₹12 billion" in a crore target is ₹1,200 cr; the model copies the 12 (Polycab).
    if re.search(r"crore|\bcr\b", p.get("unit") or "", re.I):
        for f in ("low", "high"):
            v = p.get(f)
            if v is not None and (m := re.search(rf"(?<![\d.,]){re.escape(_fmt_num(v))}\s?(billion|bn|million|mn)\b", q, re.I)):
                p[f] = v * (100 if m.group(1).lower() in ("billion", "bn") else 0.1)
    # "2x", "twice", "double" are multiples, not percentages.
    v = p.get("low") if p.get("low") is not None else p.get("high")
    if v is not None and (re.search(rf"(?<![\d.]){re.escape(_fmt_num(v))}\s?(x|X|×|times)\b", q)
                          or (v == 2 and re.search(r"\b(twice|double|2x)\b", q, re.I))):
        p["unit"] = "x"
    return p


PERIODS = re.compile(r"\b(?:Q[1-4]|H[12])?\s?FY\s?'?\d{2,4}\b|\b(?:19|20)\d{2}\b", re.I)
GENERIC_LABEL = {"growth", "target", "guidance", "company", "total", "overall", "level", "amount", "investment",
                 "investments", "spend", "spending", "expenditure", "the", "and", "for"}


def _from_another_clause(v: float, label: str, quote: str) -> bool:
    """v sits in another clause, and the clause naming this metric has a number of its own
    ("R&D more than 1,600, capex more than 1,000": 1,000 isn't R&D's). A clause that only
    names the topic ("On Revenue, we expect 10% to 12%") points to the next one."""
    words = [w for w in re.findall(r"[a-z&]{3,}", label.lower()) if w not in GENERIC_LABEL]
    clauses = [c for c in re.split(r",(?!\d{2})|;|\.\s", quote) if c.strip()]  # not "1,600" or "10 and 12"
    if not words or len(clauses) < 2:
        return False
    named = [c for c in clauses if any(re.search(rf"\b{re.escape(w)}", c, re.I) for w in words)
             and _numbers(PERIODS.sub(" ", c))]
    has_v = [c for c in clauses if _supported(v, _numbers(c))]
    return bool(named) and bool(has_v) and not any(c in named for c in has_v)


WORD_NUMBERS = {"twice": 2, "double": 2, "doubling": 2, "triple": 3, "thrice": 3, "half": 50}


def _numbers(text: str) -> list[float]:
    out = [float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    out += [v for w, v in WORD_NUMBERS.items() if re.search(rf"\b{w}\b", text, re.I)]
    return out


def _supported(v: float, nums: list[float]) -> bool:
    """v appears in the quote, allowing unit rescaling (Rs 50 billion = Rs 5,000 crore)."""
    return any(abs(n * s - v) <= 1e-6 * max(1, abs(v)) for n in nums
               for s in (1, 10, 100, 1000, 0.1, 0.01, 0.001))


def _fmt_num(v: float) -> str:
    return f"{v:g}"


def extract(text: str, company: str, call_label: str, model: str | None = None,
            focused_pass: bool = True) -> Extraction:
    call = fiscal.parse_label(call_label)
    fy, q = fiscal.reported_quarter(call)
    excerpts = prefilter.excerpts(text)
    body = prefilter.render(excerpts)
    speakers = ", ".join(f"{s} ({w} words)" for s, w in prefilter.speaker_stats(text)[:12])
    prompt = (
        f"Company: {company}\nCall date: {call_label} (discussing {fiscal.label(fy, q)} results; "
        f"current fiscal year is FY{fiscal.fy_of(call):02d})\n"
        f"Speakers by words spoken (management usually speaks most): {speakers}\n\n"
        f"Excerpts ([pN] = page; […] = omitted text):\n\n{body}"
    )
    found = complete_json(SYSTEM, prompt, SCHEMA, model=model)["promises"]
    if focused_pass:
        found += complete_json(FOCUS, prompt, SCHEMA, model=model)["promises"]
    source = _norm(body)
    turns = prefilter.turns(text)
    asks = prefilter.questioners(turns)
    good, bad = [], []
    for p in found:
        q = _norm(p.get("quote", ""))
        # Attribute by where the quote sits, not by the model's label; an analyst's
        # question ("you've maintained your $3 billion guidance?") is not a promise.
        who = prefilter.speaker_of(turns, p.get("quote", ""), _norm)
        if who:
            p["speaker"] = who
        (good if q and q in source and who not in asks else bad).append(_normalize(p))
    return Extraction(_dedupe(good), bad, len(body) // 4 * (2 if focused_pass else 1))
