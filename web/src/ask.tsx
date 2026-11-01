// "Ask FINN about this company": a question box, typed or spoken, answered only from the
// company's own record. Every sentence shows what it's from; tap a source to open it.

import { useEffect, useRef, useState } from "react";
import { AskAnswer, Ledger, askCompany } from "./data";
import { go } from "./shell";

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

/** Questions this record can answer, to show what kind of thing to ask. */
function suggestions(l: Ledger): string[] {
  const fy = (l.latest_results ?? "").match(/FY(\d{2})/)?.[1];
  const out = [fy ? `What did they guide for FY${fy}?` : "What have they guided?", "Which promises slipped?"];
  out.push(l.headline ? "How did the latest results compare with guidance?" : "What does the company do?");
  return out;
}

export function AskBox({ l, base }: { l: Ledger; base: string }) {
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const short = l.name.replace(/ (Ltd|Limited|India)\.?$/i, "");

  const submit = async (text: string) => {
    const question = text.trim();
    if (question.length < 3 || busy) return;
    setBusy(true); setError(null); setAsked(question); setAnswer(null);
    try {
      setAnswer(await askCompany(l.symbol, question));
      setQ("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(/^(404|50[02]|Failed to fetch)/.test(msg) || /fetch/i.test(msg)
        ? "Ask needs FINN's research server, and it isn't running right now." : msg);
    } finally {
      setBusy(false);
    }
  };
  const voice = useVoice((text, final) => { setQ(text); if (final) submit(text); });
  const said = answer?.sentences.map((s) => s.text).join(" ") ?? "";

  const openSource = (id: string) => {
    const s = answer?.sources[id];
    if (s?.kind === "promise" && s.ref) go(`${base}/t/${s.ref}`);
    else if (s?.kind === "filing" && s.ref) go(`/filings/${s.ref}`);
  };

  return (
    <section className="ask">
      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); submit(q); }}>
        <input className="ask-input" value={q} onChange={(e) => setQ(e.target.value)} maxLength={300}
          placeholder={voice?.listening ? "Listening…" : `Ask FINN about ${short}`} aria-label={`Ask FINN about ${short}`} />
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

      {!asked && (
        <div className="ask-sugg">
          {suggestions(l).map((s) => <button key={s} className="chip" onClick={() => submit(s)}>{s}</button>)}
        </div>
      )}

      {asked && (
        <div className="ask-answer" aria-live="polite">
          <p className="ask-q">{asked}</p>
          {busy && <p className="ask-wait"><span className="spin" /> Reading {short}'s record…</p>}
          {error && <p className="ask-error">{error}</p>}
          {answer && (
            <>
              <ul className="ask-sentences">
                {answer.sentences.map((s, i) => (
                  <li key={i}>
                    {s.text}
                    {s.sources.length > 0 && (
                      <span className="refs">
                        {s.sources.map((id) => {
                          const src = answer.sources[id];
                          const linked = src?.ref && (src.kind === "promise" || src.kind === "filing");
                          return linked
                            ? <button key={id} className="ref" onClick={() => openSource(id)}>{src.label} ›</button>
                            : <span key={id} className="ref">{src?.label ?? id}</span>;
                        })}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              <div className="ask-actions">
                {"speechSynthesis" in window && <button className="link small" onClick={() => speak(said)}>Read aloud</button>}
                <button className="link small" onClick={() => { setAsked(""); setAnswer(null); }}>Ask something else</button>
              </div>
            </>
          )}
        </div>
      )}
      <p className="footnote">
        Answers only from {short}'s record: its calls, results, filings and prices. Every sentence names its source,
        and any sentence with a number its source doesn't contain is dropped. No buy or sell advice.
      </p>
    </section>
  );
}
