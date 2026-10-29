"""Group a company's promises from many calls into threads: one thread per commitment.

"FY27 revenue growth 10-12%" said in May, reaffirmed in July and cut in October is one
thread with three statements. The model only decides which statements belong together;
what changed between them (raised, cut, deferred) is computed in code from the numbers.
"""

from finn import config
from finn.llm.client import complete_json

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
    return threads
