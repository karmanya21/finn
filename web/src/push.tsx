// Push: alerts against the user's own thresholds, and the filings digest.

import { useEffect, useMemo, useState } from "react";
import {
  ALERT_TYPES, AlertSettings, CompanySummary, Digest, Filing, Headline, Ledger, MoveAlert, Prices, Thread,
  inWindow, loadDigest, loadLedger, loadPrices, moveAlerts, readSettings, saveSettings, statusOf,
} from "./data";
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

// ---- Today: alerts ----------------------------------------------------------------

type AlertRow = { key: string; date: string; move?: MoveAlert; filing?: Filing };

export function Alerts({ index, onSettings }: { index: CompanySummary[]; onSettings: () => void }) {
  const prices = useData<Prices>(loadPrices);
  const loaded = useData<Digest>(loadDigest);
  const digest = loaded === null ? NO_FILINGS : loaded;  // price alerts don't need filings
  const s = useSettings();
  const names = useMemo(() => new Map(index.map((c) => [c.symbol, c.name])), [index]);
  const [all, setAll] = useState(false);

  const rows = useMemo<AlertRow[]>(() => {
    if (!prices || !digest) return [];
    const moves = moveAlerts(prices, s, 5, digest.items);
    const since = moves.length ? moves[moves.length - 1].date : "";
    const anyDates = Object.values(prices.series)[0] || [];
    const cutoff = anyDates.length >= 5 ? anyDates[anyDates.length - 5][0] : since;
    const filings = digest.items.filter((f) => !f.routine && s.types.includes(f.type) && f.at.slice(0, 10) >= cutoff);
    const r: AlertRow[] = [
      ...moves.map((m) => ({ key: `m-${m.symbol}-${m.date}`, date: m.date, move: m })),
      ...filings.map((f) => ({ key: `f-${f.id}`, date: f.at.slice(0, 10), filing: f })),
    ];
    return r.sort((a, b) => b.date.localeCompare(a.date) ||
      (b.filing?.importance ?? Math.abs(b.move!.pct) * 10) - (a.filing?.importance ?? Math.abs(a.move!.pct) * 10));
  }, [prices, digest, s]);

  if (!prices || !digest) return null;
  const shown = all ? rows : rows.slice(0, 5);
  return (
    <section>
      <h2 className="section-title">Alerts</h2>
      <p className="section-sub">
        Last 5 sessions · volume ≥{s.volume}× its 20-day average, moves beyond ±{s.move}%, and {s.types.length} filing
        types. <button className="link" onClick={onSettings}>Change</button>
      </p>
      {rows.length === 0 ? <p className="quiet-note">Nothing crossed your thresholds. FINN is staying quiet.</p> : (
        <div className="feed">
          {shown.map((r) => r.move ? <MoveRow key={r.key} a={r.move} name={names.get(r.move.symbol) ?? r.move.symbol} />
            : <FilingAlertRow key={r.key} f={r.filing!} />)}
        </div>
      )}
      {rows.length > 5 && <button className="more" onClick={() => setAll(!all)}>{all ? "Show less" : `${rows.length - 5} more`}</button>}
    </section>
  );
}

function MoveRow({ a, name }: { a: MoveAlert; name: string }) {
  const up = a.pct >= 0;
  const what = [a.byPrice && `${up ? "+" : ""}${a.pct.toFixed(1)}% on the day`, a.byVolume && `volume ${a.volRatio.toFixed(1)}× average`]
    .filter(Boolean).join(" · ");
  return (
    <button className="feed-row" onClick={() => go(`/c/${a.symbol}`)}>
      <span className={`dot tone-${a.byPrice ? (up ? "kept" : "missed") : "live"}`} />
      <span className="feed-body">
        <span className="feed-co">{name} · {fmtDate(a.date)}</span>
        <span className="feed-head">{what}</span>
        <span className="feed-line">
          Closed ₹{a.close.toLocaleString("en-IN")}
          {a.reason ? ` · same day: ${a.reason.label.toLowerCase()}, ${a.reason.summary ?? a.reason.subject}` : " · no material filing that day"}
        </span>
      </span>
    </button>
  );
}

function FilingAlertRow({ f }: { f: Filing }) {
  return (
    <button className="feed-row" onClick={() => go(`/filings/${f.id}`)}>
      <span className="dot tone-moved" />
      <span className="feed-body">
        <span className="feed-co">{f.name} · {fmtDate(f.at)}</span>
        <span className="feed-head">{f.type === "results" ? (f.summary ?? "Results") : `${f.label}: ${f.summary ?? cleanSubject(f.subject)}`}</span>
        {f.people.length > 0 && <span className="feed-line">{peopleLine(f)}</span>}
      </span>
    </button>
  );
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
        <p className="section-sub">Every exchange filing across your {index.length} companies, most important first. As of {fmtStamp(digest.built)}.</p>
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
                <span className="dot tone-muted" />
                <span className="feed-body">
                  <span className="feed-co">{f.name} · {f.label} · {ago(f.at, digest.built)}</span>
                  <span className="feed-line strong">{f.summary ?? cleanSubject(f.subject)}</span>
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
                  <span className="rr-time">{ago(f.at, digest.built)}</span>
                </a>
              ))}
            </div>
          )}
        </section>
      )}
    </Shell>
  );
}

function FilingCard({ f, threads, open, built }: { f: Filing; threads: Map<string, Thread>; open: boolean; built: string }) {
  const linked = f.promises.map((id) => threads.get(id)).filter(Boolean) as Thread[];
  return (
    <article id={`f-${f.id}`} className={`card filing ${open ? "focus" : ""}`}>
      <div className="card-top">
        <span className="filing-co">{f.name}</span>
        <span className="filing-time">{ago(f.at, built)}</span>
      </div>
      <div className="filing-head">
        <span className={`pill kind-${f.type.replace(/[^a-z]/gi, "")}`}>{f.label}</span>
        <span className="filing-title">{f.summary ?? cleanSubject(f.subject)}</span>
      </div>

      {f.numbers && <Numbers h={f.numbers} />}
      {f.type === "results" && !f.numbers && <p className="basis">Numbers not yet in the consolidated table; open the filing.</p>}

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
            <span className={`pdot tone-${s.tone}`} />
            <span className="pchip-title">{t.title}{t.target ? ` · ${t.target}` : ""}</span>
            {detail && <span className="pchip-status">{s.label}{t.pace ? `: ${t.pace.so_far_label} at ${t.pace.so_far.toFixed(1)}%` : ""}</span>}
          </button>
        );
      })}
    </div>
  );
}

function Numbers({ h }: { h: Headline }) {
  const chg = (v: number | null) => v === null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(0)}%`;
  return (
    <div className="numbers">
      <div className="num-row num-head"><span>{h.quarter}</span><span>₹ cr</span><span>QoQ</span><span>YoY</span></div>
      {h.rows.map((r) => (
        <div key={r.label} className="num-row">
          <span>{r.label}</span>
          <span className="num-v">{Math.round(r.value).toLocaleString("en-IN")}</span>
          <span className={r.qoq === null ? "" : r.qoq >= 0 ? "up" : "down"}>{chg(r.qoq)}</span>
          <span className={r.yoy === null ? "" : r.yoy >= 0 ? "up" : "down"}>{chg(r.yoy)}</span>
        </div>
      ))}
    </div>
  );
}

// ---- text helpers ---------------------------------------------------------------------

function cleanSubject(s: string): string {
  return s.replace(/^Announcement under Regulation 30 \(LODR\)\s*-\s*/i, "")
    .replace(/^Compliances?-\s*/i, "").replace(/_/g, " ");
}

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

function ago(at: string, now: string): string {
  const h = (new Date(now).getTime() - new Date(at).getTime()) / 3600_000;
  if (h < 1) return "just now";
  if (h < 24) return `${Math.floor(h)}h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? "yesterday" : `${d}d ago`;
}

function fmtDate(s: string): string {
  return new Date(s.length > 10 ? s : `${s}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function fmtStamp(s: string): string {
  return new Date(s).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}
