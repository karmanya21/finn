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
  rows: { label: string; value: number; qoq: number | null; yoy: number | null; prev_yoy?: number | null }[];
  margin?: { label: string; now: number | null; year_ago: number | null; prev: number | null } | null;
  notes?: { row: string; text: string }[]; // e.g. a year-ago tax credit that distorts PAT growth
}

export interface Metric {
  key: string; label: string; value: number | null; unit: string; reading: string;
  series: { x: string; y: number }[];
}
export interface CasePoint { figure?: string; point: string; refs: string[] }

export interface Business {
  what: string; year: string | null; source: string; url: string; pages: number[];
  segments: { name: string; share_pct: number | null; revenue_cr: number | null; note: string | null; page: number | null }[];
  geographies: { region: string; share_pct: number | null; page: number | null }[];
}

export interface Ledger extends CompanySummary {
  built: string;
  headline: Headline | null;
  snapshot?: { lender: boolean; metrics: Metric[] };
  case?: { bull: CasePoint[]; bear: CasePoint[] };
  business?: Business | null;
  price?: [string, number][] | null; // 3 months of closes, for companies outside the daily price file
  price_asof?: number | null;
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

// Where the research/Ask server lives: same origin in development (Vite proxies /api), its
// own address when the app is hosted (VITE_API_URL, set at build time).
const API = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");
// Data files sit next to the app, wherever it's served from.
const DATA = `${import.meta.env.BASE_URL}data/`;

// The hosted server sleeps when idle (Render's free plan) and takes up to a minute to wake.
// Calls give up after 90 s so nothing hangs; once anything answers, the app knows it's awake
// and stops saying it's waking.
const API_TIMEOUT_MS = 90_000;
export const WAKE_AFTER_MS = 2_000;  // no answer by then: say it's waking
let awake = false;
export const apiAwake = () => awake;
export class ApiTimeout extends Error { constructor() { super("timeout"); } }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), API_TIMEOUT_MS);
  try {
    const r = await fetch(`${API}${path}`, { ...init, signal: ctl.signal });
    const body = await r.json().catch(() => ({}));
    if (r.status < 500) awake = true;  // a 5xx can be the host's own "starting up" page
    if (!r.ok) throw new Error(body.error || `${r.status}`);
    return body as T;
  } catch (e) {
    if (ctl.signal.aborted) throw new ApiTimeout();
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export const searchCompanies = (q: string) =>
  api<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}`).then((b) => b.results);
export const researchStatus = (sym: string) => api<ResearchStatus>(`/api/research/${encodeURIComponent(sym)}`);
export interface AskAnswer {
  answerable: boolean;
  sentences: { text: string; sources: string[] }[];
  dropped: number;
  sources: { [id: string]: { kind: string; label: string; ref?: string } };
}
export const askCompany = (sym: string, question: string, history: { q: string; a: string }[] = []) =>
  api<AskAnswer>(`/api/ask/${encodeURIComponent(sym)}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, history }),
  });

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

/** What each status label means, in one line, for when someone taps it. */
export const MEANING: { [label: string]: string } = {
  "Kept": "Reported numbers, or management's later word, met the promise.",
  "Beat": "Came in better than promised.",
  "Missed": "The period ended short of what was promised.",
  "Tracking": "Still running; checked against reported numbers as results come in.",
  "Ahead of pace": "Results so far are running ahead of what the guide needs.",
  "On pace": "Results so far are running at about the rate the guide needs.",
  "Behind pace": "Results so far are running below what the guide needs.",
  "On track": "Management says it's on track; the numbers can't confirm it yet.",
  "Behind": "Running behind what was promised.",
  "Delayed": "Management says it will come later than promised.",
  "Open": "Not due yet; nothing to check so far.",
  "Can't verify": "Guided on a basis reported numbers don't show (such as constant currency), so it can't be checked.",
  "Gone quiet": "Management stopped reporting on it: the period ended, or it went unmentioned for two calls.",
  "Lapsed": "A loose remark whose period passed unmentioned; not scored.",
  "Said": "An ambition, listed for context; never scored.",
};

export function statusOf(t: Thread): { label: string; tone: Tone } {
  if (t.status === "tracking" && t.pace) {
    const p = { ahead: "Ahead of pace", on_pace: "On pace", behind: "Behind pace" }[t.pace.status];
    return { label: p, tone: t.pace.status === "behind" ? "missed" : "live" };
  }
  return STATUS[t.status];
}

/** How a move reads as a tag on Today. */
export const MOVE_TAG: { [k in ChangeEvent["type"]]: string } = {
  reaffirmed: "Reaffirmed", raised: "Guidance raised", lowered: "Guidance cut",
  narrowed: "Guidance revised", widened: "Guidance revised", deferred: "Delayed", advanced: "Pulled in",
};

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
export const loadIndex = () => get<CompanySummary[]>(`${DATA}index.json`);
export const loadLedger = (sym: string) => get<Ledger>(`${DATA}${sym}.json`);

/** Research-set companies that ship pre-built with the app (searchable offline). */
export const loadResearchIndex = () =>
  get<{ symbol: string; name: string }[]>(`${DATA}research.json`).catch(() => []);

/** A pre-built record if the app ships one, else null (the dev server answers unknown
 *  paths with the app's HTML, so anything that isn't JSON counts as "not shipped"). */
export async function loadShipped(sym: string): Promise<Ledger | null> {
  try {
    const r = await fetch(`${DATA}${encodeURIComponent(sym)}.json`);
    if (!r.ok || !(r.headers.get("content-type") ?? "").includes("json")) return null;
    return await r.json();
  } catch { return null; }
}

// ---- what moved in the latest calls (the push side of the slice) -------------

export interface FeedItem {
  symbol: string;
  name: string;
  thread: Thread;
  headline: string;
  tag: string;   // what happened, in words: "Missed", "Guidance cut", "Delayed", "Kept"...
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
        items.push({ ...base, tone: cut ? "missed" : "moved", tag: MOVE_TAG[last.type],
          headline: `${t.title}: ${last.from} → ${last.to}`,
          line: moveHistory(t, last.type),
          weight: (cut ? 90 : 70) + (hardish ? 5 : 0) });
      }
      const guided = t.target ? `: guided ${t.target}` : "";
      if (t.status === "missed" && hardish)
        items.push({ ...base, tone: "missed", tag: "Missed", weight: 85, headline: `${t.title}${guided}`, line: t.detail ?? undefined });
      if (t.status === "tracking" && t.pace?.status === "behind")
        items.push({ ...base, tone: "missed", tag: "Behind pace", weight: 80, headline: `${t.title}${guided}`,
          line: `${t.pace.so_far_label} at ${t.pace.so_far.toFixed(1)}%` });
      if (t.status === "delayed" && hardish)
        items.push({ ...base, tone: "moved", tag: "Delayed", weight: 75, headline: `${t.title}${guided}`, line: t.detail ?? undefined });
      if (t.status === "gone_quiet" && t.statements.length > 1)
        items.push({ ...base, tone: "quiet", tag: "Gone quiet", weight: 60,
          // The backend's detail may already say "Said 3 times, then…"; only add it when it doesn't.
          headline: `${t.title}${guided}`,
          line: /^Said \d+ times/.test(t.detail ?? "") ? t.detail! : `Said ${t.statements.length} times; ${t.detail ?? "no update since"}` });
      if ((t.status === "delivered" || t.status === "beat") && t.basis === "financials" && t.kind === "hard")
        items.push({ ...base, tone: "kept", tag: t.status === "beat" ? "Beat" : "Kept", weight: 40,
          headline: `${t.title}${guided}`, line: t.detail ?? undefined });
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

// ---- push: filings digest ---------------------------------------------------------

export interface Person {
  name: string; role: string;
  change: "appointed" | "resigned" | "retired" | "ceased" | "redesignated";
  effective: string | null; profile: string | null;
  promises: string[]; // thread ids this person stated, when they're leaving
}

export interface Filing {
  id: string; symbol: string; name: string; at: string;
  type: string; label: string; subject: string; headline: string; pdf: string | null;
  summary: string | null; people: Person[]; promises: string[];
  numbers: Headline | null; importance: number; routine: boolean;
  also?: { label: string; pdf: string }[]; // other documents filed with the same results
  senior?: boolean; // management change involving the CEO, CFO, MD or Chair
  tone?: "positive" | "negative" | null; // which way a regulatory outcome points
}

export interface Digest { built: string; days: number; items: Filing[] }
export const loadDigest = () => get<Digest>(`${DATA}filings.json`);

// ---- push: prices and alerts --------------------------------------------------------

export interface Prices {
  built: string; source: string;
  series: { [sym: string]: [string, number, number][] };
  asof?: { [sym: string]: number | null }; // Unix time of each stock's last trade
}
export const loadPrices = () => get<Prices>(`${DATA}prices.json`);

export interface AlertSettings {
  volume: number;   // alert when volume >= this multiple of its 20-day average
  move: number;     // alert when |daily move| >= this %
  types: string[];  // filing categories that alert
}
export const DEFAULT_SETTINGS: AlertSettings = {
  volume: 2, move: 5, types: ["results", "M&A", "management_change", "litigation", "regulatory", "fund_raise", "credit_rating"],
};
export const ALERT_TYPES: [string, string][] = [
  ["results", "Results"], ["M&A", "M&A"], ["management_change", "Management change"],
  ["litigation", "Legal"], ["regulatory", "Regulatory"], ["fund_raise", "Fund raise"], ["credit_rating", "Credit rating"],
  ["capex", "Capex"], ["pledging", "Pledging"], ["insider_trading", "Insider trades"], ["dividend", "Dividend"],
];

const SETTINGS_KEY = "finn.alerts.v1";
export function readSettings(): AlertSettings {
  try {
    const s: AlertSettings = { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
    // Regulatory used to be part of litigation: carry the choice over.
    if (s.types.includes("litigation") && !s.types.includes("regulatory")) s.types = [...s.types, "regulatory"];
    return s;
  } catch { return DEFAULT_SETTINGS; }
}
export function saveSettings(s: AlertSettings) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch { /* private mode */ }
}

export interface MoveAlert {
  kind: "move"; symbol: string; date: string; close: number;
  pct: number; volRatio: number; byPrice: boolean; byVolume: boolean;
  reason?: Filing; // a material filing that day or the day before
}

/** End-of-day alerts over the last `days` trading days, judged against the user's thresholds. */
export function moveAlerts(p: Prices, s: AlertSettings, days: number, filings: Filing[]): MoveAlert[] {
  const out: MoveAlert[] = [];
  for (const [sym, rows] of Object.entries(p.series)) {
    for (let i = Math.max(21, rows.length - days); i < rows.length; i++) {
      const [date, close, vol] = rows[i];
      const prev = rows[i - 1][1];
      const avg = rows.slice(i - 20, i).reduce((a, r) => a + r[2], 0) / 20;
      const pct = (close / prev - 1) * 100;
      const volRatio = avg ? vol / avg : 0;
      const byPrice = Math.abs(pct) >= s.move, byVolume = volRatio >= s.volume;
      if (!byPrice && !byVolume) continue;
      const prevDate = rows[i - 1][0];
      const reason = filings
        .filter((f) => f.symbol === sym && !f.routine && f.at.slice(0, 10) <= date && f.at.slice(0, 10) >= prevDate)
        .sort((a, b) => b.importance - a.importance)[0];
      out.push({ kind: "move", symbol: sym, date, close, pct, volRatio, byPrice, byVolume, reason });
    }
  }
  return out.sort((a, b) => b.date.localeCompare(a.date) || Math.abs(b.pct) - Math.abs(a.pct));
}

/** Filings within the last `hours` of the digest's build time. */
export function inWindow(d: Digest, hours: number): Filing[] {
  const end = new Date(d.built).getTime();
  return d.items.filter((f) => end - new Date(f.at).getTime() <= hours * 3600_000);
}
