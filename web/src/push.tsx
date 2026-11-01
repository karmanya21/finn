// Push: alerts against the user's own thresholds, and the filings digest.

import { useEffect, useMemo, useState } from "react";
import {
  ALERT_TYPES, AlertSettings, CompanySummary, Digest, Filing, Headline, Ledger, Prices, Thread,
  feed, inWindow, loadDigest, loadPrices, moveAlerts, readSettings, saveSettings, statusOf,
} from "./data";
import { cleanSubject, filingText, fmtChange, fmtDate, normText, priceWhen, tone } from "./format";
import { Shell, go } from "./shell";
import { brief } from "./brief";
import { loadAnyLedger } from "./mine";

const SETTINGS_EVENT = "finn:settings";

function useSettings(): AlertSettings {
  const [s, setS] = useState(readSettings);
  useEffect(() => {
    const on = () => setS(readSettings());
    window.addEventListener(SETTINGS_EVENT, on);
    return () => window.removeEventListener(SETTINGS_EVENT, on);
  }, []);
  return s;
}

/** undefined while loading, null if the file isn't there (yet). */
function useData<T>(load: () => Promise<T>): T | null | undefined {
  const [v, setV] = useState<T | null | undefined>(undefined);
  useEffect(() => { load().then(setV).catch(() => setV(null)); }, []);
  return v;
}

const NO_FILINGS: Digest = { built: new Date().toISOString(), days: 30, items: [] };

/** Every company's record, so filings can say which promises they touch. */
function useLedgers(index: CompanySummary[]): Map<string, Ledger> {
  const [m, setM] = useState(new Map<string, Ledger>());
  useEffect(() => {
    Promise.all(index.map((c) => loadAnyLedger(c.symbol).catch(() => null))).then((ls) =>
      setM(new Map((ls.filter(Boolean) as Ledger[]).map((l) => [l.symbol, l]))));
  }, [index]);
  return m;
}

// ---- Today: what needs attention, company by company ---------------------------------

/** One thing that happened: a price/volume alert, a filing, or a move in a promise. */
export interface Update {
  key: string; symbol: string; name: string; weight: number;
  tag: string;        // what it is, in words ("Results", "Price", "Missed")
  date: string;       // when, in the app's one date format
  title: string;      // the one line that matters, with its number
  line?: string;      // optional detail
  tone?: "up" | "down";
  // How the Today tile lays it out, when there's more structure than a sentence:
  figures?: Figure[];                               // results, price, volume: one row per figure
  change?: { subject: string; from: string; to: string };  // guidance moved: old → new
  target?: { subject: string; value: string };     // a promise's verdict: what was guided
  heading?: string;   // the tile's whole heading when "tag · date" won't fit ("Q2FY27 · 30 Oct", "Guidance cut")
  promise?: { text: string; tone?: "up" | "down" };  // results: what they mean for a promise
  urgent?: boolean;   // a guidance cut, a missed promise, a big move, bad regulatory news
  open: () => void;
}

/** "Touches FY27 order inflow guide (open)" for a filing that bears on a promise. */
function touchLine(f: Filing, l: Ledger | undefined): Update["promise"] {
  if (f.type === "results" || f.type === "management_change") return undefined;
  const t = brief(f, l).touches[0];
  if (!t) return undefined;
  return { text: `${f.type === "earnings_call" ? "Moved" : "Touches"}: ${t.title}`, tone: statusOf(t).tone === "missed" ? "down" : undefined };
}

/** What a results filing means for the promises it touches, most serious first:
 *  "Missed FY26 net debt reduction", "Behind FY27 EBITDA margin guide". */
function promiseImpact(ts: Thread[], quarter: string): Update["promise"] {
  const settled = (t: Thread) => settledBy(t.period, quarter);
  const pick = (test: (t: Thread) => boolean) => ts.find(test);
  let t: Thread | undefined;
  if ((t = pick((x) => x.status === "missed" && settled(x)))) return { text: `Missed ${t.title}`, tone: "down" };
  if ((t = pick((x) => x.pace?.status === "behind"))) return { text: `Behind ${t.title} guide`, tone: "down" };
  if ((t = pick((x) => x.pace?.status === "ahead"))) return { text: `Ahead of ${t.title} guide`, tone: "up" };
  if ((t = pick((x) => (x.status === "delivered" || x.status === "beat") && settled(x))))
    return { text: `${t.status === "beat" ? "Beat" : "Kept"} ${t.title}`, tone: "up" };
  if ((t = pick((x) => x.pace?.status === "on_pace"))) return { text: `On pace for ${t.title} guide` };
  return undefined;
}

const SEEN_KEY = "finn.seen.v3";
const RECENT_CALL_DAYS = 60;  // promise moves from older calls aren't news any more

/** Every update in the alert window, grouped by company, most important first. */
export function useUpdates(index: CompanySummary[]): Map<string, Update[]> | null {
  const prices = useData<Prices>(loadPrices);
  const loaded = useData<Digest>(loadDigest);
  const digest = loaded === null ? NO_FILINGS : loaded;  // price alerts don't need filings
  const s = useSettings();
  const [ledgers, setLedgers] = useState<Ledger[] | null>(null);
  useEffect(() => {
    Promise.all(index.map((c) => loadAnyLedger(c.symbol).catch(() => null)))
      .then((ls) => setLedgers(ls.filter(Boolean) as Ledger[]));
  }, [index]);

  return useMemo(() => {
    if (!prices || !digest || !ledgers) return null;
    const names = new Map(index.map((c) => [c.symbol, c.name]));
    const dates = Object.values(prices.series)[0] || [];
    const lastSession = dates.length ? dates[dates.length - 1][0] : "";
    const cutoff = dates.length >= 5 ? dates[dates.length - 5][0] : "";
    const recent = (d: string) => (d >= lastSession ? 10 : 0);
    const out = new Map<string, Update[]>(index.map((c) => [c.symbol, []]));
    const add = (u: Update) => out.get(u.symbol)?.push(u);

    for (const m of moveAlerts(prices, s, 5, digest.items)) {
      const up = m.pct >= 0;
      const what = [m.byPrice && `${fmtChange(m.pct, 1)} on the day`,
                    m.byVolume && `volume ${m.volRatio.toFixed(1)}× its average`].filter(Boolean).join(" · ");
      add({ key: `m-${m.symbol}-${m.date}`, symbol: m.symbol, name: names.get(m.symbol) ?? m.symbol,
        weight: (m.byPrice ? 55 + Math.min(Math.abs(m.pct), 15) * 2 : 45) + (m.byVolume ? Math.min(m.volRatio, 6) * 3 : 0)
          + (m.reason ? 10 : 0) + recent(m.date),
        tag: m.byPrice ? "Price" : "Volume", date: fmtDate(m.date), title: what, tone: m.byPrice ? (up ? "up" : "down") : undefined,
        figures: [
          ...(m.byPrice ? [{ label: "Price", change: fmtChange(m.pct, 1), tone: up ? "up" as const : "down" as const }] : []),
          ...(m.byVolume ? [{ label: "Volume", change: `${m.volRatio.toFixed(1)}×`, unit: "avg" }] : []),
          ...(!m.byPrice ? [{ label: "Price", change: fmtChange(m.pct, 1) }] : []),
        ],
        line: m.reason ? `Same day: ${m.reason.type === "results" ? resultsHeadline(m.reason) : filingText(m.reason.summary, m.reason.subject)}`
          : `Closed ₹${m.close.toLocaleString("en-IN")}; no material filing that day`,
        urgent: m.byPrice, open: () => go(`/c/${m.symbol}`) });
    }
    const threadById = new Map(ledgers.flatMap((l) => l.threads.map((t) => [t.id, t] as const)));
    const bySym = new Map(ledgers.map((l) => [l.symbol, l]));
    for (const f of digest.items) {
      if (f.routine || !s.types.includes(f.type) || f.at.slice(0, 10) < cutoff) continue;
      if (f.type === "management_change" && !f.senior) continue;  // only CEO, CFO, MD, Chair
      const yoy = f.numbers?.rows.find((r) => r.label === "PAT")?.yoy;
      // Results without figures yet say little; let a real move or a cut outrank them.
      const blank = f.type === "results" && !f.numbers ? 25 : 0;
      add({ key: `f-${f.id}`, symbol: f.symbol, name: f.name, weight: f.importance + recent(f.at.slice(0, 10)) - blank,
        tag: f.label, date: fmtDate(f.at),
        figures: f.numbers ? resultsFigures(f.numbers) : undefined, heading: resultsQuarter(f) && `${resultsQuarter(f)} · ${fmtDate(f.at)}`, title: f.type === "results" ? resultsHeadline(f) : filingText(f.summary, f.subject),
        tone: f.type === "results" && yoy != null ? (yoy >= 0 ? "up" : "down")
          : f.tone === "positive" ? "up" : f.tone === "negative" ? "down" : undefined,
        line: f.people.length ? peopleLine(f) : f.type !== "results" ? brief(f, bySym.get(f.symbol)).why ?? undefined : undefined,
        promise: f.numbers ? promiseImpact(f.promises.map((id) => threadById.get(id)).filter(Boolean) as Thread[], f.numbers.quarter)
          : touchLine(f, bySym.get(f.symbol)),
        urgent: f.type === "regulatory" && f.tone === "negative",
        open: () => go(`/filings/${f.id}`) });
    }
    const VERDICT = new Set(["Missed", "Kept", "Beat"]);
    for (const i of feed(ledgers)) {
      const l = bySym.get(i.symbol);
      if (!l?.latest_call || daysBetween(l.latest_call, lastSession) > RECENT_CALL_DAYS) continue;
      // A verdict is news only if the latest results settled it (period ends this quarter).
      if (VERDICT.has(i.tag) && !settledBy(i.thread.period, l.latest_results)) continue;
      const lastEvent = i.thread.events[i.thread.events.length - 1];
      const date = VERDICT.has(i.tag) || i.tag === "Behind pace" ? `${l.latest_results ?? ""} results`
        : i.tag === "Gone quiet" ? `${l.latest_call} call` : `${lastEvent?.call ?? l.latest_call} call`;
      const moved = i.headline.includes(" → ") && lastEvent?.from && lastEvent?.to;
      const better = moved && (lastEvent.type === "raised" || lastEvent.type === "advanced");
      add({ key: `p-${i.thread.id}|${i.headline}`, symbol: i.symbol, name: i.name, weight: i.weight,
        tag: i.tag, date, title: normText(i.headline), line: i.line && normText(i.line),
        tone: better || i.tone === "kept" ? "up" : i.tone === "missed" ? "down" : undefined,
        heading: i.tag,  // the call or quarter is on the promise page; "Guidance cut · Oct 2026 call" wraps
        change: moved ? { subject: normText(i.thread.title), from: normText(lastEvent.from!), to: normText(lastEvent.to!) } : undefined,
        target: !moved ? { subject: normText(i.thread.title), value: i.thread.target ? normText(i.thread.target) : "" } : undefined,
        // a cut or a pushed-out deadline; management merely saying "delayed" waits in the rows
        urgent: i.tag === "Guidance cut" || i.tag === "Missed" || (!!moved && lastEvent.type === "deferred"),
        open: () => go(`/c/${i.symbol}/t/${i.thread.id}`) });
    }
    for (const list of out.values()) list.sort((a, b) => b.weight - a.weight);
    return out;
  }, [prices, digest, ledgers, s, index]);
}

/** Did results for `latest` (e.g. "Q4FY26") close this promise's period? */
function settledBy(period: string, latest: string | null): boolean {
  if (!latest) return false;
  const m = latest.match(/Q([1-4])FY(\d{2})/);
  if (!m) return false;
  const [q, fy] = [Number(m[1]), m[2]];
  return period === latest || (q === 4 && period === `FY${fy}`) || (q === 2 && period === `H1FY${fy}`)
    || (q === 4 && period === `H2FY${fy}`);
}

function daysBetween(callLabel: string, isoDate: string): number {
  const [m, y] = callLabel.split(" ");
  const call = new Date(`${m} 1, ${y}`).getTime();
  return (new Date(isoDate || Date.now()).getTime() - call) / 86_400_000;
}

/** A tile's lead update: figures in rows, a guidance move as old → new, a verdict under
 *  what was promised; anything else as its one line. */
function TileBody({ u }: { u: Update }) {
  const verdict = !!(u.change || u.target);
  const tag = <span className={`tile-tag ${verdict ? u.tone ?? "" : ""}`}>{u.heading ?? `${u.tag} · ${u.date}`}</span>;
  const impact = u.promise && <span className={`tile-impact ${u.promise.tone ?? ""}`}>{u.promise.text}</span>;
  if (u.figures?.length) return <>{tag}
    <span className="tile-figs">
      {u.figures.map((f) => (
        <span key={f.label} className="tile-fig">
          <span className="tile-fig-l">{f.label}</span>
          <span className={`tile-fig-v ${f.tone ?? ""}`}>{f.change}</span>
          <span className="tile-fig-u">{f.unit}</span>
        </span>
      ))}
    </span>{impact}</>;
  if (u.change) return <>{tag}
    <span className="tile-subject">{u.change.subject}</span>
    <span className="tile-move">
      <span className="tile-from">{u.change.from}</span><span className="tile-arrow">→</span>
      <span className={`tile-to ${u.tone ?? ""}`}>{u.change.to}</span>
    </span></>;
  if (u.target) return <>{tag}
    <span className="tile-subject">{u.target.subject}</span>
    {u.target.value && <span className="tile-move"><span className="tile-fig-u">Guided</span>
      <span className="tile-to">{u.target.value}</span></span>}</>;
  // The heading already names the quarter: "Q2FY27: filed, …" → "Filed, …"
  const q = u.heading?.split(" · ")[0];
  const title = q && u.title.startsWith(`${q}: `) ? u.title.slice(q.length + 2).replace(/^./, (c) => c.toUpperCase()) : u.title;
  return <>{tag}<span className={`tile-line ${u.tone ?? ""}`}>{title}</span></>;
}

/** "Updated 30 Oct, 7:25 pm": when the newest of the day's data was built. */
function fmtUpdated(iso: string): string {
  const [h, m] = iso.slice(11, 16).split(":").map(Number);
  return `Updated ${fmtDate(iso)}, ${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
}

/** Today: the few things that need a look as tiles (a guidance cut, a missed promise, a big
 *  move, bad regulatory news), everything else new as compact rows, then the quiet ones. */
export function TodayGrid({ index, onSettings }: { index: CompanySummary[]; onSettings: () => void }) {
  const updates = useUpdates(index);
  const prices = usePrices();
  const digest = useData<Digest>(loadDigest);
  const s = useSettings();
  const allKeys = useMemo(() => [...(updates?.values() ?? [])].flat().map((u) => u.key), [updates]);
  const seen = useSeenKeys(allKeys);
  if (!updates) return <p className="empty">Loading…</p>;

  const rows = index.map((c) => {
    const us = updates.get(c.symbol) ?? [];
    const fresh = seen.size > 0 ? us.filter((u) => !seen.has(u.key)).length : 0;
    // Lead with the most serious update; else the top one that carries a number.
    const lead = us.find((u) => u.urgent) ?? us.find((u) => /\d/.test(u.title)) ?? us[0];
    return { c, us, lead, fresh, score: us.length ? us[0].weight + Math.min(us.length, 5) * 2 : -1 };
  }).sort((a, b) => b.score - a.score);
  const urgent = rows.filter((r) => r.lead?.urgent);
  const rest = rows.filter((r) => r.us.length && !r.lead?.urgent);
  const quiet = rows.filter((r) => !r.us.length);
  const stamps = [prices?.built, digest?.built].filter(Boolean).map((x) => x!.slice(0, 19)).sort();

  return (
    <section>
      {stamps.length > 0 && <p className="updated">{fmtUpdated(stamps[stamps.length - 1])}</p>}
      <h2 className="section-title">Needs attention <span className="count">{urgent.length}</span></h2>
      {urgent.length === 0 ? <p className="quiet-note">No guidance cuts, missed promises or big moves.</p> : (
        <div className="tiles">
          {urgent.map(({ c, us, lead, fresh }) => (
            <button key={c.symbol} className="tile" onClick={() => go(`/u/${c.symbol}`)}>
              <span className="tile-top">
                <span className="tile-name">{c.name}</span>
                {fresh > 0 && <span className="new">{fresh} new</span>}
              </span>
              <TileBody u={lead!} />
              {us.length > 1 && <span className="tile-more">+{us.length - 1} more update{us.length > 2 ? "s" : ""}</span>}
            </button>
          ))}
        </div>
      )}
      {rest.length > 0 && (
        <>
          <h2 className="section-title quiet-title">Also new <span className="count">{rest.length}</span></h2>
          <div className="feed">
            {rest.map(({ c, us, lead, fresh }) => (
              <button key={c.symbol} className="feed-row compact" onClick={() => go(`/u/${c.symbol}`)}>
                <span className="feed-body">
                  <span className="cr-top">
                    <span className="cr-name">{c.name}</span>
                    <span className="feed-tag">{lead!.heading?.includes("·") ? lead!.heading : `${lead!.tag} · ${lead!.date}`}</span>
                  </span>
                  <span className={`cr-line ${lead!.figures?.length && lead!.figures.length > 1 ? "" : lead!.tone ?? ""}`}>{compactLine(lead!)}</span>
                  {lead!.promise && <span className={`cr-impact ${lead!.promise.tone ?? ""}`}>{lead!.promise.text}</span>}
                </span>
                <span className="cr-side">
                  {fresh > 0 && <span className="new">{fresh} new</span>}
                  {us.length > 1 && <span className="cr-more">+{us.length - 1}</span>}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
      {quiet.length > 0 && (
        <>
          <h2 className="section-title quiet-title">Nothing new <span className="count">{quiet.length}</span></h2>
          <div className="tiles quiet">
            {quiet.map(({ c }) => (
              <button key={c.symbol} className="tile muted" onClick={() => go(`/c/${c.symbol}`)}>
                <span className="tile-name">{c.name}</span>
              </button>
            ))}
          </div>
        </>
      )}
      <Footnote>
        Last 5 sessions. Alerts when volume is ≥{s.volume}× its 20-day average, a stock moves beyond ±{s.move}%,
        or one of {s.types.length} filing types lands; promise moves from calls in the last 60 days.{" "}
        <button className="link" onClick={onSettings}>Change</button>
      </Footnote>
    </section>
  );
}

/** One line for a compact row: figures inline ("Revenue +27% · PAT +20% YoY"), else the title. */
function compactLine(u: Update): string {
  if (u.figures?.length) {
    const yoy = u.figures.every((f) => f.unit === "YoY");
    return u.figures.map((f) => `${f.label} ${f.change}${!yoy && f.unit ? ` ${f.unit}` : ""}`).join(" · ") + (yoy ? " YoY" : "");
  }
  if (u.change) return `${u.change.subject}: ${u.change.from} → ${u.change.to}`;
  const q = u.heading?.split(" · ")[0];
  return q && u.title.startsWith(`${q}: `) ? u.title.slice(q.length + 2).replace(/^./, (c) => c.toUpperCase()) : u.title;
}

/** One company's updates, most important first, with a way into its full record. */
export function CompanyUpdates({ index, symbol }: { index: CompanySummary[]; symbol: string }) {
  const updates = useUpdates(index);
  const c = index.find((x) => x.symbol === symbol);
  if (!updates) return <Shell back><p className="empty">Loading…</p></Shell>;
  const us = updates.get(symbol) ?? [];
  return (
    <Shell back>
      <section className="co-head">
        <h1 className="co-title">{c?.name ?? symbol}</h1>
        <PriceLine symbol={symbol} />
      </section>
      <section>
        <h2 className="section-title">Updates <span className="count">{us.length}</span></h2>
        {us.length === 0 ? <p className="quiet-note">Nothing new in the last 5 sessions.</p> : (
          <div className="feed">{us.map((u) => <UpdateRow key={u.key} u={u} />)}</div>
        )}
        <button className="cta" onClick={() => go(`/c/${symbol}`)}>Open the full record →</button>
      </section>
    </Shell>
  );
}

function UpdateRow({ u }: { u: Update }) {
  return (
    <button className="feed-row" onClick={u.open}>
      <span className="feed-body">
        <span className="feed-tag">{u.tag} · {u.date}</span>
        <span className={`feed-head ${u.tone ?? ""}`}>{u.title}</span>
        {u.line && <span className="feed-line">{u.line}</span>}
        {u.promise && <span className={`cr-impact ${u.promise.tone ?? ""}`}>{u.promise.text}</span>}
      </span>
    </button>
  );
}

/** Keys seen before this visit; everything shown now counts as seen next time. */
function useSeenKeys(keys: string[]): Set<string> {
  const [before] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) || "[]")); } catch { return new Set(); }
  });
  useEffect(() => {
    if (!keys.length) return;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify([...new Set([...before, ...keys])])); } catch { /* private mode */ }
  }, [keys.join("|"), before]);
  return before;
}

/** A newer quarter read from the company's results filing, if screener hasn't caught up. */
export function useNewerResults(symbol: string, current: Headline | null | undefined): Headline | null {
  const digest = useData<Digest>(loadDigest);
  const order = (q: string) => { const m = q.match(/Q([1-4])FY(\d{2})/); return m ? Number(m[2]) * 10 + Number(m[1]) : 0; };
  const newer = digest?.items.find((f) => f.symbol === symbol && f.type === "results" && f.numbers &&
    order(f.numbers.quarter) > order(current?.quarter ?? ""));
  return newer?.numbers ?? null;
}

// ---- prices -------------------------------------------------------------------------

/** Daily-refreshed portfolio prices; a searched company falls back to its record's series. */
export function usePrices(): Prices | null {
  return useData<Prices>(loadPrices) ?? null;
}

export function priceStats(rows: [string, number][] | undefined | null) {
  if (!rows || rows.length < 2) return null;
  const [, close] = rows[rows.length - 1], [, prev] = rows[rows.length - 2];
  return { close, change: (close / prev - 1) * 100, date: rows[rows.length - 1][0] };
}

/** "₹3,017.30  +5.4%" with a 3-month sparkline. */
export function PriceLine({ symbol, fallback, fallbackAsof, chart }: {
  symbol: string; fallback?: [string, number][] | null; fallbackAsof?: number | null; chart?: boolean;
}) {
  const prices = usePrices();
  const own = prices?.series[symbol];
  const series = own?.map(([d, c]) => [d, c] as [string, number]) ?? fallback ?? null;
  const asof = own ? prices?.asof?.[symbol] : fallbackAsof;
  const st = priceStats(series);
  if (!series || !st) return null;
  const last3m = series.slice(-63);
  return (
    <div className="price-line">
      <span className="price">₹{st.close.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</span>
      <span className={st.change >= 0 ? "up" : "down"}>{fmtChange(st.change, 1)}</span>
      {!chart && <Sparkline ys={last3m.map(([, c]) => c)} />}
      <span className="price-date">{priceWhen(st.date, asof)}{chart ? "" : " · chart: 3 months"}</span>
    </div>
  );
}

function Sparkline({ ys, w = 96, h = 24 }: { ys: number[]; w?: number; h?: number }) {
  if (ys.length < 2) return null;
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const pts = ys.map((y, i) => `${((i / (ys.length - 1)) * w).toFixed(1)},${(h - 2 - ((y - lo) / span) * (h - 4)).toFixed(1)}`).join(" ");
  return (
    <svg className={`spark ${ys[ys.length - 1] >= ys[0] ? "up" : "down"}`} width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden>
      <polyline points={pts} fill="none" />
    </svg>
  );
}

/** Small, muted source notes and caveats; the same style at the end of every section. */
export function Footnote({ children }: { children: React.ReactNode }) {
  return <p className="footnote">{children}</p>;
}

export interface Figure { label: string; change: string; unit?: string; tone?: "up" | "down" }

/** Revenue and PAT growth as separate rows, each coloured by its own direction. */
export function resultsFigures(h: Headline): Figure[] {
  return ["Revenue", "PAT"].flatMap((label) => {
    const yoy = h.rows.find((r) => r.label === label)?.yoy;
    if (yoy == null) return [];
    const change = fmtChange(yoy);
    return [{ label, change, unit: "YoY", tone: change === "flat" ? undefined : yoy > 0 ? "up" as const : "down" as const }];
  });
}

/** "Q2FY27: Revenue +16% YoY · PAT +27%". The one format for results everywhere. */
export function resultsLine(h: Headline): string {
  const row = (l: string) => h.rows.find((r) => r.label === l);
  const pct = (v: number | null | undefined) => v == null ? null : fmtChange(v);
  const rev = pct(row("Revenue")?.yoy), pat = pct(row("PAT")?.yoy);
  const bits = [rev && `Revenue ${rev} YoY`, pat && `PAT ${pat}`].filter(Boolean);
  return bits.length ? `${h.quarter}: ${bits.join(" · ")}` : `${h.quarter} results`;
}

/** "Q2FY27" for a results filing, from its figures or else its text. */
function resultsQuarter(f: Filing): string | undefined {
  if (f.type !== "results") return undefined;
  return f.numbers?.quarter
    ?? `${f.summary ?? ""} ${f.subject}`.match(/Q[1-4]\s?FY\s?\d{2}/i)?.[0].replace(/\s/g, "").toUpperCase();
}

function resultsHeadline(f: Filing): string {
  if (f.numbers) return resultsLine(f.numbers);
  const q = resultsQuarter(f);
  return `${q ?? "Results"}: filed, figures not yet tabulated`;
}

// ---- settings ----------------------------------------------------------------------

export function SettingsSheet({ onClose }: { onClose: () => void }) {
  const [s, setS] = useState(readSettings);
  const update = (next: AlertSettings) => { setS(next); saveSettings(next); window.dispatchEvent(new Event(SETTINGS_EVENT)); };
  const toggle = (t: string) => update({ ...s, types: s.types.includes(t) ? s.types.filter((x) => x !== t) : [...s.types, t] });
  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet" role="dialog" aria-label="Alert settings" onClick={(e) => e.stopPropagation()}>
        <div className="grab" />
        <h3 className="sheet-title">What counts as unusual</h3>
        <p className="basis">You decide; FINN watches end-of-day prices and every filing against these.</p>

        <h4 className="sheet-h">Volume spike</h4>
        <Segmented options={[1.5, 2, 2.5, 3, 4]} value={s.volume} fmt={(v) => `${v}×`} onChange={(v) => update({ ...s, volume: v })} />
        <p className="basis">Alert when a day's volume is at least this multiple of its 20-day average.</p>

        <h4 className="sheet-h">Price move</h4>
        <Segmented options={[2, 3, 4, 5, 7, 10]} value={s.move} fmt={(v) => `±${v}%`} onChange={(v) => update({ ...s, move: v })} />
        <p className="basis">Alert when a stock closes up or down by at least this much in a day.</p>

        <h4 className="sheet-h">Filings that alert</h4>
        <div className="toggles">
          {ALERT_TYPES.map(([k, label]) => (
            <button key={k} className={`chip ${s.types.includes(k) ? "on" : ""}`} onClick={() => toggle(k)}>{label}</button>
          ))}
        </div>
        <button className="close" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

function Segmented({ options, value, fmt, onChange }: {
  options: number[]; value: number; fmt: (v: number) => string; onChange: (v: number) => void;
}) {
  return (
    <div className="segmented">
      {options.map((o) => <button key={o} className={o === value ? "on" : ""} onClick={() => onChange(o)}>{fmt(o)}</button>)}
    </div>
  );
}

// ---- filings digest -------------------------------------------------------------------

const WINDOWS: [string, number][] = [["24h", 24], ["7d", 24 * 7], ["30d", 24 * 30]];
const ATTENTION = 50; // full card; 30-49 is a one-line "worth a look"; below 30 is routine

export function FilingsView({ index }: { index: CompanySummary[] }) {
  const digest = useData<Digest>(loadDigest);
  const ledgers = useLedgers(index);
  const threads = useMemo(() => new Map([...ledgers.values()].flatMap((l) => l.threads.map((t) => [t.id, t] as const))), [ledgers]);
  const [hours, setHours] = useState(24);
  const [showRoutine, setShowRoutine] = useState(false);
  const openId = window.location.hash.split("/")[2];

  useEffect(() => {  // a deep link to an older filing widens the window to include it
    if (!digest || !openId) return;
    const f = digest.items.find((x) => x.id === openId);
    if (f) {
      const age = (new Date(digest.built).getTime() - new Date(f.at).getTime()) / 3600_000;
      if (age > hours) setHours(age <= 24 * 7 ? 24 * 7 : 24 * 30);
      setTimeout(() => document.getElementById(`f-${openId}`)?.scrollIntoView({ block: "center" }), 50);
    }
  }, [digest, openId]);

  if (digest === undefined) return <Shell tab="filings"><p className="empty">Loading filings…</p></Shell>;
  if (digest === null) return <Shell tab="filings"><p className="empty">No filings digest yet. Run scripts.build_push.</p></Shell>;
  const items = inWindow(digest, hours);
  const byRank = (a: Filing, b: Filing) => b.importance - a.importance || b.at.localeCompare(a.at);
  const material = items.filter((f) => f.importance >= ATTENTION).sort(byRank);
  const notable = items.filter((f) => !f.routine && f.importance < ATTENTION).sort(byRank);
  const routine = items.filter((f) => f.routine);
  const counts = WINDOWS.map(([, h]) => inWindow(digest, h).filter((f) => f.importance >= ATTENTION).length);

  return (
    <Shell tab="filings">
      <section>
        <h2 className="section-title">Filings</h2>
        <p className="section-sub">Every exchange filing across your {index.length} companies, most important first.</p>
        <div className="segmented wide">
          {WINDOWS.map(([label, h], i) => (
            <button key={label} className={h === hours ? "on" : ""} onClick={() => setHours(h)}>
              {label} <span className="seg-n">{counts[i]}</span>
            </button>
          ))}
        </div>
      </section>

      {material.length === 0 ? (
        <p className="quiet-note">Nothing that needs your attention in this window.</p>
      ) : (
        <div className="cards filings">
          {material.map((f) => <FilingCard key={f.id} f={f} threads={threads} ledger={ledgers.get(f.symbol)} open={f.id === openId} />)}
        </div>
      )}

      {notable.length > 0 && (
        <section>
          <h2 className="section-title">Worth a look <span className="count">{notable.length}</span></h2>
          <div className="cards filings">
            {notable.map((f) => <FilingCard key={f.id} f={f} threads={threads} ledger={ledgers.get(f.symbol)} open={f.id === openId} compact />)}
          </div>
        </section>
      )}

      {routine.length > 0 && (
        <section>
          <button className="routine-toggle" onClick={() => setShowRoutine(!showRoutine)}>
            <span>{routine.length} routine filings</span>
            <span className="routine-kinds">{topKinds(routine)}</span>
            <span className="chev">{showRoutine ? "▴" : "▾"}</span>
          </button>
          {showRoutine && (
            <div className="routine-list">
              {routine.map((f) => (
                <a key={f.id} className="routine-row" href={f.pdf ?? undefined} target="_blank" rel="noreferrer">
                  <span className="rr-co">{f.name}</span>
                  <span className="rr-sub">{f.label} · {cleanSubject(f.subject)}</span>
                  <span className="rr-time">{fmtDate(f.at)}</span>
                </a>
              ))}
            </div>
          )}
        </section>
      )}
      <Footnote>
        From BSE corporate announcements, as of {fmtStamp(digest.built)}. Summaries and appointee profiles are read from
        each filing's PDF; ranking is FINN's.
      </Footnote>
    </Shell>
  );
}

function FilingCard({ f, threads, ledger, open, compact }: {
  f: Filing; threads: Map<string, Thread>; ledger?: Ledger; open: boolean; compact?: boolean;
}) {
  const linked = f.promises.map((id) => threads.get(id)).filter(Boolean) as Thread[];
  // Results and management changes have their own layouts; every other kind gets the brief.
  const b = f.type !== "results" && f.type !== "management_change" ? brief(f, ledger) : null;
  return (
    <article id={`f-${f.id}`} className={`card filing ${open ? "focus" : ""} ${compact ? "compact" : ""}`}>
      <div className="card-top">
        <button className="filing-co co-link" onClick={() => go(`/c/${f.symbol}`)}>{f.name} ›</button>
        <span className="filing-time">{fmtDate(f.at)}</span>
      </div>
      <div className="filing-head">
        <span className={`pill kind-${f.type.replace(/[^a-z]/gi, "")}`}>{f.label}</span>
        <span className={`filing-title ${f.tone === "positive" ? "up" : f.tone === "negative" ? "down" : ""}`}>
          {f.type === "results" ? resultsHeadline(f) : filingText(f.summary, f.subject)}</span>
      </div>

      {f.numbers && <Numbers h={f.numbers} />}
      {f.numbers && /filing/.test(f.numbers.source) && <p className="footnote">Figures read from {f.numbers.source}.</p>}

      {f.people.map((p, i) => {  // one person can appear twice, once per role
        const leaving = ["resigned", "retired", "ceased"].includes(p.change);
        const theirs = p.promises.map((id) => threads.get(id)).filter(Boolean) as Thread[];
        return (
          <div key={`${p.name}-${i}`} className="person">
            <div className="person-head">
              <b>{p.name}</b> <span className="person-role">{p.role}</span>
              <span className={`person-change ${leaving ? "out" : "in"}`}>{p.change}{p.effective ? ` · ${p.effective}` : ""}</span>
            </div>
            {p.profile && !leaving && <p className="person-profile">{p.profile}</p>}
            {leaving && (theirs.length
              ? <p className="person-profile">Made {theirs.length} of the promises FINN tracks. Who carries them now?</p>
              : <p className="person-profile">Not a speaker on the calls FINN has read.</p>)}
            {theirs.length > 0 && <PromiseChips ts={theirs.slice(0, 4)} />}
            {theirs.length > 4 && (
              <button className="link small" onClick={() => go(`/c/${f.symbol}`)}>+{theirs.length - 4} more in the {f.name} record</button>
            )}
          </div>
        );
      })}

      {b && b.facts.length > 0 && (
        <div className="facts">
          {b.facts.map((x) => <span key={x.label} className="fact"><span className="fact-l">{x.label}</span><b className={x.tone ?? ""}>{x.value}</b></span>)}
        </div>
      )}
      {b?.why && <p className="why"><span className="why-h">Why it matters</span>{b.why}</p>}
      {b && (b.touches.length ? (
        <div className="filing-promises">
          <span className="fp-label">{b.touchLabel}</span>
          <PromiseRows ts={b.touches} />
        </div>
      ) : <p className="fp-none">{f.type === "earnings_call" ? "No tracked promise moved on this call." : "Touches none of the promises FINN tracks."}</p>)}
      {!b && linked.length > 0 && (
        <div className="filing-promises">
          <span className="fp-label">{f.type === "results" ? "What these results mean for their promises" : "Relates to"}</span>
          <PromiseRows ts={linked} quarter={f.type === "results" ? f.numbers?.quarter ?? resultsQuarter(f) : undefined} />
        </div>
      )}

      <div className="filing-links">
        <button className="filing-link link" onClick={() => go(`/c/${f.symbol}`)}>Company record ›</button>
        {f.pdf && <a className="filing-link" href={f.pdf} target="_blank" rel="noreferrer">Open filing ↗</a>}
        {(f.also ?? []).map((a, i) => <a key={i} className="filing-link" href={a.pdf} target="_blank" rel="noreferrer">{a.label} ↗</a>)}
      </div>
    </article>
  );
}

/** Highest quarter a span label covers: "H1FY27" → 2, "Q3FY27" → 3, "FY27" → 4. */
function lastQuarter(label: string): number | null {
  const m = label.match(/(?:Q(\d)|H(\d)|^)FY\d{2}$/);
  if (!m) return null;
  return m[1] ? Number(m[1]) : m[2] ? Number(m[2]) * 2 : 4;
}

/** One row per promise: name and verdict, then what was guided against what's come in. */
function PromiseRows({ ts, quarter }: { ts: Thread[]; quarter?: string }) {
  // Promises still running first; ones last year's results already settled after.
  const rows = [...ts].sort((a, b) => Number(b.status === "tracking") - Number(a.status === "tracking"));
  const q = quarter?.match(/Q([1-4])FY(\d{2})/);
  const stale = q ? rows.map((t) => t.pace?.so_far_label).find((l) =>
    l && l.endsWith(`FY${q[2]}`) && (lastQuarter(l) ?? 4) < Number(q[1])) : undefined;
  return (
    <div className="prows">
      {rows.map((t) => {
        const s = statusOf(t);
        const sym = t.id.replace(/-\d+$/, "");
        // "Reported revenue growth 16.2% for FY26" → 16.2%, FY26
        const done = t.detail?.match(/^Reported .*? (-?[\d.,]+%) for (\S+)$/);
        const actual = t.pace ? { when: normText(t.pace.so_far_label), value: `${t.pace.so_far.toFixed(1)}%` }
          : done ? { when: done[2], value: done[1] } : null;
        return (
          <button key={t.id} className="prow-p" onClick={() => go(`/c/${sym}/t/${t.id}`)}>
            <span className="prow-p-top">
              <span className="prow-p-title">{t.title}</span>
              <span className={`prow-p-status tone-text-${s.tone}`}>{s.label}</span>
            </span>
            <span className="prow-p-figs">
              {t.target && <span><span className="k">Guided</span> <b>{normText(t.target)}</b></span>}
              {actual && <span><span className="k">{actual.when}</span> <b className={`tone-text-${s.tone}`}>{actual.value}</b></span>}
            </span>
            {!actual && t.detail && <span className="prow-p-note">{normText(t.detail)}</span>}
            {t.pace?.needs != null && t.pace.needs_label && (
              <span className="prow-p-note">Needs {t.pace.needs.toFixed(1)}% in {normText(t.pace.needs_label)} to reach {t.pace.needs_for}%</span>)}
          </button>
        );
      })}
      {stale && quarter && <p className="footnote">Progress uses {stale} figures; {quarter} isn't in the financials FINN reads yet.</p>}
    </div>
  );
}

function PromiseChips({ ts, detail }: { ts: Thread[]; detail?: boolean }) {
  return (
    <div className="pchips">
      {ts.map((t) => {
        const s = statusOf(t);
        const sym = t.id.replace(/-\d+$/, "");
        return (
          <button key={t.id} className="pchip" onClick={() => go(`/c/${sym}/t/${t.id}`)}>
            <span className="pchip-title">{t.title}{t.target ? ` · ${t.target}` : ""}</span>
            <span className={`pchip-status tone-text-${s.tone}`}>{s.label}{detail && t.pace ? `: ${normText(t.pace.so_far_label)} at ${t.pace.so_far.toFixed(1)}%` : ""}</span>
          </button>
        );
      })}
    </div>
  );
}

function Numbers({ h }: { h: Headline }) {
  // Same order as the company page: YoY first and bold, QoQ muted; the title already names the quarter.
  return (
    <div className="numbers">
      <div className="num-row num-head"><span /><span>₹ cr</span><span>YoY</span><span>QoQ</span></div>
      {h.rows.map((r) => (
        <div key={r.label} className="num-row">
          <span>{r.label}</span>
          <span className="num-v">{Math.round(r.value).toLocaleString("en-IN")}</span>
          <span className={`num-yoy ${tone(r.yoy)}`}>{fmtChange(r.yoy)}</span>
          <span className="num-qoq">{fmtChange(r.qoq)}</span>
          {h.notes?.filter((n) => n.row === r.label).map((n, i) => <span key={i} className="row-note">{n.text}</span>)}
        </div>
      ))}
    </div>
  );
}

// ---- text helpers ---------------------------------------------------------------------


function peopleLine(f: Filing): string {
  // Whoever made tracked promises first, then everyone else; two names is enough for a row.
  const ps = [...f.people].sort((a, b) => b.promises.length - a.promises.length);
  const shown = ps.slice(0, 2).map((p) =>
    `${p.name} ${p.change}${p.promises.length ? ` (made ${p.promises.length} tracked promises)` : ""}`);
  return shown.join(" · ") + (ps.length > 2 ? ` · +${ps.length - 2} more` : "");
}

function topKinds(fs: Filing[]): string {
  const c = new Map<string, number>();
  fs.forEach((f) => {
    const k = /newspaper/i.test(f.subject) ? "newspaper ads" : /investor|analyst/i.test(f.subject) ? "investor meets"
      : /certificate|compliance/i.test(f.subject) ? "compliance" : f.label.toLowerCase();
    c.set(k, (c.get(k) ?? 0) + 1);
  });
  return [...c.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => k).join(", ");
}



function fmtStamp(s: string): string {
  return `${fmtDate(s)}, ${s.slice(11, 16)}`;
}
