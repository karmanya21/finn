// Types mirror backend/scripts/build_ledger.py output.

export type Status =
  | "delivered" | "beat" | "missed"
  | "tracking" | "on_track" | "behind" | "delayed" | "open"
  | "cant_verify" | "gone_quiet" | "lapsed" | "said";

export type Kind = "hard" | "directional" | "milestone" | "soft";

export interface Statement {
  call: string;
  quarter: string;
  quote: string;
  speaker: string;
  page: number;
  target: string;
  period: string | null;
  deadline: string | null;
  condition: string | null;
  url: string | null;
}

export interface ChangeEvent {
  call: string;
  type: "reaffirmed" | "raised" | "lowered" | "narrowed" | "widened" | "deferred" | "advanced";
  from: string;
  to: string;
}

export interface Pace {
  so_far: number;
  so_far_label: string;
  status: "ahead" | "on_pace" | "behind";
  measure: string;
  needs?: number;
  needs_label?: string;
  needs_for?: number;
}

export interface Thread {
  id: string;
  title: string;
  kind: Kind;
  metric: string;
  scope: string | null;
  basis: string | null;
  period: string;
  target: string;
  direction: string | null;
  moved: boolean;
  events: ChangeEvent[];
  status: Status;
  detail?: string | null;
  pace?: Pace;
  evidence?: { quote: string | null; call: string | null; new_deadline: string | null };
  statements: Statement[];
}

export interface Record {
  kept: number;
  missed: number;
  resolved: number;
  moved: number;
  quiet: number;
  open: number;
}

export interface CompanySummary {
  symbol: string;
  name: string;
  sector: string;
  size: string | null;
  latest_call: string | null; // null: the company holds no earnings calls
  latest_results: string | null;
  record: Record;
}

export interface Headline {
  quarter: string;
  unit: string;
  source: string;
  rows: { label: string; value: number; qoq: number | null; yoy: number | null }[];
}

export interface Ledger extends CompanySummary {
  built: string;
  headline: Headline | null;
  calls: { call: string; quarter: string; url: string | null; promises: number }[];
  threads: Thread[];
}

// ---- pull: research any company through backend/server.py ----------------------

export interface SearchResult { symbol: string; name: string; ready: boolean }

export interface ResearchStatus {
  symbol: string;
  state: "idle" | "queued" | "running" | "done" | "error";
  steps: { step: string; detail: string; at: number }[];
  position?: number;
  error?: string;
  ledger?: Ledger;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `${r.status}`);
  return body as T;
}

export const searchCompanies = (q: string) =>
  api<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}`).then((b) => b.results);
export const researchStatus = (sym: string) => api<ResearchStatus>(`/api/research/${encodeURIComponent(sym)}`);
export const startResearch = (sym: string) =>
  api<ResearchStatus>(`/api/research/${encodeURIComponent(sym)}`, { method: "POST" });

// ---- vocabulary --------------------------------------------------------------

export type Tone = "kept" | "missed" | "moved" | "quiet" | "live" | "muted";

export const STATUS: { [s in Status]: { label: string; tone: Tone } } = {
  delivered: { label: "Kept", tone: "kept" },
  beat: { label: "Beat", tone: "kept" },
  missed: { label: "Missed", tone: "missed" },
  tracking: { label: "Tracking", tone: "live" },
  on_track: { label: "On track", tone: "live" },
  behind: { label: "Behind", tone: "missed" },
  delayed: { label: "Delayed", tone: "moved" },
  open: { label: "Open", tone: "muted" },
  cant_verify: { label: "Can't verify", tone: "muted" },
  gone_quiet: { label: "Gone quiet", tone: "quiet" },
  lapsed: { label: "Lapsed", tone: "muted" },
  said: { label: "Said", tone: "muted" },
};

export function statusOf(t: Thread): { label: string; tone: Tone } {
  if (t.status === "tracking" && t.pace) {
    const p = { ahead: "Ahead of pace", on_pace: "On pace", behind: "Behind pace" }[t.pace.status];
    return { label: p, tone: t.pace.status === "behind" ? "missed" : "live" };
  }
  return STATUS[t.status];
}

export const MOVE_VERB: { [k in ChangeEvent["type"]]: string } = {
  reaffirmed: "Reaffirmed",
  raised: "Raised",
  lowered: "Cut",
  narrowed: "Narrowed",
  widened: "Widened",
  deferred: "Pushed out",
  advanced: "Pulled in",
};

export const KIND_LABEL: { [k in Kind]: string } = {
  hard: "Number",
  directional: "Direction",
  milestone: "Milestone",
  soft: "Ambition",
};

// ---- loading -----------------------------------------------------------------

const cache = new Map<string, Promise<any>>();
function get<T>(path: string): Promise<T> {
  if (!cache.has(path)) cache.set(path, fetch(path).then((r) => {
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }));
  return cache.get(path)!;
}
export const loadIndex = () => get<CompanySummary[]>("/data/index.json");
export const loadLedger = (sym: string) => get<Ledger>(`/data/${sym}.json`);

// ---- what moved in the latest calls (the push side of the slice) -------------

export interface FeedItem {
  symbol: string;
  name: string;
  thread: Thread;
  headline: string;
  line?: string; // overrides the thread's own one-liner
  tone: Tone;
  weight: number; // higher = more important
}

const ORDINAL = ["", "First", "Second", "Third", "Fourth", "Fifth"];

/** "Second cut this year · first guided 1.5–3.5% in Apr 2026" */
function moveHistory(t: Thread, type: ChangeEvent["type"]): string {
  const n = t.events.filter((e) => e.type === type).length;
  const first = t.statements[0];
  const verb = MOVE_VERB[type].toLowerCase();
  const nth = n > 1 ? `${ORDINAL[n] ?? `${n}th`} ${verb}` : verb[0].toUpperCase() + verb.slice(1);
  return `${nth} since ${first.call} · first guided ${first.target || first.deadline}`;
}

export function feed(ledgers: Ledger[]): FeedItem[] {
  const items: FeedItem[] = [];
  for (const l of ledgers) {
    for (const t of l.threads) {
      if (t.kind === "soft") continue;
      const base = { symbol: l.symbol, name: l.name, thread: t };
      const hardish = t.kind === "hard" || t.kind === "milestone";
      const last = t.events[t.events.length - 1];
      if (last && last.call === l.latest_call && last.type !== "reaffirmed") {
        const cut = last.type === "lowered" || last.type === "deferred";
        items.push({ ...base, tone: cut ? "missed" : "moved",
          headline: `${MOVE_VERB[last.type]} ${t.title}: ${last.from} → ${last.to}`,
          line: moveHistory(t, last.type),
          weight: (cut ? 90 : 70) + (hardish ? 5 : 0) });
      }
      if (t.status === "missed" && hardish)
        items.push({ ...base, tone: "missed", weight: 85,
          headline: `Missed ${t.title}${t.target ? ` (guided ${t.target})` : ""}` });
      if (t.status === "tracking" && t.pace?.status === "behind")
        items.push({ ...base, tone: "missed", weight: 80, headline: `Behind pace on ${t.title}` });
      if (t.status === "delayed" && hardish)
        items.push({ ...base, tone: "moved", weight: 75, headline: `Delayed: ${t.title}` });
      if (t.status === "gone_quiet" && t.statements.length > 1)
        items.push({ ...base, tone: "quiet", weight: 60,
          headline: `Gone quiet on ${t.title} after ${t.statements.length} mentions` });
      if ((t.status === "delivered" || t.status === "beat") && t.basis === "financials" && t.kind === "hard")
        items.push({ ...base, tone: "kept", weight: 40,
          headline: `${t.status === "beat" ? "Beat" : "Kept"} ${t.title}${t.target ? ` (guided ${t.target})` : ""}` });
    }
  }
  // One item per thread, the most important reading of it.
  const best = new Map<string, FeedItem>();
  for (const i of items) {
    const prev = best.get(i.thread.id);
    if (!prev || i.weight > prev.weight) best.set(i.thread.id, i);
  }
  return [...best.values()].sort((a, b) => b.weight - a.weight);
}
