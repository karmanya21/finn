"""Cut a transcript down to the passages that could contain a promise, before any LLM sees it.

A promise needs a forward-looking cue ("expect", "guidance", "by FY28", "will commission")
and something checkable (a number, a period, a band word like "double-digit"). We keep
sentences with both, plus one sentence of context either side, grouped under their speaker
and page. Everything else (macro commentary, results recaps, pleasantries) is dropped.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

PAGE = re.compile(r"^\[\[page (\d+)\]\]$")
# "Name:" with or without text after it, or a name alone on its line (Infosys style).
SPEAKER_INLINE = re.compile(r"^([A-Z][A-Za-z.'’ -]{1,40}?):\s*(.*)$")
NAME_LINE = re.compile(r"^(?:(?:Mr|Ms|Mrs|Dr)\.?\s+)?[A-Z][A-Za-z.'’-]*(?:\s+[A-Z][A-Za-z.'’-]*){0,4}$")

FORWARD = re.compile(
    r"\b(expect\w*|guid\w*|outlook|target\w*|aim\w*|aspir\w*|plan\w*|will|would be|should be|"
    r"going forward|next (?:year|quarter|few)|on track|trajectory|intend\w*|commission\w*|"
    r"anticipat\w*|envisag\w*|look(?:ing)? (?:at|to)|maintain\w*|sustain\w*|reiterat\w*|"
    r"revis\w*|narrow\w*|rais\w*|lower\w*|cut|deliver\w*|achiev\w*|reach\w*|by (?:the end of|end of))\b",
    re.I,
)
CHECKABLE = re.compile(
    r"(\d[\d,.]*\s*(?:%|per ?cent|bps|basis points|x\b|crore|cr\b|lakh|bn|billion|mn|million|"
    r"MTPA|KTPA|GW|MW|beds|stores|tons?|tonnes)|(?:\b|[QH][1-4]\s?)FY\s?'?\d{2}\b|\b[QH][1-4]\b|"
    r"\bCY\s?'?\d{2}\b|\b(?:calendar|financial|fiscal) year\b|\b(?:this|current|next|coming) "
    r"(?:quarter|year|half)\b|\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\b|"
    r"\b(?:single|double|high|low|mid)[- ](?:single[- ])?(?:digit|teens?|twenties)\b|"
    r"\b(?:20[2-3]\d)\b|\$\s?\d|₹\s?\d|Rs\.?\s?\d|INR\s?\d|\d+\s?(?:to|-|–)\s?\d+\s?%)",
    re.I,
)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(“\"])")
BOILERPLATE = re.compile(r"forward-looking statements|disclaimer|listen-only|touch-tone|"
                         r"conference is being recorded", re.I)


@dataclass
class Turn:
    speaker: str
    page: int
    lines: list[str] = field(default_factory=list)


@dataclass
class Excerpt:
    speaker: str
    page: int
    text: str


def _speakers(lines: list[str], repeated: set[str]) -> set[str]:
    """Names used as speaker markers: 'Name:' labels, or short name lines that recur."""
    lines = [l for l in lines if l not in repeated]
    inline = Counter(m.group(1).strip() for l in lines if (m := SPEAKER_INLINE.match(l))
                     and len(m.group(1).split()) <= 5)
    alone = Counter(l for l in lines if NAME_LINE.match(l) and 1 <= len(l.split()) <= 5)
    # A one-question journalist appears twice: in the participant list and when speaking.
    return {n for n, k in inline.items() if k >= 2} | {n for n, k in alone.items() if k >= 2}


def turns(text: str) -> list[Turn]:
    raw = [l.strip() for l in text.splitlines()]
    raw = [l for l in raw if l]
    # Drop running headers/footers: lines that sit at the top or bottom of many pages
    # ("Page 3 of 26", company name, classification labels). Position matters: speaker
    # labels also repeat, but not at page edges.
    pages: list[list[str]] = [[]]
    for l in raw:
        if PAGE.match(l):
            pages.append([])
        else:
            pages[-1].append(l)
    edges = Counter(l for p in pages for l in set(p[:3] + p[-3:]))
    often = max(3, len(pages) // 3)
    # A frequent speaker's name also lands near page edges a lot; a real header is on
    # nearly every page, so name-like lines need a much higher bar.
    def is_header(l: str, k: int) -> bool:
        name_like = NAME_LINE.match(l) or ((m := SPEAKER_INLINE.match(l)) and not m.group(2))
        return len(l) < 100 and k >= (max(3, int(len(pages) * 0.6)) if name_like else often)
    repeated = {l for l, k in edges.items() if is_header(l, k)}
    # "Label : same text" verbatim on many pages is a stamp, not someone speaking.
    repeated |= {l for l, k in Counter(raw).items()
                 if k >= often and (m := SPEAKER_INLINE.match(l)) and m.group(2)}
    names = _speakers(raw, repeated)
    out: list[Turn] = []
    page, cur = 1, Turn("UNKNOWN", 1)
    for l in raw:
        if m := PAGE.match(l):
            page = int(m.group(1))
            continue
        if l in repeated or re.match(r"^Page \d+ of \d+$", l):
            continue
        m = SPEAKER_INLINE.match(l)
        if m and m.group(1).strip() in names:
            out.append(cur)
            cur = Turn(m.group(1).strip(), page, [m.group(2)] if m.group(2) else [])
        elif l in names:
            out.append(cur)
            cur = Turn(l, page)
        else:
            cur.lines.append(l)
    out.append(cur)
    return [t for t in out if t.lines]


def questioners(turns: list[Turn]) -> set[str]:
    """Analysts and journalists: the speakers the moderator (or IR host) hands the line to
    ("the next question is from the line of X"). Precise where "asks questions" isn't:
    management answers can contain question marks too."""
    hosts = " ".join(" ".join(t.lines) for t in turns if t.speaker.lower() in ("moderator", "operator"))
    intro = re.findall(r"question(?:s)?\s+(?:is\s+|comes\s+)?from(?:\s+the\s+line\s+of)?\s+([A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*){0,3})", hosts)
    names = {n.strip().split()[-1].lower() for n in intro}
    return {t.speaker for t in turns if t.speaker.split()[-1].lower().strip(".") in names}


def speaker_of(turns: list[Turn], quote: str, norm) -> str | None:
    """Who actually said quote (norm: the comparison normaliser), whatever the model wrote."""
    head = norm(quote)[:80]
    for t in turns:
        if head and head in norm(" ".join(t.lines)):
            return t.speaker
    return None


def excerpts(text: str, context: int = 1) -> list[Excerpt]:
    out = []
    for t in turns(text):
        if t.speaker.lower() == "moderator":
            continue
        sents = [s.strip() for s in SENTENCE.split(" ".join(t.lines)) if s.strip()]
        hits = [i for i, s in enumerate(sents)
                if FORWARD.search(s) and CHECKABLE.search(s) and not BOILERPLATE.search(s)]
        if not hits:
            continue
        keep = sorted({j for i in hits for j in range(i - context, i + context + 1)
                       if 0 <= j < len(sents)})
        # Join consecutive kept sentences; mark gaps so the model doesn't read across them.
        chunks, prev = [], None
        for j in keep:
            if prev is not None and j != prev + 1:
                chunks.append("[…]")
            chunks.append(sents[j])
            prev = j
        out.append(Excerpt(t.speaker, t.page, " ".join(chunks)))
    return out


def speaker_stats(text: str) -> list[tuple[str, int]]:
    """Words spoken per speaker, most first; management usually dominates."""
    words = Counter()
    for t in turns(text):
        words[t.speaker] += sum(len(l.split()) for l in t.lines)
    return words.most_common()


def render(exs: list[Excerpt]) -> str:
    return "\n\n".join(f"[p{e.page}] {e.speaker}: {e.text}" for e in exs)
