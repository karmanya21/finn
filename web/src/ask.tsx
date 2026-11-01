// "Ask FINN about this company": a question box, typed or spoken, answered only from the
// company's own record. Every sentence shows what it's from; tap a source to open it.

import { useEffect, useRef, useState } from "react";
import { ApiTimeout, AskAnswer, CompanySummary, askCompany, askPortfolio } from "./data";
import { loadAnyLedger } from "./mine";
import { go } from "./shell";
import { WakingNote, useWaking } from "./waking";

// The browser's speech recognition, where it has one (Chrome, Edge, Safari).
type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: (e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void;
  onend: () => void; onerror: () => void; start: () => void; stop: () => void;
};
const Speech = (window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition })
  .SpeechRecognition ?? (window as unknown as { webkitSpeechRecognition?: new () => Recognition }).webkitSpeechRecognition;

function useVoice(onText: (text: string, final: boolean) => void) {
  const [listening, setListening] = useState(false);
  const rec = useRef<Recognition | null>(null);
  useEffect(() => () => rec.current?.stop(), []);
  if (!Speech) return null;
  const toggle = () => {
    if (listening) { rec.current?.stop(); return; }
    const r = new Speech();
    r.lang = "en-IN"; r.interimResults = true; r.continuous = false;
    r.onresult = (e) => {
      const res = Array.from(e.results);
      onText(res.map((x) => x[0].transcript).join(" "), res[res.length - 1].isFinal);
    };
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    rec.current = r;
    setListening(true);
    r.start();
  };
  return { listening, toggle };
}

function speak(text: string) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-IN";
  speechSynthesis.speak(u);
}

interface Turn { q: string; answer?: AskAnswer; error?: string }

function Waiting({ reading }: { reading: string }) {
  const waking = useWaking(true);
  return waking ? <WakingNote what="question" /> : <p className="ask-wait"><span className="spin" /> Reading {reading}…</p>;
}

const said = (a?: AskAnswer) => a?.sentences.map((s) => s.text).join(" ") ?? "";

/** A conversation: messages scrolling above, suggestions and the input at the bottom. */
function ChatPanel({ ask, reading, placeholder, suggest, openSource, turns, setTurns }: {
  ask: (question: string, history: { q: string; a: string }[]) => Promise<AskAnswer>;
  reading: string;            // "Bharat Electronics's record", "your 18 companies' records"
  placeholder: string;
  suggest: string[];
  openSource: (s: AskAnswer["sources"][string]) => void;
  turns: Turn[];
  setTurns: (f: (ts: Turn[]) => Turn[]) => void;
}) {
  const [q, setQ] = useState("");
  const busy = turns.length > 0 && !turns[turns.length - 1].answer && !turns[turns.length - 1].error;
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns]);
  useEffect(() => { input.current?.focus({ preventScroll: true }); }, []);

  const submit = async (text: string) => {
    const question = text.trim();
    if (question.length < 3 || busy) return;
    // the last two answered exchanges go along, so a follow-up ("why?") has something to refer to
    const history = turns.filter((t) => t.answer).slice(-2).map((t) => ({ q: t.q, a: said(t.answer) }));
    setTurns((ts) => [...ts.slice(-9), { q: question }]);
    setQ("");
    const settle = (patch: Partial<Turn>) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, ...patch } : t)));
    try {
      settle({ answer: await ask(question, history) });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      settle({ error: e instanceof ApiTimeout ? "Live research didn't wake up in time. Try again in a minute."
        : /^(404|50[0234])$/.test(msg) || /fetch/i.test(msg) ? "Ask needs FINN's research server, and it isn't reachable right now." : msg });
    }
  };
  const voice = useVoice((text, final) => { setQ(text); if (final) submit(text); });

  return (
    <>
      <div className="chat-thread" aria-live="polite">
        {turns.length === 0 && <p className="chat-hello">Ask about promises, results, filings or prices. Answers come only from the record, and every sentence names its source.</p>}
        {turns.map((t, k) => (
          <div key={k} className="chat-turn">
            <p className="chat-q">{t.q}</p>
            <div className="chat-a">
              {!t.answer && !t.error && <Waiting reading={reading} />}
              {t.error && <p className="ask-error">{t.error}</p>}
              {t.answer && (
                <ul className="ask-sentences">
                  {t.answer.sentences.map((s, i) => (
                    <li key={i}>
                      {s.text}
                      {s.sources.length > 0 && (
                        <span className="refs">
                          {s.sources.map((id) => {
                            const src = t.answer!.sources[id];
                            const linked = src?.ref && (src.kind === "promise" || src.kind === "filing" || src.kind === "company");
                            return linked
                              ? <button key={id} className="ref" onClick={() => openSource(src)}>{src.label} ›</button>
                              : <span key={id} className="ref">{src?.label ?? id}</span>;
                          })}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {t.answer && "speechSynthesis" in window && (
                <button className="link small chat-speak" onClick={() => speak(said(t.answer))}>Read aloud</button>
              )}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="chat-foot">
        {turns.length === 0 && (
          <div className="ask-sugg row">
            {suggest.map((s) => <button key={s} className="chip" onClick={() => submit(s)}>{s}</button>)}
          </div>
        )}
        <form className="ask-form" onSubmit={(e) => { e.preventDefault(); submit(q); }}>
          <input ref={input} className="ask-input" value={q} onChange={(e) => setQ(e.target.value)} maxLength={300}
            placeholder={voice?.listening ? "Listening…" : turns.length ? "Ask a follow-up" : placeholder} aria-label={placeholder} />
          {voice && (
            <button type="button" className={`ask-btn mic ${voice.listening ? "on" : ""}`} onClick={voice.toggle}
              aria-label={voice.listening ? "Stop listening" : "Ask by voice"} aria-pressed={voice.listening}>
              <svg viewBox="0 0 24 24" aria-hidden><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
            </button>
          )}
          <button type="submit" className="ask-btn go" disabled={busy || q.trim().length < 3} aria-label="Ask">
            <svg viewBox="0 0 24 24" aria-hidden><path d="M5 12h14M13 6l6 6-6 6" /></svg>
          </button>
        </form>
        {turns.length > 0 && <button className="link small chat-clear" onClick={() => setTurns(() => [])}>Clear conversation</button>}
      </div>
    </>
  );
}

const short = (name: string) => name.replace(/ (Ltd|Limited|India)\.?$/i, "");

/** Ask FINN, as a bubble in the corner of every screen. On a company's page it answers about
 *  that company (with a switch to the whole portfolio); elsewhere, about the portfolio.
 *  Conversations are kept per subject while the reader moves around the app. */
export function ChatBubble({ index, route }: { index: CompanySummary[]; route: string[] }) {
  const [open, setOpen] = useState(false);
  const [wide, setWide] = useState(false);  // on a company page: the whole portfolio instead
  const [threads, setThreads] = useState<{ [subject: string]: Turn[] }>({});
  const sym = (route[0] === "c" || route[0] === "r") && route[1] ? decodeURIComponent(route[1]).toUpperCase() : null;
  const [company, setCompany] = useState<{ symbol: string; name: string; fy: string | null } | null>(null);
  useEffect(() => {
    setWide(false);
    if (!sym) { setCompany(null); return; }
    const known = index.find((c) => c.symbol === sym);
    const fyOf = (r: string | null | undefined) => (r ?? "").match(/FY(\d{2})/)?.[1] ?? null;
    if (known) { setCompany({ symbol: sym, name: known.name, fy: fyOf(known.latest_results) }); return; }
    let alive = true;  // a researched company outside the portfolio: only once its record exists
    loadAnyLedger(sym).then((l) => { if (alive) setCompany(l ? { symbol: sym, name: l.name, fy: fyOf(l.latest_results) } : null); });
    return () => { alive = false; };
  }, [sym, index]);
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open]);

  const one = company && !wide ? company : null;
  const subject = one ? one.symbol : "portfolio";
  const turns = threads[subject] ?? [];
  const setTurns = (f: (ts: Turn[]) => Turn[]) => setThreads((all) => ({ ...all, [subject]: f(all[subject] ?? []) }));
  const symbols = index.map((c) => c.symbol);
  const tabs = !route[0] || route[0] === "filings" || route[0] === "portfolio";
  const base = route[0] === "r" ? "/r" : "/c";
  const goSource = (s: AskAnswer["sources"][string]) => {
    if (!s.ref) return;
    setOpen(false);
    if (s.kind === "company") go(`/c/${s.ref}`);
    else if (s.kind === "promise") go(`${one ? `${base}/${one.symbol}` : `/c/${s.ref.replace(/-\d+$/, "")}`}/t/${s.ref}`);
    else if (s.kind === "filing") go(`/filings/${s.ref}`);
  };

  if (!open) return (
    <button className={`chat-fab ${tabs ? "over-tabs" : ""}`} onClick={() => setOpen(true)} aria-label="Ask FINN">
      <svg viewBox="0 0 24 24" aria-hidden><path d="M4 5h16v11H9l-5 4z" /><path d="M8 10h8M8 13h5" /></svg>
      {turns.some((t) => t.answer) && <span className="chat-dot" />}
    </button>
  );
  return (
    <div className="chat-scrim" onClick={() => setOpen(false)}>
      <div className="chat-panel" role="dialog" aria-label="Ask FINN" onClick={(e) => e.stopPropagation()}>
        <div className="chat-head">
          <b>Ask FINN</b>
          {company ? (
            <div className="segmented small chat-scope" role="group" aria-label="What to ask about">
              <button className={!wide ? "on" : ""} onClick={() => setWide(false)}>{short(company.name)}</button>
              <button className={wide ? "on" : ""} onClick={() => setWide(true)}>My portfolio</button>
            </div>
          ) : <span className="chat-sub">Your portfolio · {symbols.length} companies</span>}
          <button className="chat-close" onClick={() => setOpen(false)} aria-label="Close">✕</button>
        </div>
        <ChatPanel key={subject}
          ask={one ? (q, h) => askCompany(one.symbol, q, h) : (q, h) => askPortfolio(q, symbols, h)}
          reading={one ? `${short(one.name)}'s record` : `your ${symbols.length} companies' records`}
          placeholder={one ? `Ask about ${short(one.name)}` : "Ask about your portfolio"}
          suggest={one
            ? [one.fy ? `What did they guide for FY${one.fy}?` : "What have they guided?", "Which promises slipped?", "How were the latest results?"]
            : ["Which companies missed or cut guidance?", "Which stocks fell most this month?", "Who is behind on FY27 guidance?"]}
          openSource={goSource} turns={turns} setTurns={setTurns} />
      </div>
    </div>
  );
}
