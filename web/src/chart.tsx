// The company page's price chart: closes over a chosen range, volume beneath on its own
// axis, and dots where results came out. Scrub (pointer, touch or arrow keys) to read a day.

import { useEffect, useMemo, useRef, useState } from "react";
import { Digest, loadDigest } from "./data";
import { fmtChange, fmtDate } from "./format";
import { resultsLine } from "./push";

type Row = [string, number, number?];  // date, close, volume (absent for research companies)
interface Event { date: string; text: string }

const RANGES: [string, number][] = [["1M", 21], ["3M", 63], ["6M", 126], ["1Y", 260]];
const H = 150, VH = 36, GAP = 6, PAD = { top: 10, right: 46, bottom: 18 };

/** ₹ volumes the Indian way: 1.2 Cr, 45.3 L. */
function fmtVolume(v: number): string {
  if (v >= 1e7) return `${(v / 1e7).toFixed(1)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)} L`;
  return v.toLocaleString("en-IN");
}

const rupees = (v: number) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: v < 1000 ? 2 : 0 })}`;

/** Three or four round price levels across [lo, hi]. */
function ticks(lo: number, hi: number): number[] {
  const raw = (hi - lo) / 4, mag = 10 ** Math.floor(Math.log10(raw || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(320);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Results filings for this company, as dated events on the chart. */
function useResultEvents(symbol: string): Event[] {
  const [d, setD] = useState<Digest | null>(null);
  useEffect(() => { loadDigest().then(setD).catch(() => setD(null)); }, []);
  return useMemo(() => (d?.items ?? [])
    .filter((f) => f.symbol === symbol && f.type === "results")
    .map((f) => ({ date: f.at.slice(0, 10), text: f.numbers ? resultsLine(f.numbers) : "Results filed" })), [d, symbol]);
}

export function PriceChart({ symbol, rows }: { symbol: string; rows: Row[] }) {
  // a range is offered only when it shows clearly more than the one before it
  const usable = RANGES.filter((_, i) => i === 0 || rows.length >= RANGES[i - 1][1] * 1.3);
  // The default follows the data until the reader picks a range: the record's 3-month
  // fallback can draw first, before the year of prices has loaded.
  const [picked, setRange] = useState<string | null>(null);
  const range = picked && usable.some(([k]) => k === picked) ? picked
    : (usable.find(([k]) => k === "6M") ?? usable[usable.length - 1])[0];
  const [hover, setHover] = useState<number | null>(null);
  const [ref, width] = useWidth();
  const events = useResultEvents(symbol);

  const n = RANGES.find(([k]) => k === range)![1];
  const data = rows.slice(-n);
  const hasVol = data.every((r) => r[2] != null);
  if (data.length < 2) return null;

  const closes = data.map((r) => r[1]);
  const lo = Math.min(...closes), hi = Math.max(...closes), padY = (hi - lo) * 0.08 || hi * 0.02;
  const y0 = lo - padY, y1 = hi + padY;
  const plotW = width - PAD.right;
  const x = (i: number) => (i / (data.length - 1)) * plotW;
  const y = (v: number) => PAD.top + (1 - (v - y0) / (y1 - y0)) * (H - PAD.top);
  const line = data.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r[1]).toFixed(1)}`).join("");
  const area = `${line}L${x(data.length - 1).toFixed(1)},${H}L0,${H}Z`;
  const first = closes[0], last = closes[closes.length - 1];
  const tone = last >= first ? "up" : "down";

  // month boundaries for the x axis (dates for a 1-month view), thinned to fit
  const labels: { i: number; text: string }[] = [];
  data.forEach((r, i) => {
    const d = r[0], prev = data[i - 1]?.[0];
    if (range === "1M" ? i % 5 === 0 : prev && d.slice(5, 7) !== prev.slice(5, 7))
      labels.push({ i, text: range === "1M" ? fmtDate(d).replace(/ \d{4}$/, "") : new Date(d).toLocaleString("en-US", { month: "short" }) });
  });
  const minGap = 34;
  const shown: typeof labels = [];
  labels.forEach((l) => { if (!shown.length || x(l.i) - x(shown[shown.length - 1].i) >= minGap) shown.push(l); });

  const eventAt = new Map<number, Event>();
  events.forEach((e) => {
    const i = data.findIndex((r) => r[0] >= e.date);  // results after the close land on the next session
    if (i >= 0 && e.date >= data[0][0]) eventAt.set(i, e);
  });

  const vmax = hasVol ? Math.max(...data.map((r) => r[2]!)) : 1;
  const step = plotW / data.length;
  const at = hover ?? data.length - 1;
  const row = data[at];
  const ev = eventAt.get(at);

  const pick = (clientX: number) => {
    const box = ref.current!.getBoundingClientRect();
    const i = Math.round(((clientX - box.left) / plotW) * (data.length - 1));
    setHover(Math.max(0, Math.min(data.length - 1, i)));
  };
  const key = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      setHover(Math.max(0, Math.min(data.length - 1, (hover ?? data.length - 1) + (e.key === "ArrowLeft" ? -1 : 1))));
    } else if (e.key === "Escape") setHover(null);
  };

  return (
    <div className="pchart">
      <div className="pchart-top">
        <div className="pchart-read" aria-live="polite">
          {hover != null && <b>{rupees(row[1])}</b>}
          <span className={row[1] >= first ? "up" : "down"}>{fmtChange((row[1] / first - 1) * 100, 1)}</span>
          <span className="pchart-when">{hover == null ? `over ${range}` : fmtDate(row[0])}</span>
          {hover != null && hasVol && <span className="pchart-when">· vol {fmtVolume(row[2]!)}</span>}
        </div>
        <div className="segmented small" role="group" aria-label="Chart range">
          {usable.map(([k]) => (
            <button key={k} className={k === range ? "on" : ""} onClick={() => { setRange(k); setHover(null); }}>{k}</button>
          ))}
        </div>
      </div>
      {ev && <p className="pchart-event">{fmtDate(ev.date)}: {ev.text}</p>}

      <div ref={ref} className="pchart-plot" tabIndex={0} onKeyDown={key}
        onPointerMove={(e) => pick(e.clientX)} onPointerDown={(e) => pick(e.clientX)} onPointerLeave={() => setHover(null)}
        aria-label={`${range} price chart: from ${rupees(first)} to ${rupees(last)}, high ${rupees(hi)}, low ${rupees(lo)}. Use arrow keys to read each day.`}>
        <svg width={width} height={H + PAD.bottom + (hasVol ? GAP + VH : 0)} className={`pchart-svg ${tone}`}>
          <defs>
            <linearGradient id={`pfill-${symbol}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" className="pchart-fill-top" />
              <stop offset="1" className="pchart-fill-bottom" />
            </linearGradient>
          </defs>
          {ticks(y0, y1).map((v) => (
            <g key={v}>
              <line className="pchart-grid" x1={0} x2={plotW} y1={y(v)} y2={y(v)} />
              <text className="pchart-axis" x={plotW + 6} y={y(v) + 4}>{v >= 1000 ? v.toLocaleString("en-IN") : v}</text>
            </g>
          ))}
          <path d={area} fill={`url(#pfill-${symbol})`} />
          <path d={line} className="pchart-line" />
          {[...eventAt.keys()].map((i) => (
            <circle key={i} className="pchart-event-dot" cx={x(i)} cy={y(data[i][1])} r={4} />
          ))}
          {hover != null && (
            <>
              <line className="pchart-cross" x1={x(at)} x2={x(at)} y1={PAD.top} y2={H + (hasVol ? GAP + VH : 0)} />
              <circle className="pchart-dot" cx={x(at)} cy={y(row[1])} r={4} />
            </>
          )}
          {shown.map((l) => (
            <text key={l.i} className="pchart-axis" x={x(l.i)} y={H + 13} textAnchor={l.i === 0 ? "start" : "middle"}>{l.text}</text>
          ))}
          {hasVol && data.map((r, i) => {
            const h = Math.max(1, (r[2]! / vmax) * VH);
            return <rect key={r[0]} className={`pchart-vol ${i === at && hover != null ? "on" : ""}`}
              x={i * step} y={H + PAD.bottom + GAP + VH - h} width={Math.max(1, step - 1)} height={h} />;
          })}
        </svg>
      </div>
      <p className="pchart-foot">
        {range} high {rupees(hi)} · low {rupees(lo)}
        {hasVol ? " · bars: daily volume" : ""}{eventAt.size ? " · dots: results" : ""}
      </p>
    </div>
  );
}
