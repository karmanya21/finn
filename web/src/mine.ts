// The reader's own additions to the portfolio. The 18 built-in companies ship with the app;
// companies a reader adds live in their browser, with the record itself for any company that
// was researched live (the server forgets live research when it restarts).

import { useEffect, useState } from "react";
import { CompanySummary, Ledger, loadShipped, researchStatus } from "./data";

const KEY = "finn.portfolio.added.v1";
const LEDGER = (sym: string) => `finn.ledger.v1.${sym}`;
const EVENT = "finn:portfolio";
export const MAX_ADDED = 15;

let builtIn = new Set<string>();
export const setBuiltIn = (symbols: string[]) => { builtIn = new Set(symbols); };
export const isBuiltIn = (sym: string) => builtIn.has(sym);

function read(): CompanySummary[] {
  try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; }
}

function write(list: CompanySummary[]) {
  try { localStorage.setItem(KEY, JSON.stringify(list)); } catch { /* private mode: lasts this visit only */ }
  window.dispatchEvent(new Event(EVENT));
}

export const summaryOf = (l: Ledger): CompanySummary => ({
  symbol: l.symbol, name: l.name, sector: l.sector, size: l.size, latest_call: l.latest_call,
  latest_results: l.latest_results, record: l.record, added: true,
});

/** Add a company; keep its record too unless it ships with the app. False when full. */
export async function addToPortfolio(l: Ledger): Promise<boolean> {
  const list = read().filter((c) => c.symbol !== l.symbol);
  if (list.length >= MAX_ADDED) return false;
  if (!(await loadShipped(l.symbol))) {
    try { localStorage.setItem(LEDGER(l.symbol), JSON.stringify(l)); } catch { /* too big or private mode */ }
  }
  write([...list, summaryOf(l)]);
  return true;
}

export function removeFromPortfolio(sym: string) {
  try { localStorage.removeItem(LEDGER(sym)); } catch { /* nothing kept */ }
  write(read().filter((c) => c.symbol !== sym));
}

/** The reader's added companies, kept current across the app. */
export function useAdded(): CompanySummary[] {
  const [list, setList] = useState(read);
  useEffect(() => {
    const on = () => setList(read());
    window.addEventListener(EVENT, on);
    window.addEventListener("storage", on);  // another tab
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener("storage", on); };
  }, []);
  return list;
}

/** A company's record from wherever it is: shipped with the app, kept in this browser, or
 *  on the research server. */
export async function loadAnyLedger(sym: string): Promise<Ledger | null> {
  const shipped = await loadShipped(sym);
  if (shipped) return shipped;
  try {
    const kept = localStorage.getItem(LEDGER(sym));
    if (kept) return JSON.parse(kept);
  } catch { /* fall through to the server */ }
  try {
    const st = await researchStatus(sym);
    return st.state === "done" && st.ledger ? st.ledger : null;
  } catch { return null; }
}
