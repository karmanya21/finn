import { useEffect, useMemo, useRef, useState } from "react";
import { Alerts, FilingsView, SettingsSheet } from "./push";
import { Shell, go } from "./shell";
import {
  CompanySummary, FeedItem, Headline, KIND_LABEL, Ledger, MOVE_VERB, Metric, ResearchStatus, SearchResult, Thread, Tone,
  feed, loadIndex, loadLedger, researchStatus, searchCompanies, startResearch, statusOf,
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
    return <ResearchView symbol={sym} threadId={route[3]} portfolio={index.some((c) => c.symbol === sym)} />;
  }
  if (route[0] === "filings") return <FilingsView index={index} />;
  if (route[0] === "portfolio") return <Portfolio index={index} />;
  return <Home index={index} />;
}

// ---- today (home) ------------------------------------------------------------------

function Home({ index }: { index: CompanySummary[] }) {
  const [ledgers, setLedgers] = useState<Ledger[]>([]);
  useEffect(() => { Promise.all(index.map((c) => loadLedger(c.symbol))).then(setLedgers); }, [index]);
  const items = useMemo(() => feed(ledgers), [ledgers]);
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, 6);
  const seen = useSeen(items);
  const fresh = items.filter((i) => !seen.has(feedKey(i))).length;
  const [settings, setSettings] = useState(false);

  return (
    <Shell tab="today" action={<button className="icon-btn" onClick={() => setSettings(true)} aria-label="Alert settings">
      <svg viewBox="0 0 24 24" aria-hidden><path d="M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6" /></svg></button>}>
      <SearchBar />
      <Alerts index={index} onSettings={() => setSettings(true)} />
      <section>
        <h2 className="section-title">What moved</h2>
        <p className="section-sub">
          {seen.size === 0 ? "From the latest earnings calls and results, most important first."
            : fresh ? `${fresh} new since you last looked.` : "Nothing new since you last looked."}
        </p>
        <div className="feed">
          {shown.map((i) => <FeedRow key={i.thread.id} item={i} fresh={seen.size > 0 && !seen.has(feedKey(i))} />)}
        </div>
        {items.length > 6 && (
          <button className="more" onClick={() => setAll(!all)}>
            {all ? "Show less" : `${items.length - 6} more`}
          </button>
        )}
      </section>

      {settings && <SettingsSheet onClose={() => setSettings(false)} />}
    </Shell>
  );
}

function Portfolio({ index }: { index: CompanySummary[] }) {
  return (
    <Shell tab="portfolio">
      <SearchBar />
      <section>
        <h2 className="section-title">Track records</h2>
        <p className="section-sub">{index.length} companies. Kept vs missed counts settled numbers and milestones only.</p>
        <div className="companies">
          {index.map((c) => <CompanyRow key={c.symbol} c={c} />)}
        </div>
      </section>
    </Shell>
  );
}

// "Since you last looked": remembered per device in localStorage. The set returned is
// what had been seen *before* this visit; everything shown now is marked seen for next time.
const SEEN_KEY = "finn.seen.v1";
const feedKey = (i: FeedItem) => `${i.thread.id}|${i.headline}`;

function useSeen(items: FeedItem[]): Set<string> {
  const [before] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) || "[]")); }
    catch { return new Set(); }
  });
  useEffect(() => {
    if (!items.length) return;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify([...new Set([...before, ...items.map(feedKey)])])); }
    catch { /* private mode: every visit is a first visit */ }
  }, [items, before]);
  return before;
}

function FeedRow({ item, fresh }: { item: FeedItem; fresh: boolean }) {
  const t = item.thread;
  return (
    <button className="feed-row" onClick={() => go(`/c/${item.symbol}/t/${t.id}`)}>
      <span className={`dot tone-${item.tone}`} />
      <span className="feed-body">
        <span className="feed-co">{item.name}{fresh && <span className="new">new</span>}</span>
        <span className="feed-head">{item.headline}</span>
        <span className="feed-line">{item.line ?? oneLine(t)}</span>
      </span>
    </button>
  );
}

function CompanyRow({ c }: { c: CompanySummary }) {
  const r = c.record;
  return (
    <button className="company-row" onClick={() => go(`/c/${c.symbol}`)}>
      <span className="co-main">
        <span className="co-name">{c.name}</span>
        <span className="co-meta">{c.sector} · last call {c.latest_call}</span>
      </span>
      <span className="co-record">
        <RecordBar r={r} />
        <span className="co-record-text">
          {r.resolved ? <><b>{r.kept}</b> of {r.resolved} kept</> : "Nothing settled yet"}
          {r.moved ? <> · {r.moved} moved</> : null}
          {r.quiet ? <> · {r.quiet} quiet</> : null}
        </span>
      </span>
    </button>
  );
}

function RecordBar({ r }: { r: CompanySummary["record"] }) {
  const parts: [Tone, number][] = [["kept", r.kept], ["missed", r.missed], ["quiet", r.quiet], ["muted", r.open]];
  const total = parts.reduce((s, [, n]) => s + n, 0) || 1;
  return (
    <span className="bar" aria-hidden>
      {parts.map(([tone, n]) => n ? <span key={tone} className={`seg tone-${tone}`} style={{ flex: n / total }} /> : null)}
    </span>
  );
}

// ---- company -------------------------------------------------------------------

type Filter = "all" | "live" | "settled" | "moved" | "quiet";

const GROUPS: { key: string; title: string; test: (t: Thread) => boolean }[] = [
  { key: "live", title: "Live", test: (t) => ["tracking", "on_track", "behind", "delayed", "open", "cant_verify"].includes(t.status) && t.kind !== "soft" },
  { key: "settled", title: "Settled", test: (t) => ["delivered", "beat", "missed"].includes(t.status) },
  { key: "quiet", title: "Gone quiet", test: (t) => t.status === "gone_quiet" },
  { key: "said", title: "Also said", test: (t) => t.status === "said" || t.status === "lapsed" },
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

function ResearchView({ symbol, threadId, portfolio }: { symbol: string; threadId?: string; portfolio: boolean }) {
  const [s, setS] = useState<ResearchStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (portfolio) { go(`/c/${symbol}`); return; }
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
  }, [symbol, portfolio]);

  if (s?.state === "done" && s.ledger) return <LedgerView l={s.ledger} threadId={threadId} base={`/r/${symbol}`} />;
  const failed = err || (s?.state === "error" ? s.error : null);
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

function SearchBar() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    setBusy(true);
    const id = setTimeout(() => {
      searchCompanies(q.trim()).then(setResults).catch(() => setResults([])).finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(id);
  }, [q]);

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
          {!busy && !results.length && <p className="sr-empty">No listed company matches “{q.trim()}”.</p>}
        </div>
      )}
    </div>
  );
}

function HeadlineStrip({ h }: { h: Headline }) {
  const chg = (v: number | null) => v === null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(0)}%`;
  const tone = (v: number | null) => v === null ? "" : v >= 0 ? "up" : "down";
  return (
    <div className="headline">
      <div className="hl-head">
        <span>{h.quarter} results</span>
        <span className="hl-cols"><span>QoQ</span><span>YoY</span></span>
      </div>
      {h.rows.map((r) => (
        <div key={r.label} className="hl-row">
          <span className="hl-label">{r.label}</span>
          <span className="hl-val">₹{Math.round(r.value).toLocaleString("en-IN")} cr</span>
          <span className="hl-cols">
            <span className={tone(r.qoq)}>{chg(r.qoq)}</span>
            <span className={tone(r.yoy)}>{chg(r.yoy)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

// ---- snapshot and the case each way ------------------------------------------------

function CaseView({ l, base }: { l: Ledger; base: string }) {
  const threads = new Map(l.threads.map((t) => [t.id, t]));
  const metric = new Map((l.snapshot?.metrics ?? []).map((m) => [m.key, m.label]));
  const side = (title: string, pts: { point: string; refs: string[] }[], tone: string) => (
    <div className={`case-side case-${tone}`}>
      <h3>{title}</h3>
      <ul>
        {pts.map((p, i) => (
          <li key={i}>
            {p.point}
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
    <section>
      <h2 className="section-title">The case each way</h2>
      <p className="section-sub">Built only from this promise record and the reported numbers below; every point cites its source.</p>
      <div className="case">
        {l.case!.bull.length > 0 && side("Bull", l.case!.bull, "bull")}
        {l.case!.bear.length > 0 && side("Bear", l.case!.bear, "bear")}
      </div>
    </section>
  );
}

function SnapshotView({ m, lender }: { m: Metric[]; lender: boolean }) {
  return (
    <section>
      <h2 className="section-title">The numbers</h2>
      <p className="section-sub">From reported annual results (screener.in){lender ? "; shown the way a lender should be read" : ""}.</p>
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
  const visible = l.threads.filter((t) =>
    filter === "all" || (filter === "moved" ? t.moved : GROUPS.find((g) => g.key === filter)!.test(t)));
  const noCalls = l.calls.length === 0;

  return (
    <Shell back>
      <section className="co-head">
        <h1 className="co-title">{l.name}</h1>
        <p className="co-sub">
          {l.sector ? `${l.sector} · ` : ""}
          {noCalls ? "no earnings calls on record" : `${l.calls.length} calls read, ${l.calls[0].call} to ${l.latest_call}`}
          {l.latest_results ? ` · results through ${l.latest_results}` : ""}
        </p>
        {l.headline && l.headline.rows.length > 0 && <HeadlineStrip h={l.headline} />}
        {noCalls ? (
          <div className="notice">
            <b>No promise record.</b>
            <p>{l.name} doesn't publish earnings-call transcripts, so there are no management promises to track. The numbers above are from its reported results.</p>
          </div>
        ) : (
          <>
            <div className="record">
              <Stat n={r.resolved ? `${r.kept}/${r.resolved}` : "–"} label="kept" tone="kept" />
              <Stat n={r.missed} label="missed" tone="missed" />
              <Stat n={r.moved} label="moved" tone="moved" />
              <Stat n={r.quiet} label="gone quiet" tone="quiet" />
              <Stat n={r.open} label="open" tone="muted" />
            </div>
            <p className="record-note">Numbers and milestones only. Directional remarks and ambitions are listed but not scored.</p>
          </>
        )}
      </section>

      {l.case && (l.case.bull.length > 0 || l.case.bear.length > 0) && <CaseView l={l} base={base} />}
      {l.snapshot && l.snapshot.metrics.length > 0 && <SnapshotView m={l.snapshot.metrics} lender={l.snapshot.lender} />}

      {!noCalls && l.threads.length === 0 && (
        <div className="notice"><b>No forward-looking promises found.</b><p>Management didn't commit to numbers, directions or milestones in the calls read.</p></div>
      )}

      {l.threads.length > 0 && <nav className="chips">
        {(["all", "live", "settled", "moved", "quiet"] as Filter[]).map((f) => (
          <button key={f} className={`chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
            {{ all: "All", live: "Live", settled: "Settled", moved: "Moved", quiet: "Quiet" }[f]}
          </button>
        ))}
      </nav>}

      {GROUPS.map((g) => {
        const ts = visible.filter(g.test).sort((a, b) => rank(a) - rank(b));
        if (!ts.length) return null;
        return (
          <section key={g.key}>
            <h2 className="section-title">{g.title} <span className="count">{ts.length}</span></h2>
            <div className="cards">
              {ts.map((t) => <ThreadCard key={t.id} t={t} onOpen={() => go(`${base}/t/${t.id}`)} />)}
            </div>
          </section>
        );
      })}

      {open && <Sheet t={open} company={l.name} onClose={() => go(base)} />}
    </Shell>
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
      {t.statements.length > 1 && <Trail t={t} />}
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
          {t.basis && <p className="basis">{t.basis === "financials"
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

const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

function paceLine(t: Thread): string | null {
  const p = t.pace;
  if (!p) return null;
  const sofar = p.measure === "revenue growth" ? pct(p.so_far) : `${p.so_far.toFixed(1)}% ${p.measure}`;
  let line = `${p.so_far_label} at ${sofar}`;
  if (p.needs !== undefined && p.needs_label) line += ` · ${p.needs_label} needs ${pct(p.needs)} to reach ${p.needs_for}%`;
  return line;
}

function oneLine(t: Thread): string {
  const pace = paceLine(t);
  if (pace) return pace;
  if (t.detail) return t.detail;
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
