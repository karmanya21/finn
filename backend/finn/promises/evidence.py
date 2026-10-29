"""What did management say later about a promise the financials can't settle?

Order inflow, segment margins, plant commissioning and deal closings don't appear in
screener's tables. The only primary evidence is management's own later statements, so
we retrieve the sentences in later calls that mention the commitment and let the cheap
model classify them. Verdicts from here are labelled 'per management', never presented
as if they were checked against reported financials.
"""

import re

from finn.llm.client import complete_json
from finn.promises import prefilter
from finn.promises.extract import _norm

STOP = set("""the and for with from that this will our are was were has have had into over
under about next year years quarter quarters half first second fy27 fy26 fy28 company
business growth expected expect target guidance plan range""".split())
DONE = re.compile(r"\b(commission\w*|complet\w*|achiev\w*|deliver\w*|delay\w*|defer\w*|"
                  r"revis\w*|postpon\w*|on track|grew|declin\w*|stood at|came in|closed|"
                  r"surpass\w*|exceed\w*|miss\w*|short of|behind|ahead)\b", re.I)

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["delivered", "exceeded", "on_track", "behind",
                                               "delayed", "missed", "no_update"]},
        "reported": {"type": ["string", "null"], "description": "What was reported, in under 12 words, e.g. 'FY26 order inflow +23% vs 10% guided'"},
        "value": {"type": ["number", "null"], "description": "The reported number for the promised metric and period, in the promise's unit; null if none"},
        "lower_is_better": {"type": "boolean", "description": "True for debt, working capital, costs, mix reductions where a lower number is better"},
        "quote": {"type": ["string", "null"], "description": "Verbatim sentence supporting the status"},
        "call": {"type": ["string", "null"]},
        "new_deadline": {"type": ["string", "null"], "description": "If delayed: the new date/period, e.g. Q2FY27"},
    },
    "required": ["status", "reported", "value", "lower_is_better", "quote", "call", "new_deadline"],
}

# A quote that only looks forward can't prove anything was done ("we are going to hire").
FUTURE = re.compile(r"\b(will|going to|expect\w*|plan\w*|should|would|anticipat\w*|intend\w*|aim\w*)\b", re.I)
PAST = re.compile(r"\b(completed|achieved|was|were|stood|closed|grew|delivered|commissioned|reached|"
                  r"improved|declined|ended|came in|did|has been|have been|hired|added)\b", re.I)

SYSTEM = """You check whether a company kept an earlier promise, using ONLY sentences management said in later earnings calls.

Statuses:
- delivered / exceeded: management reports it done or the number met / beaten.
- on_track: management says it is progressing as planned and the deadline hasn't passed.
- behind: progressing but below the promised pace.
- delayed: the date moved later (give new_deadline).
- missed: management reports falling short, or the deadline passed with it undone.
- no_update: the sentences don't clearly speak to THIS promise.

Evidence must be a REPORTED OUTCOME or progress, not the promise repeated. "We are going to hire 20,000" or "we expect closure this quarter" is a restatement, not proof: use no_update (or on_track if management explicitly says it is on track).
If the later figure is on a different definition or segment basis than the promise (e.g. "on this reclassified basis", a renamed or regrouped segment), it cannot settle the promise: use no_update.
For targets where lower is better (working capital, debt, costs, onsite mix), coming in below target is delivered.
Use no_update whenever unsure. quote must be copied exactly from the sentences given."""


def _stem(w: str) -> str:
    """Crude, but enough to match 'inflows'/'inflow' and 'freshers hired'/'fresh hiring'."""
    for suf in ("ers", "ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _words(s: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z][a-z0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


def _keywords(thread: dict) -> set[str]:
    return _words(" ".join([thread["title"], thread["latest"].get("scope", ""),
                            thread["latest"].get("metric_label", "")]))


def candidates(thread: dict, later: list[tuple[str, str]], managers: set[str], k: int = 6) -> list[tuple[str, int, str]]:
    """(call, page, passage) from management turns in later calls, best matches first.

    A passage is the matching sentence plus the next one: the verdict often follows the
    topic ("On order inflows, we had guided 10%. We have significantly surpassed this.")."""
    keys = _keywords(thread)
    if not keys:
        return []
    scored = []
    for call, text in later:
        for t in prefilter.turns(text):
            if t.speaker not in managers:
                continue
            sents = [s.strip() for s in prefilter.SENTENCE.split(" ".join(t.lines)) if s.strip()]
            for i, s in enumerate(sents):
                hit = len(keys & _words(s))
                passage = " ".join(sents[i:i + 2])
                outcome = bool(DONE.search(passage))
                # One topical word is enough if the passage reports an outcome
                # ("The divestment of Nabha Power was completed on 25th June").
                if hit and (hit >= 2 or len(keys) == 1 or outcome):
                    scored.append((hit + outcome, call, t.page, passage))
    scored.sort(key=lambda x: -x[0])
    return [(c, p, s) for _, c, p, s in scored[:k * max(1, len(later))]][: k * 2]


def check(company: str, thread: dict, later: list[tuple[str, str]], managers: set[str]) -> dict | None:
    sents = candidates(thread, later, managers)
    if not sents:
        return {"status": "no_update"}
    p = thread["latest"]
    target = "" if p.get("low") is None and p.get("high") is None else \
        f"Target: {p.get('low')}–{p.get('high')} {p.get('unit') or ''}\n"
    prompt = (f"Company: {company}\nPromise ({thread['title']}), first said {thread['first_call']}, "
              f"last said {thread['last_call']}:\n\"{p['quote']}\"\nPeriod: {p.get('period')}"
              f"{' · deadline ' + p['deadline'] if p.get('deadline') else ''}\n{target}\n"
              "Later management sentences:\n" + "\n".join(f"[{c}, p{pg}] {s}" for c, pg, s in sents))
    out = complete_json(SYSTEM, prompt, SCHEMA)
    q = out.get("quote") or ""
    if q and _norm(q) not in _norm(" ".join(s for _, _, s in sents)):
        return {"status": "no_update"}  # unverifiable evidence is no evidence
    # A delay can be stated in the future tense ("will only commission in April");
    # delivery, misses and progress cannot.
    if out["status"] != "delayed" and FUTURE.search(q) and not PAST.search(q):
        return {"status": "no_update"}
    return out
