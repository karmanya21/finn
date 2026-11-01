// The hosted research server sleeps when idle (Render's free plan) and takes up to a minute
// to wake. Rather than a silent wait, say so once a call has gone unanswered for a moment.

import { useEffect, useState } from "react";
import { WAKE_AFTER_MS, apiAwake } from "./data";

/** "Waking up live research…" once a call has gone unanswered for a couple of seconds and
 *  the server hasn't answered anything yet: Render's free plan sleeps when idle. */
export function useWaking(pending: boolean): boolean {
  const [waking, setWaking] = useState(false);
  useEffect(() => {
    if (!pending || apiAwake()) { setWaking(false); return; }
    const id = setTimeout(() => setWaking(!apiAwake()), WAKE_AFTER_MS);
    return () => clearTimeout(id);
  }, [pending]);
  return waking && pending;
}

export function WakingNote({ what }: { what: string }) {
  return <p className="waking"><span className="spin" /> Waking up live research, the first {what} can take a minute.</p>;
}

