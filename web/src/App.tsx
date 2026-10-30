import { useEffect, useMemo, useRef, useState } from "react";
import { CompanyUpdates, FilingsView, Footnote, PriceLine, SettingsSheet, TodayGrid, priceStats, resultsLine, useNewerResults, usePrices } from "./push";
import { fmtChange, normText, priceWhen, tone as toneOf } from "./format";
import { Shell, go } from "./shell";
import {
  Business, CasePoint, CompanySummary, Headline, KIND_LABEL, Ledger, MOVE_VERB, Metric, ResearchStatus, SearchResult, Thread, Tone,
  loadIndex, loadLedger, loadResearchIndex, loadShipped, researchStatus, searchCompanies, startResearch, statusOf,
} from "./data";

// ---- routing: #/  and  #/c/INFY  and  #/c/INFY/t/INFY-3 ------------------------

function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [r, setR] = useState(read);
  useEffect(() => {
    const on = () => setR(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

// On a wide screen the app sits in a phone-sized frame, so it looks and behaves the same
// as on a phone. The frame is its own scroll container and, through a transform, the
// containing block for the fixed-position sheet, so overlays stay inside the "phone".
export default function App() {
  const route = useRoute();
  const screen = useRef<HTMLDivElement>(null);
  useEffect(() => {  // a new page starts at the top, the way an app navigates
    if (route[2] !== "t") screen.current?.scrollTo(0, 0);
    if (route[2] !== "t") window.scrollTo(0, 0);
  }, [route[0], route[1]]);
  return (
    <div className="device">
      <StatusBar />
      <div className="screen" ref={screen}><Routes route={route} /></div>
    </div>
  );
}

function StatusBar() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 30_000); return () => clearInterval(id); }, []);
  return (
    <div className="statusbar" aria-hidden>
      <span>{now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(/\s?[AP]M$/i, "")}</span>
      <span className="island" />
      <span className="sb-icons">●●● ▮</span>
    </div>
  );
}

function Routes({ route }: { route: string[] }) {
  const [index, setIndex] = useState<CompanySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { loadIndex().then(setIndex).catch((e) => setError(String(e))); }, []);

  if (error) return <Shell><p className="empty">Couldn't load data: {error}</p></Shell>;
  if (!index) return <Shell><p className="empty">Loading…</p></Shell>;
  if (route[0] === "c" && route[1]) return <CompanyView symbol={route[1]} threadId={route[3]} />;
  if (route[0] === "r" && route[1]) {
    const sym = decodeURIComponent(route[1]).toUpperCase();
    return <ResearchView symbol={sym} threadId={route[3]} portfolio={index.some((c) => c.symbol === sym)} index={index} />;
  }
  if (route[0] === "u" && route[1]) return <CompanyUpdates index={index} symbol={decodeURIComponent(route[1])} />;
  if (route[0] === "filings") return <FilingsView index={index} />;
  if (route[0] === "portfolio") return <Portfolio index={index} />;
  return <Home index={index} />;
}

// ---- today (home) ------------------------------------------------------------------

function Home({ index }: { index: CompanySummary[] }) {
  const [settings, setSettings] = useState(false);
  return (
    <Shell tab="today" action={<button className="icon-btn" onClick={() => setSettings(true)} aria-label="Alert settings">
      <svg viewBox="0 0 24 24" aria-hidden><path d="M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6" /></svg></button>}>
      <SearchBar index={index} />
      <TodayGrid index={index} onSettings={() => setSettings(true)} />
      {settings && <SettingsSheet onClose={() => setSettings(false)} />}
    </Shell>
  );
}

// ---- portfolio: a compact overview, sortable --------------------------------------------

type Sort = "name" | "kept" | "missed" | "quiet";

function Portfolio({ index }: { index: CompanySummary[] }) {
  const prices = usePrices();
  const [sort, setSort] = useState<Sort>("name");
  const keptRate = (c: CompanySummary) => c.record.resolved ? c.record.kept / c.record.resolved : -1;
  const rows = [...index].sort((a, b) =>
    sort === "kept" ? keptRate(b) - keptRate(a) || b.record.kept - a.record.kept
      : sort === "missed" ? b.record.missed - a.record.missed
      : sort === "quiet" ? b.record.quiet - a.record.quiet
      : a.name.localeCompare(b.name));
  return (
    <Shell tab="portfolio">
      <section>
        <h2 className="section-title">Portfolio <span className="count">{index.length}</span></h2>
        <nav className="chips tight">
          {(["name", "kept", "missed", "quiet"] as Sort[]).map((k) => (
            <button key={k} className={`chip ${sort === k ? "on" : ""}`} onClick={() => setSort(k)}>
              {{ name: "A–Z", kept: "Kept", missed: "Missed", quiet: "Quiet" }[k]}
            </button>
          ))}
        </nav>
        <div className="plist">
          {rows.map((c) => {
            const st = priceStats(prices?.series[c.symbol]?.map(([d, cl]) => [d, cl] as [string, number]));
            const r = c.record;
            return (
              <button key={c.symbol} className="prow" onClick={() => go(`/c/${c.symbol}`)}>
                <span className="prow-main">
                  <span className="prow-name">{c.name}</span>
                  <span className="prow-rec">
                    <span className={sort === "kept" ? "hl" : ""}>{r.resolved ? `${r.kept}/${r.resolved} kept` : "none settled"}</span>
                    <span className={sort === "missed" ? "hl" : ""}>{r.missed} missed</span>
                    <span className={sort === "quiet" ? "hl" : ""}>{r.quiet} quiet</span>
                  </span>
                </span>
                {st && (
                  <span className="prow-price">
                    <span>₹{st.close.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>
                    <span className={toneOf(st.change, 1)}>{fmtChange(st.change, 1)}</span>
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <Footnote>
          Kept and missed count settled numbers and milestones only. Prices: NSE via Yahoo Finance
          {(() => { const d = prices && Object.values(prices.series)[0]?.slice(-1)[0]?.[0];
            const a = prices?.asof && Object.values(prices.asof)[0];
            return d ? `, ${priceWhen(d, a)}` : ""; })()}.
        </Footnote>
      </section>
    </Shell>
  );
}

// ---- company -------------------------------------------------------------------

type Filter = "all" | "live" | "settled" | "moved" | "quiet";

const LIVE = ["tracking", "on_track", "behind", "delayed", "open", "cant_verify"];

/** A target due after next fiscal year (or a multi-year plan) can't move this quarter. */
function farDated(t: Thread, fy: number): boolean {
  if (/multi/i.test(t.period)) return true;
  const m = t.period.match(/FY(\d{2})/);
  return !!m && Number(m[1]) > fy + 1;
}

/** Pending: nothing to check yet (no numbers in, not due) or due after next fiscal year. */
function pending(t: Thread, fy: number): boolean {
  return t.status === "open" || farDated(t, fy);
}

const GROUPS: { key: string; title: string; test: (t: Thread, fy: number) => boolean; collapsed?: (n: number, fy: number) => string }[] = [
  { key: "live", title: "Live", test: (t, fy) => LIVE.includes(t.status) && t.kind !== "soft" && !pending(t, fy) },
  { key: "settled", title: "Settled", test: (t) => ["delivered", "beat", "missed"].includes(t.status) },
  { key: "quiet", title: "Gone quiet", test: (t) => t.status === "gone_quiet" },
  { key: "later", title: "Due later", test: (t, fy) => LIVE.includes(t.status) && t.kind !== "soft" && pending(t, fy),
    collapsed: (n) => `${n} more due later` },
  { key: "said", title: "Also said", test: (t) => t.status === "said" || t.status === "lapsed",
    collapsed: (n) => `${n} ambitions and remarks, not scored` },
];

// Headline P&L promises first, then what investors model off, then the rest.
const METRIC_WEIGHT: { [m: string]: number } = {
  revenue_growth: 0, revenue: 0, order_inflow: 1, ebitda_margin: 1, operating_margin: 1,
  pat: 2, pat_growth: 2, roe: 2, loan_growth: 2, capex: 3, debt: 3, capacity: 3,
};

function rank(t: Thread): number {
  const kind = { hard: 0, milestone: 1, directional: 2, soft: 3 }[t.kind];
  const company = !t.scope || /company|group|consolidated/i.test(t.scope) ? 0 : 1;
  return kind * 100 + company * 30 + (METRIC_WEIGHT[t.metric] ?? 5) * 10 - (t.moved ? 3 : 0);
}

function CompanyView({ symbol, threadId }: { symbol: string; threadId?: string }) {
  const [l, setL] = useState<Ledger | null>(null);
  useEffect(() => { loadLedger(symbol).then(setL); }, [symbol]);
  if (!l) return <Shell back><p className="empty">Loading…</p></Shell>;
  return <LedgerView l={l} threadId={threadId} base={`/c/${symbol}`} />;
}

// ---- pull: research any company ---------------------------------------------------

const STEP_ORDER = ["company", "calls", "link", "verdicts", "done"];
const STEP_LABEL: { [s: string]: string } = {
  company: "Find the company",
  calls: "Read the earnings calls",
  link: "Follow each promise across calls",
  verdicts: "Check against results and later calls",
  done: "Done",
};

function ResearchView({ symbol, threadId, portfolio, index }: {
  symbol: string; threadId?: string; portfolio: boolean; index: CompanySummary[];
}) {
  const [s, setS] = useState<ResearchStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [shipped, setShipped] = useState<Ledger | null | undefined>(undefined);
  useEffect(() => { loadShipped(symbol).then(setShipped); }, [symbol]);
  useEffect(() => {
    if (portfolio) { go(`/c/${symbol}`); return; }
    if (shipped !== null) return;  // still checking, or already have it: no API call
    let alive = true, timer = 0;
    const poll = async (first: boolean) => {
      try {
        const st = first ? await startResearch(symbol) : await researchStatus(symbol);
        if (!alive) return;
        setS(st);
        if (st.state === "queued" || st.state === "running") timer = window.setTimeout(() => poll(false), 1500);
      } catch (e) { if (alive) setErr(String(e instanceof Error ? e.message : e)); }
    };
    poll(true);
    return () => { alive = false; clearTimeout(timer); };
  }, [symbol, portfolio, shipped]);

  if (shipped) return <LedgerView l={shipped} threadId={threadId} base={`/r/${symbol}`} />;
  if (s?.state === "done" && s.ledger) return <LedgerView l={s.ledger} threadId={threadId} base={`/r/${symbol}`} />;
  if (err) return <Offline symbol={symbol} index={index} />;
  const failed = s?.state === "error" ? s.error : null;
  const reached = new Set(s?.steps.map((x) => x.step));
  const current = s?.steps[s.steps.length - 1];

  return (
    <Shell back>
      <section className="co-head">
        <p className="eyebrow">Researching</p>
        <h1 className="co-title">{symbol}</h1>
        {failed ? (
          <div className="notice tone-border-missed"><b>Couldn't research {symbol}.</b><p>{failed}</p></div>
        ) : (
          <>
            <p className="co-sub">
              {s?.state === "queued" ? "Queued behind another company's research. The free model tier runs one at a time."
                : "Reading primary sources only: the company's own transcripts and reported results. The first run takes a minute or two; after that it's instant."}
            </p>
            <ol className="steps">
              {STEP_ORDER.slice(0, -1).map((k) => {
                const done = reached.has(k) && current?.step !== k;
                const now = current?.step === k;
                return (
                  <li key={k} className={done ? "done" : now ? "now" : ""}>
                    <span className="step-mark">{done ? "✓" : now ? <span className="spin" /> : ""}</span>
                    <span>
                      <span className="step-name">{STEP_LABEL[k]}</span>
                      {now && current?.detail && <span className="step-detail">{current.detail}</span>}
                    </span>
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </section>
    </Shell>
  );
}

/** Live research needs the API; when it's down, say so and offer what's already built. */
function Offline({ symbol, index }: { symbol: string; index: CompanySummary[] }) {
  const ready = useShipped(index);
  return (
    <Shell back>
      <section className="co-head">
        <p className="eyebrow">Researching</p>
        <h1 className="co-title">{symbol}</h1>
        <div className="notice"><b>Live research is offline.</b>
          <p>FINN can't reach its research service right now, so it can't build a new record. These companies are already built:</p></div>
      </section>
      <section><div className="plist">{ready.map((c) => (
        <button key={c.symbol} className="prow" onClick={() => go(c.portfolio ? `/c/${c.symbol}` : `/r/${c.symbol}`)}>
          <span className="prow-main"><span className="prow-name">{c.name}</span>
            <span className="prow-rec"><span>{c.portfolio ? "Portfolio" : "Research set"}</span></span></span>
        </button>))}</div></section>
    </Shell>
  );
}

type Shipped = { symbol: string; name: string; portfolio: boolean };
function useShipped(index: CompanySummary[]): Shipped[] {
  const [research, setResearch] = useState<{ symbol: string; name: string }[]>([]);
  useEffect(() => { loadResearchIndex().then(setResearch); }, []);
  return [...index.map((c) => ({ symbol: c.symbol, name: c.name, portfolio: true })),
          ...research.map((c) => ({ ...c, portfolio: false }))];
}

function SearchBar({ index }: { index: CompanySummary[] }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const shipped = useShipped(index);
  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    setBusy(true);
    const id = setTimeout(() => {
      searchCompanies(q.trim()).then((r) => { setResults(r); setOffline(false); })
        .catch(() => {  // API down: match what's already built
          const k = q.trim().toLowerCase();
          setOffline(true);
          setResults(shipped.filter((c) => c.name.toLowerCase().includes(k) || c.symbol.toLowerCase().includes(k))
            .map((c) => ({ symbol: c.symbol, name: c.name, ready: true })));
        }).finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(id);
  }, [q, shipped.length]);

  return (
    <div className="search">
      <input
        className="search-input" value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Research any company: name or NSE ticker" aria-label="Search companies"
        autoComplete="off" autoCorrect="off" spellCheck={false}
        onKeyDown={(e) => { if (e.key === "Enter" && results[0]) go(`/r/${results[0].symbol}`); }}
      />
      {q.trim().length >= 2 && (
        <div className="search-results">
          {results.map((r) => (
            <button key={r.symbol} className="search-row" onClick={() => go(`/r/${r.symbol}`)}>
              <span className="sr-name">{r.name}</span>
              <span className="sr-meta">{r.symbol}{r.ready ? " · record ready" : ""}</span>
            </button>
          ))}
          {offline && <p className="sr-empty">Live research is offline; showing companies FINN has already built.</p>}
          {!busy && !results.length && <p className="sr-empty">{offline ? `None of them match “${q.trim()}”.` : `No listed company matches “${q.trim()}”.`}</p>}
        </div>
      )}
    </div>
  );
}

// ---- snapshot and the case each way ------------------------------------------------

function CaseView({ l, base }: { l: Ledger; base: string }) {
  const threads = new Map(l.threads.map((t) => [t.id, t]));
  const metric = new Map((l.snapshot?.metrics ?? []).map((m) => [m.key, m.label]));
  const side = (title: string, pts: CasePoint[], tone: string) => (
    <div className={`case-side case-${tone}`}>
      <h3>{title}</h3>
      <ul>
        {pts.map((p, i) => (
          <li key={i}>
            {p.figure && <b className="figure">{normText(p.figure)}</b>}{normText(p.point)}
            <span className="refs">
              {p.refs.map((r) => threads.has(r)
                ? <button key={r} className="ref" onClick={() => go(`${base}/t/${r}`)}>{threads.get(r)!.title}</button>
                : <span key={r} className="ref num">{metric.get(r) ?? r}</span>)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
  return (
    <div className="case">
      {l.case!.bull.length > 0 && side("Bull", l.case!.bull, "bull")}
      {l.case!.bear.length > 0 && side("Bear", l.case!.bear, "bear")}
    </div>
  );
}

function BusinessView({ b }: { b: Business }) {
  const segs = b.segments.filter((s) => s.share_pct !== null);
  const geos = b.geographies.filter((g) => g.share_pct !== null);
  const cite = `${b.source}${b.pages.length ? `, pp. ${b.pages.slice(0, 3).join(", ")}${b.pages.length > 3 ? "…" : ""}` : ""}`;
  return (
    <section>
      <h2 className="section-title">The business</h2>
      <p className="biz-what">{b.what}</p>
      {segs.length > 0 && (
        <div className="mix">
          <p className="mix-title">Revenue mix{b.year ? `, ${b.year}` : ""}</p>
          {segs.map((s) => (
            <div key={s.name} className="mix-row">
              <span className="mix-name">{s.name}</span>
              <span className="mix-bar"><span style={{ width: `${Math.min(100, s.share_pct!)}%` }} /></span>
              <span className="mix-pct">{s.share_pct}%</span>
            </div>
          ))}
        </div>
      )}
      {segs.length === 0 && b.segments.length > 0 && (
        <p className="snap-reading">Businesses: {b.segments.map((s) => s.name).join(", ")}.</p>
      )}
      {geos.length > 0 && <p className="snap-reading">{geos.map((g) => `${g.region} ${g.share_pct}%`).join(" · ")}</p>}
      <Footnote>From the <a href={b.url} target="_blank" rel="noreferrer">{cite}</a>.</Footnote>
    </section>
  );
}

function SnapshotView({ m, lender }: { m: Metric[]; lender: boolean }) {
  return (
    <section>
      <h2 className="section-title">The numbers</h2>
      <div className="snapshot">
        {m.map((x) => (
          <div key={x.key} className="snap-row">
            <div className="snap-top">
              <span className="snap-label">{x.label}</span>
              {x.value !== null && <span className="snap-val">{x.value}{x.unit.startsWith("%") ? "%" : x.unit.startsWith("x") ? "×" : ""}<span className="snap-unit">{x.unit.replace(/^%\s?|^x\s?/, " ")}</span></span>}
              {x.series.length > 1 && <Spark s={x.series} />}
            </div>
            <p className="snap-reading">{x.reading}</p>
          </div>
        ))}
      </div>
      <Footnote>From reported annual results (screener.in){lender ? "; read the way a lender should be read" : ""}.</Footnote>
    </section>
  );
}

function Spark({ s }: { s: { x: string; y: number }[] }) {
  const w = 64, h = 20, ys = s.map((p) => p.y);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const pts = s.map((p, i) => `${(i / (s.length - 1)) * w},${h - 2 - ((p.y - lo) / span) * (h - 4)}`).join(" ");
  const up = ys[ys.length - 1] >= ys[0];
  return (
    <svg className={`spark ${up ? "up" : "down"}`} width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-label={`${s[0].x} to ${s[s.length - 1].x}`}>
      <polyline points={pts} fill="none" />
    </svg>
  );
}

// ---- one company's promise record (push and pull share it) ----------------------

function LedgerView({ l, threadId, base }: { l: Ledger; threadId?: string; base: string }) {
  const [filter, setFilter] = useState<Filter>("all");
  const open = l.threads.find((t) => t.id === threadId);
  const r = l.record;
  const fy = Number((l.latest_results ?? "").match(/FY(\d{2})/)?.[1] ?? 0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const visible = l.threads.filter((t) =>
    filter === "all" || (filter === "moved" ? t.moved
      : filter === "live" ? LIVE.includes(t.status) && t.kind !== "soft"
      : GROUPS.find((g) => g.key === filter)!.test(t, fy)));
  const noCalls = l.calls.length === 0;
  const fromFiling = useNewerResults(l.symbol, l.headline);
  const results = fromFiling ?? l.headline;
  const short_ = l.name.replace(/ (Ltd|Limited|India)\.?$/i, "");

  return (
    <Shell back>
      <section className="co-head">
        <h1 className="co-title">{l.name}</h1>
        {l.sector && <p className="co-sub">{l.sector}</p>}
        <PriceLine symbol={l.symbol} fallback={l.price} fallbackAsof={l.price_asof} />
      </section>

      {results && results.rows.length > 0 && <ResultsBlock h={results} l={l} base={base} />}

      <section>
        <h2 className="section-title">Record</h2>
        {noCalls ? (
          <div className="notice">
            <b>No promise record.</b>
            <p>{l.name} doesn't publish earnings-call transcripts, so there are no management promises to track.</p>
          </div>
        ) : (
          <div className="record">
            <Stat n={r.resolved ? `${r.kept}/${r.resolved}` : "–"} label="kept" tone="kept" />
            <Stat n={r.missed} label="missed" tone="missed" />
            <Stat n={r.moved} label="moved" tone="moved" />
            <Stat n={r.quiet} label="gone quiet" tone="quiet" />
            <Stat n={r.open} label="open" tone="muted" />
          </div>
        )}
        {l.case && (l.case.bull.length > 0 || l.case.bear.length > 0) && <CaseView l={l} base={base} />}
        <Footnote>
          {noCalls ? "" : "Counts settled numbers and milestones only; directional remarks and ambitions are listed but not scored. "}
          {l.case ? "The case each way uses only this record and reported numbers; every point cites its source." : ""}
        </Footnote>
      </section>

      {!noCalls && (
        <section>
          <h2 className="section-title">Promises <span className="count">{l.threads.length}</span></h2>
          {l.threads.length === 0 ? (
            <div className="notice"><b>No forward-looking promises found.</b><p>Management didn't commit to numbers, directions or milestones in the calls read.</p></div>
          ) : (
            <nav className="chips">
              {(["all", "live", "settled", "moved", "quiet"] as Filter[]).map((f) => (
                <button key={f} className={`chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
                  {{ all: "All", live: "Live", settled: "Settled", moved: "Moved", quiet: "Quiet" }[f]}
                </button>
              ))}
            </nav>
          )}
          {GROUPS.map((g) => {
            const ts = visible.filter((t) => g.test(t, fy)).sort((a, b) => rank(a) - rank(b));
            if (!ts.length) return null;
            const folded = g.collapsed && !expanded.has(g.key) && filter === "all";
            return (
              <div key={g.key} className="group">
                {folded ? (
                  <button className="routine-toggle" onClick={() => setExpanded(new Set([...expanded, g.key]))}>
                    <span>{g.collapsed!(ts.length, fy)}</span>
                    <span className="routine-kinds">{ts.slice(0, 3).map((t) => t.title).join(", ")}</span>
                    <span className="chev">▾</span>
                  </button>
                ) : (
                  <>
                    <h3 className="group-title">{g.title} <span className="count">{ts.length}</span></h3>
                    <div className="cards">
                      {ts.map((t) => <ThreadCard key={t.id} t={t} onOpen={() => go(`${base}/t/${t.id}`)} />)}
                    </div>
                  </>
                )}
              </div>
            );
          })}
          <Footnote>
            From {l.calls.length} earnings calls, {l.calls[0]?.call} to {l.latest_call}. Quotes are verbatim from the BSE transcripts;
            verdicts are checked against reported results or, where marked, management's own later statements.
          </Footnote>
        </section>
      )}

      {(l.business || l.snapshot?.metrics.length) && (
        <details className="more">
          <summary>More about {short_}</summary>
          {l.business && <BusinessView b={l.business} />}
          {l.snapshot && l.snapshot.metrics.length > 0 && <SnapshotView m={l.snapshot.metrics} lender={l.snapshot.lender} />}
        </details>
      )}

      {open && <Sheet t={open} company={l.name} onClose={() => go(base)} />}
    </Shell>
  );
}

// ---- results: what the latest quarter says -------------------------------------------

/** One line from the numbers: what's going well, and where growth or margins are slipping. */
function resultsSummary(h: Headline): string {
  const row = (k: string) => h.rows.find((r) => r.label === k);
  const p = (v: number) => fmtChange(v);
  const good: string[] = [], watch: string[] = [];
  const rev = row("Revenue"), pat = row("PAT");
  if (rev?.yoy != null) {
    const d = rev.prev_yoy != null ? rev.yoy - rev.prev_yoy : 0;
    if (rev.yoy < 0) watch.push(`revenue down ${Math.abs(rev.yoy).toFixed(0)}% YoY`);
    else if (d >= 3) good.push(`revenue growth up to ${p(rev.yoy)} from ${p(rev.prev_yoy!)}`);
    else if (d <= -3) watch.push(`revenue growth slowed to ${p(rev.yoy)} from ${p(rev.prev_yoy!)}`);
    else if (rev.yoy >= 10) good.push(`revenue ${p(rev.yoy)} YoY`);
  }
  if (pat?.yoy != null) {
    if (pat.yoy < 0) watch.push(`PAT down ${Math.abs(pat.yoy).toFixed(0)}%`);
    else if (rev?.yoy != null && pat.yoy >= rev.yoy + 5) good.push(`PAT ${p(pat.yoy)}, ahead of revenue`);
    else if (pat.yoy >= 10) good.push(`PAT ${p(pat.yoy)}`);
  }
  const m = h.margin;
  if (m?.now != null && m.year_ago != null) {
    const d = m.now - m.year_ago;
    if (d <= -0.5) watch.push(`EBITDA margin down ${Math.abs(d).toFixed(1)} pts to ${m.now.toFixed(1)}%`);
    else if (d >= 0.5) good.push(`EBITDA margin up ${d.toFixed(1)} pts to ${m.now.toFixed(1)}%`);
  }
  const cap = (x: string) => x[0].toUpperCase() + x.slice(1);
  const parts = [good.length && `Going well: ${good.join(", ")}`, watch.length && `Watch: ${watch.join(", ")}`].filter(Boolean);
  return parts.length ? parts.join(". ") + "." : `${cap(resultsLine(h).split(": ")[1] ?? "")}; a steady quarter.`;
}

/** Guidance the latest numbers speak to: targets being paced, and anything moved on the last call. */
function guidance(l: Ledger): Thread[] {
  return l.threads.filter((t) => t.kind === "hard" && (
    t.pace || (t.moved && t.events[t.events.length - 1]?.call === l.latest_call)
  )).sort((a, b) => rank(a) - rank(b)).slice(0, 3);
}

function ResultsBlock({ h, l, base }: { h: Headline; l: Ledger; base: string }) {
  const chg = (v: number | null) => fmtChange(v);
  const tone = (v: number | null) => toneOf(v);
  const pts = (v: number) => fmtChange(v, 1, " pts");
  const g = guidance(l);
  return (
    <section>
      <h2 className="section-title">Results · {h.quarter}</h2>
      <p className="results-summary">{resultsSummary(h)}</p>
      <div className="headline">
        <div className="hl-row hl-head"><span /><span /><span className="hl-yoy">YoY</span><span className="hl-qoq">QoQ</span></div>
        {h.rows.map((r) => (
          <div key={r.label} className="hl-row">
            <span className="hl-label">{r.label}</span>
            <span className="hl-val">₹{Math.round(r.value).toLocaleString("en-IN")} cr</span>
            <span className={`hl-yoy ${tone(r.yoy)}`}>{chg(r.yoy)}</span>
            <span className="hl-qoq">{chg(r.qoq)}</span>
          </div>
        ))}
        {h.margin?.now != null && (
          <div className="hl-row">
            <span className="hl-label">Margin</span>
            <span className="hl-val">{h.margin.now.toFixed(1)}%</span>
            <span className={`hl-yoy ${h.margin.year_ago != null ? toneOf(h.margin.now - h.margin.year_ago, 1) : ""}`}>
              {h.margin.year_ago != null ? pts(h.margin.now - h.margin.year_ago) : "–"}
            </span>
            <span className="hl-qoq">{h.margin.prev != null ? pts(h.margin.now - h.margin.prev) : "–"}</span>
          </div>
        )}
      </div>
      {g.length > 0 && (
        <div className="guidance">
          {g.map((t) => (
            <button key={t.id} className="guide-row" onClick={() => go(`${base}/t/${t.id}`)}>
              <span className="guide-label">Guidance</span>
              <span className="guide-text">{normText(t.title)}: {t.target}{t.pace ? ` · ${paceLine(t)}` : ""}
                {t.moved && !t.pace ? ` · ${MOVE_VERB[t.events[t.events.length - 1].type].toLowerCase()} on the ${t.events[t.events.length - 1].call} call` : ""}</span>
              <span className={`pill tone-${statusOf(t).tone}`}>{statusOf(t).label}</span>
            </button>
          ))}
        </div>
      )}
      <Footnote>
        {/filing/.test(h.source) ? `Read from ${h.source}.` : "Consolidated results via screener.in."} YoY compares with the same
        quarter last year; margin is EBITDA ÷ revenue.
      </Footnote>
    </section>
  );
}

function Stat({ n, label, tone }: { n: number | string; label: string; tone: Tone }) {
  return (
    <div className="stat">
      <span className={`stat-n tone-text-${tone}`}>{n}</span>
      <span className="stat-l">{label}</span>
    </div>
  );
}

function ThreadCard({ t, onOpen }: { t: Thread; onOpen: () => void }) {
  const s = statusOf(t);
  return (
    <button className="card" onClick={onOpen}>
      <span className="card-top">
        <span className="card-title">{t.title}</span>
        <span className={`pill tone-${s.tone}`}>{s.label}</span>
      </span>
      <span className="card-target">
        {t.target ? <span className="target">{t.target}</span> : null}
        <span className="kind">{KIND_LABEL[t.kind]} · {t.period}</span>
        {t.moved ? <span className="moved-tag">moved</span> : null}
      </span>
      <span className="card-line">{oneLine(t)}</span>
      {t.moved && <Trail t={t} />}
    </button>
  );
}

function Trail({ t }: { t: Thread }) {
  return (
    <span className="trail">
      {t.statements.map((s, i) => {
        const ev = i > 0 ? t.events[i - 1] : null;
        return (
          <span key={i} className="trail-step">
            {i > 0 && <span className={`trail-arrow ev-${ev?.type}`}>{ev && ev.type !== "reaffirmed" ? MOVE_VERB[ev.type].toLowerCase() : "→"}</span>}
            <span className="trail-node">
              <span className="trail-val">{s.target || s.deadline || "said"}</span>
              <span className="trail-when">{short(s.call)}</span>
            </span>
          </span>
        );
      })}
    </span>
  );
}

// ---- detail sheet ----------------------------------------------------------------

function Sheet({ t, company, onClose }: { t: Thread; company: string; onClose: () => void }) {
  const s = statusOf(t);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", k); document.body.style.overflow = ""; };
  }, [onClose]);

  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet" role="dialog" aria-label={t.title} onClick={(e) => e.stopPropagation()}>
        <div className="grab" />
        <p className="sheet-co">{company}</p>
        <h3 className="sheet-title">{t.title}</h3>
        <div className="sheet-target">
          {t.target ? <span className="target big">{t.target}</span> : null}
          <span className="kind">{KIND_LABEL[t.kind]} · {t.period}{t.scope && t.scope !== "company" ? ` · ${t.scope}` : ""}</span>
        </div>

        <div className={`verdict tone-border-${s.tone}`}>
          <span className={`pill tone-${s.tone}`}>{s.label}</span>
          <p className="verdict-line">{verdictLine(t)}</p>
          {t.basis && <p className="footnote">{t.basis === "financials"
            ? "Checked against reported consolidated financials (screener.in)."
            : "Per management's own later statements; not independently verified."}</p>}
          {t.evidence?.quote && (
            <blockquote className="quote small">“{t.evidence.quote}”<cite>{t.evidence.call}</cite></blockquote>
          )}
        </div>

        <h4 className="sheet-h">What they said</h4>
        <ol className="said">
          {t.statements.map((st, i) => {
            const ev = i > 0 ? t.events[i - 1] : null;
            return (
              <li key={i}>
                <div className="said-head">
                  <span className="said-call">{st.call} call</span>
                  {ev && <span className={`said-ev ev-${ev.type}`}>{MOVE_VERB[ev.type]}{ev.type !== "reaffirmed" ? ` ${ev.from} → ${ev.to}` : ""}</span>}
                </div>
                <blockquote className="quote">“{st.quote}”</blockquote>
                <p className="said-meta">
                  {st.speaker} · p.{st.page}
                  {st.condition ? <> · <i>if {st.condition}</i></> : null}
                  {st.url && <> · <a href={st.url} target="_blank" rel="noreferrer">transcript ↗</a></>}
                </p>
              </li>
            );
          })}
        </ol>
        <button className="close" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

// ---- text helpers ----------------------------------------------------------------

const pct = (v: number) => fmtChange(v, 1);

function paceLine(t: Thread): string | null {
  const p = t.pace;
  if (!p) return null;
  const sofar = p.measure === "revenue growth" ? pct(p.so_far) : `${p.so_far.toFixed(1)}% ${p.measure}`;
  let line = `${p.so_far_label} at ${sofar}`;
  if (p.needs !== undefined && p.needs_label) line += ` · ${p.needs_label} needs ${pct(p.needs)} to reach ${p.needs_for}%`;
  return normText(line);
}

function oneLine(t: Thread): string {
  const pace = paceLine(t);
  if (pace) return pace;
  if (t.detail) return normText(t.detail);
  if (t.status === "open") return `Due ${t.period}. Nothing to check yet.`;
  if (t.status === "said") return "An ambition, not scored.";
  return t.statements[t.statements.length - 1].quote;
}

function verdictLine(t: Thread): string {
  const pace = paceLine(t);
  const parts = [t.detail, pace].filter(Boolean) as string[];
  if (parts.length) return parts.join(" · ");
  if (t.status === "open") return `Due ${t.period}. Results and later calls will settle it.`;
  if (t.status === "said") return "An ambition or aspiration. Shown for context; never scored.";
  return "No further information.";
}

function short(call: string): string {
  const [m, y] = call.split(" ");
  return `${m} ’${y.slice(2)}`;
}
