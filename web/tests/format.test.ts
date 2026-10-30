// House style for every number, date and headline the app shows. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { filingText, fmtChange, fmtDate, normText, priceWhen } from "../src/format.ts";

test("changes that round to zero read 'flat'", () => {
  assert.equal(fmtChange(0.04, 1), "flat");
  assert.equal(fmtChange(-0.02, 1, " pts"), "flat");
  assert.equal(fmtChange(0.3), "flat");
  assert.equal(fmtChange(22.4), "+22%");
  assert.equal(fmtChange(-1.84, 1), "−1.8%");
  assert.equal(fmtChange(null), "–");
});

test("periods and money in model-written text", () => {
  assert.equal(normText("Q2 FY'27 and H1 FY27"), "Q2FY27 and H1FY27");
  assert.equal(normText("Net debt INR4,358 crore"), "Net debt ₹4,358 cr");
  assert.equal(normText("up to Rs. 100 crore"), "up to ₹100 cr");
});

test("boilerplate summaries fall back to the subject", () => {
  assert.equal(filingText("Please find enclosed disclosure.", "Announcement under Regulation 30 (LODR)-Credit Rating"), "Credit Rating");
  assert.equal(filingText("Cipla's InvaGen facility cleared a USFDA inspection with zero observations.", "x"),
    "Cipla's InvaGen facility cleared a USFDA inspection with zero observations.");
});

test("one date format", () => {
  assert.equal(fmtDate(`${new Date().getFullYear()}-10-30`), "30 Oct");
  assert.equal(fmtDate("2025-03-04T10:00:00"), "4 Mar 2025");
});

test("a price says whether it's a close or intraday", () => {
  assert.equal(priceWhen("2026-10-30", 1793354296), "30 Oct close");         // 15:28 IST
  assert.match(priceWhen("2026-10-30", 1793340000), /11:30 IST · intraday$/); // 11:30 IST
});
