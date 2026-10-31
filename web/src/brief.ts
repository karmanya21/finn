// A filing in brief: the facts that matter, why it matters, and the promises it touches.
// All read from the filing's own summary and the company's record by rules, no model:
// the summary is the model's reading of the PDF; what it means is decided here.

import type { Filing, Ledger, Thread } from "./data";

export interface Fact { label: string; value: string; tone?: "up" | "down" }
export interface Brief { facts: Fact[]; why: string | null; touches: Thread[]; touchLabel: string }

const MONEY = /(?:Rs\.?|INR|₹)\s?([\d,]+(?:\.\d+)?)\s*(lakh crores?|crores?|cr\b)/gi;
const RATING = /\b(CRISIL|CARE|ICRA|IND|India Ratings|Acuite|Brickwork)\s?(AAA|AA\+|AA-|AA|A1\+|A1|A\+|A-|A|BBB\+|BBB-|BBB)(?![\w+-])/;
const CAPACITY = /([\d.,]+)\s*(million sq\.?\s?ft\.?|MTPA|MW|GW|GWh|seats|stores|beds)/i;
const ORDERISH = /\b(orders?|contracts?|deals?|agreements?|awards?|wins?|bags|secur(?:es|ed))\b/i;
const PHARMA = /USFDA|US ?FDA|Form ?483|warning letter|import alert|cGMP|EIR/i;

function money(text: string, max = 2): string[] {
  const all = [...text.matchAll(MONEY)].map((m) => `₹${m[1]}${/lakh/i.test(m[2]) ? " lakh cr" : " cr"}`);
  return [...new Set(all)].slice(0, max);
}

/** The two or three numbers or words the event turns on. */
export function keyFacts(f: Filing): Fact[] {
  const text = `${f.summary ?? ""} ${f.headline}`;
  const out: Fact[] = [];
  if (f.type === "credit_rating") {
    const r = text.match(RATING);
    const act = text.match(/\b(upgrad|downgrad|reaffirm|withdr|assign)\w*/i)?.[1].toLowerCase();
    const word = { upgrad: "Upgraded", downgrad: "Downgraded", reaffirm: "Reaffirmed", withdr: "Withdrawn", assign: "Assigned" }[act ?? ""];
    if (word) out.push({ label: "Action", value: word, tone: act === "upgrad" ? "up" : act === "downgrad" ? "down" : undefined });
    if (r) out.push({ label: "Rating", value: `${r[1]} ${r[2]}` });
    if (/subsidiary/i.test(text)) out.push({ label: "Entity", value: "a subsidiary" });
    return out;
  }
  if (f.type === "dividend") {
    const ps = text.match(/(?:Rs\.?|INR|₹)\s?([\d.]+)\s*(?:\/-)?\s*(?:per (?:equity )?share)/i);
    if (ps) out.push({ label: "Dividend", value: `₹${ps[1]} a share` });
    const rd = text.match(/record date[^\d]{0,40}(\d{1,2})(?:st|nd|rd|th)?\s+([A-Z][a-z]+)/i);
    if (rd) out.push({ label: "Record date", value: `${rd[1]} ${rd[2].slice(0, 3)}` });
    return out;
  }
  if (f.type === "regulatory") {
    const obs = text.match(/\b(zero|no|\d+)\s+(?:Form\s?483\s+)?observations?/i);
    if (obs) {
      const n = /zero|no/i.test(obs[1]) ? 0 : Number(obs[1]);
      out.push({ label: "Observations", value: String(n), tone: n === 0 ? "up" : "down" });
    }
    if (/warning letter|import alert|OAI/i.test(text)) out.push({ label: "Outcome", value: text.match(/warning letter|import alert|OAI/i)![0], tone: "down" });
    if (/without material impact|no material impact/i.test(text)) out.push({ label: "Impact", value: "none material, per company" });
    return out;
  }
  if (f.type === "earnings_call") return out;  // the call's figures are the results; the card says what moved
  const amounts = money(text);
  if (amounts.length) out.push({ label: f.type === "fund_raise" ? "Raising" : f.type === "dividend" ? "Payout" : "Size", value: amounts.join(" + ") });
  const cap = text.match(CAPACITY);
  if (cap) out.push({ label: "Adds", value: `${cap[1]} ${cap[2].replace(/\.$/, "")}` });
  return out;
}

/** One line on why an investor should care, by kind of event and which way it points. */
export function whyItMatters(f: Filing, l?: Ledger | null): string | null {
  const text = `${f.summary ?? ""} ${f.subject}`;
  switch (f.type) {
    case "fund_raise":
      if (/ESOP|stock option|employee/i.test(text)) return "Shares for employee stock options: small dilution, no new money for the business.";
      return l?.snapshot?.lender
        ? "Fresh equity lets a lender keep growing its loan book; existing holders are diluted."
        : "New shares dilute existing holders; what the money is for decides whether it pays back.";
    case "credit_rating": {
      const facts = keyFacts(f);
      const act = facts.find((x) => x.label === "Action")?.value;
      return act === "Upgraded" ? "A higher rating usually means cheaper borrowing."
        : act === "Downgraded" ? "A lower rating raises borrowing costs and can signal strain."
        : "Borrowing costs unchanged; the agency sees no new risk.";
    }
    case "regulatory": {
      const plant = PHARMA.test(text);
      return f.tone === "positive" ? (plant ? "A clean inspection keeps the site cleared to supply the US market." : "A regulator ruled in the company's favour.")
        : f.tone === "negative" ? (plant ? "Regulatory action can stop a site supplying the US until it's resolved."
          : "Action by a regulator; what it costs and how long it lasts is what matters.")
        : "Watch for the regulator's follow-up.";
    }
    case "M&A":
      return /divest|sell|exit|disinvest/i.test(text) ? "Selling a business changes what the company earns from, and frees cash."
        : "Changes what the company owns; guidance doesn't include it unless management said so.";
    case "capex":
      return "Adds capacity; revenue follows only once it's built and running.";
    case "dividend":
      return "Cash returned to shareholders; it doesn't change the business.";
    case "earnings_call":
      return "Management's own account of the quarter; promises made or moved on it land in the record.";
    case "insider_trading":
      return /promoter/i.test(text) ? "A change in how the promoter group holds the stock, not in the business."
        : "A change in who holds the shares; rarely a change in the business.";
    case "press_release":
      if (/commission/i.test(text) && CAPACITY.test(text)) return "New capacity now running; it adds to revenue from here.";
      if (CAPACITY.test(text)) return "Capacity in the works; revenue follows only once it's built and running.";
      return ORDERISH.test(text) ? "Orders and contracts fill the book that future revenue comes from."
        : "Company news; nothing here moves the reported numbers.";
    case "litigation":
      return "A legal dispute; what it could cost matters more than that it exists.";
    default:
      return null;
  }
}

// Which kinds of promise an event bears on, best first: an order win is about order inflow
// before "deal wins", which also files divestments.
const TYPE_METRICS: { [type: string]: string[][] } = {
  fund_raise: [["loan_growth", "debt"], ["capex", "capacity"]],
  credit_rating: [["debt"]],
  capex: [["capex", "capacity"]],
  "M&A": [["deal_wins", "cash_flow"]],
  press_release: [["order_inflow"], ["deal_wins"]],
};
const WEAK = new Set(["deal_wins", "cash_flow"]);
const LIVE = new Set(["tracking", "on_track", "behind", "delayed", "open"]);
const STOP = new Set(["company", "limited", "board", "approved", "announced", "the", "and", "with", "from", "into", "this",
  "that", "under", "regulation", "quarter", "crore", "crores", "share", "shares", "equity", "india", "growth", "business",
  // words every plant, project and order has: two of these don't make a match
  "facility", "plant", "inspection", "project", "order", "unit", "site", "phase", "completion", "commissioning",
  "target", "total", "global", "capacity", "addition", "production", "manufacturing", "supply", "agreement"]);
const words = (s: string) => new Set((s.toLowerCase().match(/[a-z][a-z0-9&]{3,}/g) ?? [])
  .map((w) => w.replace(/(?<=[a-z]{3})s$/, "")).filter((w) => !STOP.has(w)));

/** The company's promises this event bears on, still-running ones first, at most two. */
export function touches(f: Filing, threads: Thread[]): { ts: Thread[]; label: string } {
  const own = threads.filter((t) => t.kind !== "soft");
  if (f.type === "earnings_call") {
    // A transcript is filed days after the call: the call that month, or the month before.
    const d = new Date(f.at);
    const months = [0, 1].map((k) => new Date(d.getFullYear(), d.getMonth() - k, 1)
      .toLocaleString("en-US", { month: "short", year: "numeric" }).replace(",", ""));
    const moved = own.filter((t) => t.events.some((e) => months.includes(e.call) && e.type !== "reaffirmed"));
    return { ts: moved.slice(0, 2), label: "Moved on this call" };
  }
  const text = `${f.summary ?? ""} ${f.subject}`;
  const byId = f.promises.map((id) => own.find((t) => t.id === id)).filter(Boolean) as Thread[];
  // The kinds of promise this event bears on. ESOP shares fund nothing; a press release
  // counts only when it's an order or new capacity.
  let tiers = TYPE_METRICS[f.type] ?? [];
  if (f.type === "fund_raise" && /ESOP|stock option|employee/i.test(text)) tiers = [];
  if (f.type === "press_release") tiers = [...(ORDERISH.test(text) ? tiers : []), ...(CAPACITY.test(text) ? [["capacity"]] : [])];
  // the first tier the company has a live promise in
  const metrics = tiers.find((ms) => own.some((t) => ms.includes(t.metric) && LIVE.has(t.status))) ?? [];
  // Shared words count only if few of the company's promises use them, and it takes two:
  // one ("metro", "inspection") links a Dhaka order to a Hyderabad divestment.
  const common = new Map<string, number>();
  own.forEach((t) => words(t.title).forEach((w) => common.set(w, (common.get(w) ?? 0) + 1)));
  const said = words(text), name = words(f.name);
  const overlap = (t: Thread) => [...words(t.title)].filter((w) => said.has(w) && !name.has(w) && (common.get(w) ?? 0) < 3).length;
  // Two points to count. A live promise of the right kind is two, except "deal wins", which
  // also files launches and divestments; each specific shared word is one; the filing's own
  // link (a looser word match done at build time) is one.
  const score = (t: Thread) => (metrics.includes(t.metric) && LIVE.has(t.status) ? (WEAK.has(t.metric) ? 1 : 2) : 0)
    + overlap(t) + (byId.includes(t) ? 1 : 0);
  const ts = own.filter((t) => score(t) >= 2).sort((a, b) =>
    Number(LIVE.has(b.status)) - Number(LIVE.has(a.status)) || score(b) - score(a));
  return { ts: ts.slice(0, 2), label: "Promises it touches" };
}

export function brief(f: Filing, l?: Ledger | null): Brief {
  const { ts, label } = touches(f, l?.threads ?? []);
  return { facts: keyFacts(f), why: whyItMatters(f, l), touches: ts, touchLabel: label };
}
