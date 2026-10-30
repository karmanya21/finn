"""Group a company's promises from many calls into threads: one thread per commitment.

"FY27 revenue growth 10-12%" said in May, reaffirmed in July and cut in October is one
thread with three statements. The model only decides which statements belong together;
what changed between them (raised, cut, deferred) is computed in code from the numbers.
"""

import re

from finn import config, fiscal
from finn.llm.client import complete_json
from finn.promises.resolve import parse_period

SCHEMA = {
    "type": "object",
    "properties": {
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short label without the target, e.g. 'FY27 revenue growth', 'Zone IV commissioning'"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "ids"],
            },
        }
    },
    "required": ["threads"],
}

SYSTEM = """You group management promises from a company's successive earnings calls into threads.

A thread is ONE underlying commitment followed over time:
- the same metric, for the same business scope, for the same target period (FY27 revenue growth said in May, repeated in July, revised in October = one thread);
- a milestone about the same project, plant, JV or transaction, even if its deadline later moves (Zone IV commissioning "in FY27", then "by Q4" = one thread);
- the same multi-year plan target restated across calls.
Different target periods are different threads (FY26 revenue growth and FY27 revenue growth are separate). Different segments or subsidiaries are different threads. Repeats within one call belong to the same thread.

Every id must appear in exactly one thread. A promise with no match is its own thread."""


def _line(pid: str, call: str, p: dict) -> str:
    rng = "" if p.get("low") is None and p.get("high") is None else \
        f" {p.get('low')}–{p.get('high')}{p.get('unit') or ''}"
    return (f"{pid} | {call} | {p['kind']} | {p['metric_label']} | scope: {p['scope']} | "
            f"period: {p['period']}{' deadline ' + p['deadline'] if p.get('deadline') else ''}"
            f"{rng} | \"{p['quote'][:180]}\"")


def link(company: str, calls: list[dict]) -> list[dict]:
    """calls: [{call, promises: [...]}] oldest first. Returns [{title, ids}] with ids 'c{i}p{j}'."""
    lines = [_line(f"c{i}p{j}", c["call"], p)
             for i, c in enumerate(calls) for j, p in enumerate(c["promises"])]
    if not lines:
        return []
    prompt = f"Company: {company}\nPromises (id | call | kind | metric | scope | period | target | quote):\n\n" + "\n".join(lines)
    out = complete_json(SYSTEM, prompt, SCHEMA, model=config.MODEL_STRONG, fallback=config.MODEL_CHEAP)

    valid = {l.split(" | ")[0] for l in lines}
    seen: set[str] = set()
    threads = []
    for t in out["threads"]:
        ids = [i for i in t["ids"] if i in valid and i not in seen]
        seen.update(ids)
        if ids:
            threads.append({"title": t["title"], "ids": ids})
    for i in sorted(valid - seen):  # anything the model dropped stands alone
        c, p = map(int, i[1:].split("p"))
        threads.append({"title": calls[c]["promises"][p]["metric_label"], "ids": [i]})
    return regroup([part for t in threads for part in split(t, calls)], calls)


def dimension(unit: str | None) -> str | None:
    """What a target measures, so ₹ cr and % (or ₹ cr and ₹/t) never share a thread.
    None for bare counts, which can sit with anything."""
    u = (unit or "").lower().strip()
    if not u or re.search(r"\bunits?\b|count|\bnos\b|numbers?|people|employees", u):
        return None
    if re.search(r"per\s*(?:metric\s*)?(?:ton|tonne|t)\b|/\s*t(?:on)?\b", u):
        return "per ton"
    if u in ("%", "percent", "pct"):
        return "%"
    if "bps" in u or "basis" in u:
        return "bps"
    if re.search(r"crore|\bcr\b|billion|\bbn\b|million|\bmn\b|lakh|\brs\b|inr|₹|usd|\$", u):
        return "money"
    if re.search(r"\b[kmg]wh?\b|watt", u):
        return "power"
    if "day" in u:
        return "days"
    return "x" if u in ("x", "times", "×") else u


PERIOD_IN_TITLE = r"\b(?:Q[1-4]|H[12])?\s?FY\s?'?\d{2}\b"


# Targets for one year's flow: FY26 growth and FY27 growth are different promises.
# Stock targets (capacity, debt, order book) say "by FY28"; a later "by FY29" is the same
# promise slipping, so they keep their thread.
PER_YEAR = {"revenue_growth", "revenue", "ebitda_margin", "operating_margin", "ebitda", "pat", "pat_growth",
            "order_inflow", "capex", "volume_growth", "cash_flow", "deal_wins", "loan_growth", "roe"}


def _numbered(p: dict) -> bool:
    return (p.get("low"), p.get("high")) != (None, None)


def _period_key(p: dict, base_fy: int):
    """The fiscal year a numbered per-year target covers. Q3 and full-year FY26 are one
    year here (resolve._comparable already keeps their moves apart); a year two or more
    past base_fy is the long-range plan, the same as "multi-year"."""
    if p.get("metric") not in PER_YEAR or not _numbered(p):
        return None
    if (pp := parse_period(p.get("period"))):
        return "multi" if pp.fy - base_fy >= 2 else pp.fy
    return "multi" if "multi" in (p.get("period") or "").lower() else None


def _member(i: str, calls: list[dict]) -> tuple[str, dict, str]:
    c, j = map(int, i[1:].split("p"))
    return i, calls[c]["promises"][j], calls[c]["call"]


def _fy(call: str) -> int:
    return fiscal.fy_of(fiscal.parse_label(call))


def _id_order(i: str) -> tuple[int, int]:
    return tuple(map(int, i[1:].split("p")))


def split(thread: dict, calls: list[dict]) -> list[dict]:
    """One thread, one commitment. The model sometimes chains FY26 and FY27 capex, or a
    ₹ cr target and a % share, into one thread; then a change between them reads as a
    raise or a cut. Split by what the target measures, and per-year targets by year.
    Milestones keep their thread: a moved deadline is the thing to track.

    The largest part keeps the title; the others take their own statement's label (BEL's
    "FY26 revenue growth" thread had picked up a ₹1,000 cr capex figure)."""
    members = [_member(i, calls) for i in thread["ids"]]
    if sum(p["kind"] in ("milestone", "soft") for _, p, _ in members) * 2 >= len(members):
        return [thread]
    base = min(_fy(call) for _, _, call in members)
    keyed = [(i, _period_key(p, base), dimension(p.get("unit")) if _numbered(p) else None,
              _period_key({**p, "low": 0}, base)) for i, p, call in members]
    if len({k[1] for k in keyed} - {None}) <= 1 and len({k[2] for k in keyed} - {None}) <= 1:
        return [thread]
    groups: dict[tuple, list[str]] = {}
    for i, per, dim, year in sorted(keyed, key=lambda k: (k[1] is None) + (k[2] is None)):
        # Numbered statements first; the rest join a group they match on what is known,
        # preferring their own year ("growth trending up" said about FY27 joins FY27).
        fits = [k for k in groups if (per is None or k[0] == per) and (dim is None or k[1] == dim)]
        match = next((k for k in fits if k[0] == year), fits[0] if fits else (per, dim))
        groups.setdefault(match, []).append(i)
    if len(groups) == 1:
        return [thread]
    main = max(groups, key=lambda k: len(groups[k]))
    labels = {i: p["metric_label"] for i, p, _ in members}
    out = []
    for (per, dim), ids in groups.items():
        ids.sort(key=_id_order)
        title = thread["title"] if (per, dim) == main else labels[ids[-1]]
        label = "multi-year" if per == "multi" else f"FY{per:02d}" if per else ""
        if label == "multi-year":
            title = re.sub(PERIOD_IN_TITLE, "", title).strip() + " (multi-year)"
        elif label and not re.search(rf"\b(?:Q[1-4]|H[12])?{label}\b", title):
            title = re.sub(PERIOD_IN_TITLE, label, title, count=1) if re.search(PERIOD_IN_TITLE, title) else f"{label} {title}"
        out.append({"title": title, "ids": ids, "split": (per, dim) != main})
    return sorted(out, key=lambda t: _id_order(t["ids"][0]))


def title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z&]{3,}", re.sub(PERIOD_IN_TITLE, "", title).lower())
            if w not in ("growth", "target", "guidance", "company", "the", "and", "for")}


def regroup(threads: list[dict], calls: list[dict]) -> list[dict]:
    """A part split off one thread joins the thread it belongs to: the one other thread
    with the same metric, year and unit, or of several, the one whose title it shares
    (BEL's capex figure -> the FY26 capex thread, not FY26 R&D)."""
    def key(t):
        i, p, call = _member(t["ids"][-1], calls)
        unit = dimension(p.get("unit")) if _numbered(p) else None
        return (p["metric"], _period_key(p, _fy(call)) or p.get("period"), unit)
    homes: dict[tuple, list[dict]] = {}
    for t in threads:
        if not t.get("split"):
            homes.setdefault(key(t), []).append(t)
    def words(t):
        return title_words(t["title"])
    # A statement whose own label shares nothing with its thread's title, when another
    # thread with its metric, year and unit does carry its label, moves there (BEL's
    # "capex more than INR1,000 crores" had been filed under R&D investment).
    for t in threads:
        for i in list(t["ids"]) if len(t["ids"]) > 1 else []:
            mine = title_words(_member(i, calls)[1]["metric_label"])
            if not mine or mine & words(t):
                continue
            single = {"title": _member(i, calls)[1]["metric_label"], "ids": [i]}
            home = [h for h in homes.get(key(single), []) if h is not t and mine & words(h)]
            if len(home) == 1:
                t["ids"].remove(i)
                home[0]["ids"] = sorted(home[0]["ids"] + [i], key=_id_order)
    out = []
    for t in threads:
        home = homes.get(key(t), []) if t.pop("split", False) else []
        if len(home) > 1:  # FY26 capex and FY26 R&D are both "capex": go by the label
            best = max(len(words(h) & words(t)) for h in home)
            home = [h for h in home if best and len(words(h) & words(t)) == best]
        if len(home) == 1:
            home[0]["ids"] = sorted(home[0]["ids"] + t["ids"], key=_id_order)
        else:
            out.append(t)
    return out
