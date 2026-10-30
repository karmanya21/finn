// Push: alerts against the user's own thresholds, and the filings digest.

import { useEffect, useMemo, useState } from "react";
import {
  ALERT_TYPES, AlertSettings, CompanySummary, Digest, Filing, Headline, Ledger, Prices, Thread,
  feed, inWindow, loadDigest, loadLedger, loadPrices, moveAlerts, readSettings, saveSettings, statusOf,
} from "./data";
import { cleanSubject, filingText, fmtChange, fmtDate, normText, priceWhen, tone } from "./format";
import { Shell, go } from "./shell";

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

/** Every thread in the portfolio by id, so filings can name the promises they touch. */
function useThreads(index: CompanySummary[]): Map<string, Thread> {
  const [m, setM] = useState(new Map<string, Thread>());
  useEffect(() => {
    Promise.all(index.map((c) => loadLedger(c.symbol).catch(() => null))).then((ls) => {
      const next = new Map<string, Thread>();
      ls.forEach((l: Ledger | null) => l?.threads.forEach((t) => next.set(t.id, t)));
      setM(next);
    });
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
  open: () => void;
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
    Promise.all(index.map((c) => loadLedger(c.symbol).catch(() => null)))
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
        line: m.reason ? `Same day: ${m.reason.type === "results" ? resultsHeadline(m.reason) : filingText(m.reason.summary, m.reason.subject)}`
          : `Closed ₹${m.close.toLocaleString("en-IN")}; no material filing that day`,
        open: () => go(`/c/${m.symbol}`) });
    }
    for (const f of digest.items) {
      if (f.routine || !s.types.includes(f.type) || f.at.slice(0, 10) < cutoff) continue;
      if (f.type === "management_change" && !f.senior) continue;  // only CEO, CFO, MD, Chair
      const yoy = f.numbers?.rows.find((r) => r.label === "PAT")?.yoy;
      // Results without figures yet say little; let a real move or a cut outrank them.
      const blank = f.type === "results" && !f.numbers ? 25 : 0;
      add({ key: `f-${f.id}`, symbol: f.symbol, name: f.name, weight: f.importance + recent(f.at.slice(0, 10)) - blank,
        tag: f.label, date: fmtDate(f.at), title: f.type === "results" ? resultsHeadline(f) : filingText(f.summary, f.subject),
        tone: f.type === "results" && yoy != null ? (yoy >= 0 ? "up" : "down")
          : f.tone === "positive" ? "up" : f.tone === "negative" ? "down" : undefined,
        line: f.people.length ? peopleLine(f) : undefined,
        open: () => go(`/filings/${f.id}`) });
    }
    const bySym = new Map(ledgers.map((l) => [l.symbol, l]));
    const VERDICT = new Set(["Missed", "Kept", "Beat"]);
    for (const i of feed(ledgers)) {
      const l = bySym.get(i.symbol);
      if (!l?.latest_call || daysBetween(l.latest_call, lastSession) > RECENT_CALL_DAYS) continue;
      // A verdict is news only if the latest results settled it (period ends this quarter).
      if (VERDICT.has(i.tag) && !settledBy(i.thread.period, l.latest_results)) continue;
      const lastEvent = i.thread.events[i.thread.events.length - 1];
      const date = VERDICT.has(i.tag) || i.tag === "Behind pace" ? `${l.latest_results ?? ""} results`
        : i.tag === "Gone quiet" ? `${l.latest_call} call` : `${lastEvent?.call ?? l.latest_call} call`;
      add({ key: `p-${i.thread.id}|${i.headline}`, symbol: i.symbol, name: i.name, weight: i.weight,
        tag: i.tag, date, title: normText(i.headline), line: i.line && normText(i.line),
        tone: i.tone === "missed" ? "down" : i.tone === "kept" ? "up" : undefined,
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

/** The Today grid: one tile per company, those needing attention first. */
export function TodayGrid({ index, onSettings }: { index: CompanySummary[]; onSettings: () => void }) {
  const updates = useUpdates(index);
  const s = useSettings();
  const allKeys = useMemo(() => [...(updates?.values() ?? [])].flat().map((u) => u.key), [updates]);
  const seen = useSeenKeys(allKeys);
  if (!updates) return <p className="empty">Loading…</p>;

  const tiles = index.map((c) => {
    const us = updates.get(c.symbol) ?? [];
    const fresh = seen.size > 0 ? us.filter((u) => !seen.has(u.key)).length : 0;
    // The tile leads with the top update that carries a number ("+5.4%", "Revenue +22%"),
    // falling back to the top one only if none does.
    const lead = us.find((u) => /\d/.test(u.title)) ?? us[0];
    return { c, us, lead, fresh, score: us.length ? us[0].weight + Math.min(us.length, 5) * 2 : -1 };
  }).sort((a, b) => b.score - a.score);
  const active = tiles.filter((t) => t.us.length);
  const quiet = tiles.filter((t) => !t.us.length);

  return (
    <section>
      <h2 className="section-title">Needs attention <span className="count">{active.length}</span></h2>
      <div className="tiles">
        {active.map(({ c, us, lead, fresh }) => (
          <button key={c.symbol} className="tile" onClick={() => go(`/u/${c.symbol}`)}>
            <span className="tile-top">
              <span className="tile-name">{c.name}</span>
              <span className="tile-count">{us.length}{fresh > 0 && <span className="new">{fresh} new</span>}</span>
            </span>
            <span className="tile-tag">{lead!.tag} · {lead!.date}</span>
            <span className={`tile-line ${lead!.tone ?? ""}`}>{lead!.title}</span>
          </button>
        ))}
      </div>
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
export function PriceLine({ symbol, fallback, fallbackAsof }: {
  symbol: string; fallback?: [string, number][] | null; fallbackAsof?: number | null;
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
      <Sparkline ys={last3m.map(([, c]) => c)} />
      <span className="price-date">{priceWhen(st.date, asof)} · chart: 3 months</span>
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

/** "Q2FY27: Revenue +16% YoY · PAT +27%". The one format for results everywhere. */
export function resultsLine(h: Headline): string {
  const row = (l: string) => h.rows.find((r) => r.label === l);
  const pct = (v: number | null | undefined) => v == null ? null : fmtChange(v);
  const rev = pct(row("Revenue")?.yoy), pat = pct(row("PAT")?.yoy);
  const bits = [rev && `Revenue ${rev} YoY`, pat && `PAT ${pat}`].filter(Boolean);
  return bits.length ? `${h.quarter}: ${bits.join(" · ")}` : `${h.quarter} results`;
}

function resultsHeadline(f: Filing): string {
  if (f.numbers) return resultsLine(f.numbers);
  const q = `${f.summary ?? ""} ${f.subject}`.match(/Q[1-4]\s?FY\s?\d{2}/i)?.[0].replace(/\s/g, "").toUpperCase();
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
  const threads = useThreads(index);
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
          {material.map((f) => <FilingCard key={f.id} f={f} threads={threads} open={f.id === openId} built={digest.built} />)}
        </div>
      )}

      {notable.length > 0 && (
        <section>
          <h2 className="section-title">Worth a look <span className="count">{notable.length}</span></h2>
          <div className="feed">
            {notable.map((f) => (
              <a key={f.id} id={`f-${f.id}`} className={`feed-row ${f.id === openId ? "focus" : ""}`} href={f.pdf ?? undefined} target="_blank" rel="noreferrer">
                <span className="feed-body">
                  <span className="feed-co">{f.name} · {f.label} · {fmtDate(f.at)}</span>
                  <span className="feed-line strong">{filingText(f.summary, f.subject)}</span>
                </span>
              </a>
            ))}
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

function FilingCard({ f, threads, open, built }: { f: Filing; threads: Map<string, Thread>; open: boolean; built: string }) {
  const linked = f.promises.map((id) => threads.get(id)).filter(Boolean) as Thread[];
  return (
    <article id={`f-${f.id}`} className={`card filing ${open ? "focus" : ""}`}>
      <div className="card-top">
        <span className="filing-co">{f.name}</span>
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

      {linked.length > 0 && (
        <div className="filing-promises">
          <span className="fp-label">{f.type === "results" ? "What these results mean for their promises" : "Relates to"}</span>
          <PromiseChips ts={linked} detail />
        </div>
      )}

      <div className="filing-links">
        {f.pdf && <a className="filing-link" href={f.pdf} target="_blank" rel="noreferrer">Open filing ↗</a>}
        {(f.also ?? []).map((a, i) => <a key={i} className="filing-link" href={a.pdf} target="_blank" rel="noreferrer">{a.label} ↗</a>)}
      </div>
    </article>
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
