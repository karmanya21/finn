// One way to write dates, changes, periods and filing headlines across the app.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "30 Oct" (this year) or "30 Oct 2025". Accepts "2026-10-30" or a full ISO timestamp. */
export function fmtDate(s: string): string {
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return s;
  return `${d} ${MONTHS[m - 1]}${y === new Date().getFullYear() ? "" : ` ${y}`}`;
}

/** A signed change, "flat" when it rounds to zero at the given precision. */
export function fmtChange(v: number | null | undefined, digits = 0, unit = "%"): string {
  if (v == null || Number.isNaN(v)) return "–";
  const r = Number(v.toFixed(digits));
  if (r === 0) return "flat";
  return `${r > 0 ? "+" : "−"}${Math.abs(r).toFixed(digits)}${unit}`;
}

export const tone = (v: number | null | undefined, digits = 0) =>
  v == null || Number(v.toFixed(digits)) === 0 ? "" : v > 0 ? "up" : "down";

/** Model-written text in the app's house style: "Q2 FY'27" -> "Q2FY27" (and H1/H2),
 *  "INR 4,358 crore" / "Rs. 100 crore" -> "₹4,358 cr". */
export function normText(text: string): string {
  return text
    .replace(/\b([QH])\s?([1-4])\s?[-\s]?FY\s?['’]?\s?(\d{2})\b/gi,
      (_, k: string, n: string, y: string) => `${k.toUpperCase()}${n}FY${y}`)
    .replace(/(?:\bINR|\bRs\.?)\s?(?=\d)/g, "₹")
    .replace(/(₹\s?[\d,.]+)\s?(?:crores?|cr\.?)(?![a-z])/gi, "$1 cr");
}

const BOILERPLATE = /^(please find|pursuant to|we wish to|this is to inform|in compliance|in terms of|enclosed|attached|kindly find|with reference to|further to|the (company|board) (has )?(informed|submitted))|disclosure under regulation|^(announcement|intimation) under/i;

/** A filing's headline: its summary, unless that's boilerplate; then its subject. */
export function filingText(summary: string | null | undefined, subject: string): string {
  const s = (summary ?? "").trim();
  if (!s || s.length < 25 || BOILERPLATE.test(s)) return normText(cleanSubject(subject));
  return normText(s);
}

export function cleanSubject(s: string): string {
  return s.replace(/^Announcement under Regulation 30 \(LODR\)\s*-\s*/i, "")
    .replace(/^Compliances?-\s*/i, "").replace(/_/g, " ").trim();
}

/** When a price is from: "30 Oct close", or "30 Oct, 11:05 IST · intraday" mid-session. */
export function priceWhen(date: string, asof?: number | null): string {
  if (!asof) return `${fmtDate(date)} close`;
  const ist = new Date((asof + 5.5 * 3600) * 1000);  // shift to IST, read as UTC
  const hh = ist.getUTCHours(), mm = ist.getUTCMinutes();
  const day = ist.toISOString().slice(0, 10);
  if (hh * 60 + mm >= 15 * 60 + 25) return `${fmtDate(day)} close`;
  return `${fmtDate(day)}, ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")} IST · intraday`;
}
