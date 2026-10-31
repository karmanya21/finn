// What a filing card says: facts, why it matters, the promises it touches. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { keyFacts, touches, whyItMatters } from "../src/brief.ts";

const filing = (type: string, summary: string, extra = {}) => ({
  id: "x", symbol: "LT", name: "Larsen & Toubro", at: "2026-10-27T10:00:00", type, label: type, subject: "", headline: "",
  pdf: null, summary, people: [], promises: [], numbers: null, importance: 60, routine: false, ...extra,
}) as any;
const thread = (id: string, title: string, metric: string, status = "open") =>
  ({ id, title, metric, status, kind: "hard", events: [], statements: [], target: "10–12%", period: "FY27" }) as any;

test("a rating action and the rating", () => {
  const f = filing("credit_rating", "CARE Ratings upgraded the subsidiary to CARE AA+; Stable.");
  assert.deepEqual(keyFacts(f).slice(0, 2).map((x) => x.value), ["Upgraded", "CARE AA+"]);
  assert.match(whyItMatters(f)!, /cheaper borrowing/);
});

test("amounts aren't counted twice", () => {
  const f = filing("press_release", "BEL secured orders worth Rs. 1011 crores.", { headline: "orders worth Rs. 1011 crores" });
  assert.deepEqual(keyFacts(f).map((x) => x.value), ["₹1011 cr"]);
});

test("an order win touches order inflow, not a divestment that shares a word", () => {
  const ts = [thread("LT-1", "Gold loan transaction close / Hyderabad Metro divestment", "deal_wins", "delayed"),
              thread("LT-2", "Group order inflow growth FY27", "order_inflow")];
  const f = filing("press_release", "Larsen & Toubro secured major orders for Dhaka Metro Line 1 in Bangladesh.");
  assert.deepEqual(touches(f, ts).ts.map((t) => t.id), ["LT-2"]);
});

test("ESOP shares fund nothing", () => {
  const f = filing("fund_raise", "Approved issue of equity shares to the Employee Welfare Trust under its stock option scheme.");
  assert.deepEqual(touches(f, [thread("B-1", "Business growth", "loan_growth")]).ts, []);
  assert.match(whyItMatters(f)!, /no new money/);
});

test("a non-plant regulator doesn't get the plant explanation", () => {
  const f = filing("regulatory", "The US Department of Labor suspended its PERM program applications.", { tone: "negative" });
  assert.doesNotMatch(whyItMatters(f)!, /site/);
});
