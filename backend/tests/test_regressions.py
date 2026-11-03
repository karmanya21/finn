"""Regression tests: every wrong output we found by hand, pinned so it can't come back.

    cd backend && .venv/bin/python -m unittest discover -s tests -v

Each test uses the real text that once produced the wrong answer. No network, no model
calls: these exercise the deterministic code that sits around the model.
"""

import unittest

from finn import ask, fiscal
from finn.filings import STOP, regulatory_tone, related_promises, rule_category
from finn.financials import Financials, oneoff_notes
from finn.promises import prefilter, resolve
from finn.promises.evidence import _figures_from_quote, only_forward
from finn.promises.extract import _normalize
from finn.promises.resolve import _plain, _value_verdict, changes, fmt_range, verdict
from finn.promises import link
from finn.promises.resolve import _comparable
from finn import push
from finn.push import _fold_repeats, routine_debt
from finn.results_pdf import _agrees_with_history, is_printed, printed_numbers
from finn.snapshot import _checked, _labelled


def stmt(low, high, quote, unit="%", period="FY27", kind="hard", call="May 2026", **kw):
    return {"kind": kind, "low": low, "high": high, "unit": unit, "period": period, "quote": quote,
            "call": call, "deadline": None, **kw}


def moves(*stmts):
    return [e["type"] for e in changes(list(stmts))]


class TargetsMustBeInTheirQuote(unittest.TestCase):
    """HUL, Tata Power, BEL: the model filled numbers in from context; they read as fake cuts."""

    def test_hul_no_number_in_quote(self):
        p = _normalize({"kind": "hard", "low": 22.5, "high": 23.5, "unit": "%", "metric": "ebitda_margin",
                        "quote": "So basically, we remain with this guidance that we have provided."})
        self.assertIsNone(p["low"]); self.assertIsNone(p["high"])
        self.assertEqual(p["kind"], "directional")

    def test_tata_power_target_unchanged_has_no_figure(self):
        p = _normalize({"kind": "hard", "low": 22000, "high": 22000, "unit": "Rs crore", "metric": "capex",
                        "quote": "So that's why our target of yearly investment continues to be the same."})
        self.assertIsNone(p["low"])

    def test_numbers_that_are_there_survive(self):
        p = _normalize({"kind": "hard", "low": 10, "high": 12, "unit": "%", "metric": "order_inflow",
                        "quote": "we expect the group order inflows to grow in the range of 10% to 12% in FY27."})
        self.assertEqual((p["low"], p["high"], p["kind"]), (10, 12, "hard"))

    def test_unit_rescaling_is_allowed(self):
        p = _normalize({"kind": "hard", "low": 5000, "high": 5000, "unit": "Rs crore", "metric": "capex",
                        "quote": "capital outlay of approximately Rs 50 billion towards Industrial Electronics"})
        self.assertEqual(p["low"], 5000)


class MultiplesAreNotPercentages(unittest.TestCase):
    """Kaynes: 'twice the market growth' was read as 2%."""

    def test_twice(self):
        p = _normalize({"kind": "hard", "low": 2, "high": 2, "unit": "%", "metric": "revenue_growth",
                        "quote": "we are quite confident of achieving the twice the market growth."})
        self.assertEqual(p["unit"], "x")
        self.assertEqual(fmt_range(p["low"], p["high"], p["unit"]), "2×")

    def test_4x(self):
        p = _normalize({"kind": "hard", "low": 4, "high": 4, "unit": None, "metric": "debt",
                        "quote": "we are looking at a net debt-to-EBITDA of about 4x."})
        self.assertEqual(p["unit"], "x")


class MovesOnlyBetweenComparableStatements(unittest.TestCase):
    """BEL, Tata Power: fake raises and cuts. Infosys: real ones must survive."""

    def test_bel_restated_old_figure_beside_another(self):
        self.assertEqual(moves(
            stmt(1600, None, "of the order of INR1,600 crores", unit="Rs crore", period="FY26"),
            stmt(1000, None, "R&D investments more than INR1,600 crores, capex more than INR1,000 crores",
                 unit="Rs crore", period="FY26")), ["reaffirmed"])

    def test_bel_open_bound_sharing_an_edge(self):
        self.assertEqual(moves(stmt(15, 17.5, "15% to 17.5% of growth", period="multi-year"),
                               stmt(15, None, "more than 15% in years to come", period="multi-year")), ["reaffirmed"])

    def test_tata_power_different_spans(self):
        self.assertEqual(moves(stmt(1.3, 1.3, "in Q3 and Q4 we will do about 1.3 gigawatt", unit="GW", period="H2FY26"),
                               stmt(2.5, 2.7, "2.5 gigawatts to 2.7 gigawatts", unit="GW", period="FY26")), ["reaffirmed"])

    def test_different_units(self):
        self.assertEqual(moves(stmt(15, 15, "revenue growth around 15%", period="FY26"),
                               stmt(1000, None, "capex more than INR1,000 crores", unit="Rs crore", period="FY26")),
                         ["reaffirmed"])

    def test_point_vs_at_least_same_number(self):
        self.assertEqual(moves(stmt(20000, 20000, "approximately 20,000", unit="count"),
                               stmt(20000, None, "20,000-plus freshers", unit="count")), ["reaffirmed"])

    def test_infosys_real_cut(self):
        self.assertEqual(moves(stmt(1.5, 3, "1.5% to 3%"), stmt(1, 2, "guidance is 1% to 2%")), ["lowered"])

    def test_infosys_cut_quoting_the_old_band(self):
        self.assertEqual(moves(stmt(1.5, 3, "1.5% to 3%"),
                               stmt(1, 2, "revised downward from 1.5% to 3% to 1% to 2%")), ["lowered"])

    def test_infosys_raise_sharing_a_bound(self):
        self.assertEqual(moves(stmt(2, 3, "growth between 2% and 3%", period="FY26"),
                               stmt(3, 3.5, "The new revenue growth guidance for this financial year is 3% to 3.5%",
                                    period="FY26")), ["raised"])

    def test_milestone_deferred(self):
        self.assertEqual(moves(stmt(None, None, "closure in Q1 FY27", kind="milestone", period="Q1FY27"),
                               stmt(None, None, "revised to 30th September 2026", kind="milestone", period="Q2FY27")),
                         ["deferred"])


class EvidenceMustSupportItsFigures(unittest.TestCase):
    """Infosys hiring scored on a forward-looking line; L&T's '20% CAGR' from the wrong sentence."""

    def test_restated_promise_is_not_proof(self):
        self.assertTrue(only_forward("delivered", "we are going to hire 20,000-plus employees over the year"))

    def test_past_tense_is_proof(self):
        self.assertFalse(only_forward("delivered", "For H1, onsite mix was down by 0.7%."))

    def test_delay_may_be_future_tense(self):
        self.assertFalse(only_forward("delayed", "A large chunk of it is expected to get commissioned only in April 2026."))

    def test_lt_cagr_from_another_sentence_is_dropped(self):
        out = _figures_from_quote({"value": 20, "reported": "FY26 order inflow 20% CAGR vs 10% guidance"},
                                  "I am pleased to share that we have significantly surpassed this guidance.",
                                  {"low": 10, "high": None})
        self.assertIsNone(out["value"]); self.assertIsNone(out["reported"])

    def test_figure_in_quote_is_kept(self):
        out = _figures_from_quote({"value": 4.1, "reported": "FY26 NWC to revenue closed at 4.1% vs 10% target"},
                                  "Our NWC to Sales ratio has improved from 11% in March '25 to 4.1% in March '26.",
                                  {"low": 10, "high": 10})
        self.assertEqual(out["value"], 4.1)

    def test_plain_line_names_the_guide(self):
        self.assertEqual(_plain("beat", {"low": 10, "high": None, "unit": "%"}),
                         "Beat the ≥10% guide (per management; no figure given)")


class VerdictRules(unittest.TestCase):
    """Aarti capex, Infosys onsite mix, L&T five-year targets, Bajaj 'gone quiet'."""

    def test_small_overrun_is_kept(self):  # capex guided ~1,100, spent 1,125
        self.assertEqual(_value_verdict(1125, {"low": 1100, "high": 1100, "unit": "Rs crore"}, False, True), "delivered")

    def test_lower_is_better_not_kept_before_period_ends(self):
        self.assertEqual(_value_verdict(0.7, {"low": 0.75, "high": 1, "unit": "%"}, True, False), "on_track")

    def _v(self, stmts, n_calls, evidence=None):
        for i, s in enumerate(stmts):
            s.setdefault("call_index", i)
        return verdict(stmts, [], Financials("X"), n_calls, evidence)["status"]

    def test_five_year_target_cant_be_delivered(self):
        s = [stmt(16, 18, "16% to 18% ROE", period="multi-year")]
        self.assertEqual(self._v(s, 2, {"status": "delivered", "value": None, "quote": "x", "call": "Jul 2026"}), "on_track")

    def test_one_off_remark_not_due_stays_open(self):
        self.assertEqual(self._v([stmt(20, 20, "business growth of 20%", period="FY28")], 4), "open")

    def test_repeated_then_dropped_is_quiet(self):
        s = [stmt(20, 20, "20%", period="FY28", call="Jan"), stmt(20, 20, "20%", period="FY28", call="Feb")]
        self.assertEqual(self._v(s, 5), "gone_quiet")


class FilingClassification(unittest.TestCase):
    """Cipla USFDA, investor-meet intimations, board outcomes, newspaper results, Bajaj NCDs."""

    def cat(self, category, sub, subject, headline=""):
        return rule_category({"category": category, "subcategory": sub, "subject": subject, "headline": headline})

    def test_usfda_is_regulatory(self):
        self.assertEqual(self.cat("Company Update", "General", "USFDA Inspection At Invagen Manufacturing Facility"), "regulatory")

    def test_investor_meet_intimation(self):
        self.assertEqual(self.cat("Company Update", "Analyst / Investor Meet",
                                  "Announcement under Regulation 30 (LODR)-Analyst / Investor Meet - Intimation",
                                  "Intimation of Earnings Conference Call for the Financial Results"), "investor_meetings")

    def test_generic_board_outcome_is_read(self):
        self.assertIsNone(self.cat("Board Meeting", "Outcome of Board Meeting", "Board Meeting Outcome for Outcome Of Board Meeting Held Today"))

    def test_board_outcome_fund_raise(self):
        self.assertEqual(self.cat("Board Meeting", "Outcome of Board Meeting",
                                  "Board Meeting Outcome for Outcome Of Board Meeting - Fund Raising (Enabling)"), "fund_raise")

    def test_board_meeting_notice_is_routine(self):
        self.assertEqual(self.cat("Board Meeting", "Board Meeting", "Board Meeting Intimation for Considering The Unaudited Financial Results"), "unclassified")

    def test_newspaper_copy_of_results_is_routine(self):
        self.assertEqual(self.cat("Company Update", "Newspaper Publication",
                                  "Announcement under Regulation 30 (LODR)-Newspaper Publication", "Financial results"), "unclassified")

    def test_results(self):
        self.assertEqual(self.cat("Result", "Financial Results", "Financial Results For The Quarter Ended September 30, 2026"), "results")

    def test_regulatory_tone(self):
        self.assertEqual(regulatory_tone("USFDA inspection at InvaGen with zero Form 483 observations"), "positive")
        self.assertEqual(regulatory_tone("Receipt of warning letter from USFDA"), "negative")
        self.assertEqual(regulatory_tone("USFDA issued Form 483 with 3 observations"), "negative")
        self.assertIsNone(regulatory_tone("USFDA inspection at Goa facility"))

    def test_lender_debt_allotments_are_routine(self):
        text = "Bajaj Finance allotted 2,06,950 secured redeemable non-convertible debentures"
        self.assertTrue(routine_debt(text, lender=True))
        self.assertFalse(routine_debt(text, lender=False))
        self.assertTrue(routine_debt("Allotment of commercial paper", lender=False))

    def test_generic_words_dont_link_promises(self):
        for w in ("facility", "inspection", "plant", "site"):
            self.assertIn(w, STOP)
        ledger = {"threads": [{"id": "CIPLA-1", "kind": "milestone", "status": "gone_quiet",
                               "title": "Indore facility inspection", "scope": "Indore"}]}
        self.assertEqual(related_promises({"subject": "USFDA Inspection At Invagen Manufacturing Facility", "headline": "",
                                           "summary": "zero Form 483 observations"}, ledger), [])


class ResultsReadFromFilings(unittest.TestCase):
    """Max Healthcare's dotted thousands; Dixon's standalone/wrong-column read; one-off notes."""

    def test_dotted_thousands(self):
        nums = printed_numbers("Revenue from operations \n2.56.921 \n2.36.617 \nOther income \n4.090")
        self.assertTrue(is_printed(256921, nums)); self.assertTrue(is_printed(236617, nums))

    def test_rounded_figure_counts_as_printed(self):
        self.assertTrue(is_printed(256921, printed_numbers("2,56,921.15")))
        self.assertFalse(is_printed(256000, printed_numbers("2,56,921.15")))

    def _fin(self):
        f = Financials("DIXON")
        f.quarters[(27, 1)] = {"Sales": 15548.0}
        f.quarters[(26, 2)] = {"Sales": 14855.0}
        return f

    def test_wrong_basis_is_rejected(self):  # Dixon standalone ~1,000s vs consolidated history
        self.assertFalse(_agrees_with_history({"previous_quarter": 1079, "year_ago": 1385}, "Q2FY27", self._fin()))

    def test_right_basis_is_accepted(self):
        self.assertTrue(_agrees_with_history({"previous_quarter": 15548, "year_ago": 14855}, "Q2FY27", self._fin()))

    def test_one_off_notes(self):
        max_ = oneoff_notes({"ebitda": 711, "pbt": 541, "pat": 414}, {"ebitda": 575, "pbt": 446, "pat": 491})
        self.assertIn("tax credit", max_[0]["text"])
        dixon = oneoff_notes({"ebitda": 600, "pbt": 532, "pat": 411}, {"ebitda": 563, "pbt": 924, "pat": 746})
        self.assertIn("one-off gain", dixon[0]["text"])
        self.assertEqual(oneoff_notes({"ebitda": 8814, "pbt": 7641, "pat": 5949}, {"ebitda": 8513, "pbt": 6336, "pat": 4678}), [])
        self.assertEqual(oneoff_notes({}, {"ebitda": 60, "pbt": 42, "pat": 43}), [])  # Aarti: ₹1 cr credit is noise


class CaseFiguresAreLabelled(unittest.TestCase):
    def test_bare_number_gets_its_source(self):
        self.assertEqual(_labelled("11.8%", ["LT-1"], {"LT-1": "FY26 revenue growth"}), "11.8% · FY26 revenue growth")

    def test_labelled_figure_unchanged(self):
        self.assertEqual(_labelled("Net debt ₹4,358 cr", ["debt"], {"debt": "Net debt"}), "Net debt ₹4,358 cr")


class TranscriptSpeakers(unittest.TestCase):
    """L&T 'Sensitivity' stamp read as a speaker; a frequent speaker's name stripped as a header."""

    def test_stamp_and_header_are_not_speakers(self):
        pages = []
        body = "\n".join(f"Discussion line {k} about execution and the order book." for k in range(8))
        for i in range(1, 7):  # real pages: running header/footer at the edges, speakers mid-page
            pages.append(f"[[page {i}]]\nLarsen & Toubro Limited\nPage {i} of 6\n{body}\n"
                         "Sensitivity : This Document is Classified as \"LNT Internal Use\".\n"
                         "P. Ramakrishnan:\nWe expect revenue growth of 10% to 12% for FY27.\n"
                         f"Mohit Kumar:\nWhat about margins next year?\n{body}\nJuly 28, 2026")
        speakers = {t.speaker for t in prefilter.turns("\n".join(pages))}
        self.assertIn("P. Ramakrishnan", speakers)
        self.assertNotIn("Sensitivity", speakers)

    def test_name_alone_on_a_line(self):  # Infosys format
        text = "[[page 1]]\nSalil Parekh\nOur revenue growth guidance is 1% to 2% for FY27.\nRishi Basu\nNext question.\n" \
               "Salil Parekh\nOperating margin guidance remains at 20% to 22%.\n"
        self.assertIn("Salil Parekh", {t.speaker for t in prefilter.turns(text)})


# ---- found by the audit (round 1) --------------------------------------------------------

THREADS = {
    "CIPLA-0": {"id": "CIPLA-0", "title": "FY26 EBITDA margin", "target": "23.5–24.5%", "status": "delivered",
                "period": "FY26", "events": [], "statements": [], "detail": "EBITDA margin 24% (reported)"},
    "TATAPOWER-15": {"id": "TATAPOWER-15", "title": "FY26 Mundra plant availability", "target": "80%",
                     "status": "missed", "period": "FY26", "events": [], "statements": [],
                     "detail": "Mundra plant shut for 6 months, no capacity charges received"},
    "INFY-4": {"id": "INFY-4", "title": "Telstra JV transaction close", "target": "", "status": "delayed",
               "period": "FY26", "events": [], "statements": [], "detail": "awaiting regulatory approvals"},
}
METRICS = {"debt": {"key": "debt", "label": "Net debt", "value": 0.4, "unit": "x equity",
                    "reading": "Net debt ₹62,497 cr in FY26, 0.4× equity; up from ₹44,554 cr in FY22."}}


def case_point(figure, point, refs):
    return _checked({"figure": figure, "point": point, "refs": refs}, THREADS, METRICS)


class CaseFiguresComeFromWhatTheyCite(unittest.TestCase):
    """Cipla, IndiGo, Praj: "Delivered promises 3"; Tata Power: "availability 0%"; Dixon: ids shown."""

    def test_invented_count_is_rebuilt_from_the_source(self):
        p = case_point("Delivered promises 3", "Management delivered its FY26 EBITDA margin target.", ["CIPLA-0"])
        self.assertEqual(p["figure"], "FY26 EBITDA margin: 23.5–24.5% target, kept")

    def test_uncited_count_in_the_sentence_drops_the_point(self):
        self.assertIsNone(case_point("Kept promises 3", "Management delivered on three key targets.", ["CIPLA-0"]))

    def test_inferred_value_is_replaced(self):
        p = case_point("Mundra plant availability 0%", "Mundra was shut for 6 months.", ["TATAPOWER-15"])
        self.assertEqual(p["figure"], "FY26 Mundra plant availability: 80% target, missed")

    def test_internal_id_never_shown(self):
        p = case_point("TATAPOWER-15 availability missed", "TATAPOWER-15 missed its 80% target.", ["TATAPOWER-15"])
        self.assertNotIn("TATAPOWER-15", p["figure"] + p["point"])
        self.assertIn("FY26 Mundra plant availability", p["point"])

    def test_status_codes_read_as_words(self):  # Dixon: "₹1,100–1,200 cr: gone_quiet"
        p = case_point("FY26 Mundra plant availability 80%: gone_quiet", "It went quiet.", ["TATAPOWER-15"])
        self.assertNotIn("_", p["figure"])

    def test_milestone_figure_is_its_date(self):
        p = case_point("Delayed JV transaction", "The close awaits approvals.", ["INFY-4"])
        self.assertEqual(p["figure"], "Telstra JV transaction close: due FY26, delayed")

    def test_cited_figure_is_kept(self):
        p = case_point("Net debt ₹62,497 cr", "Net debt rose from ₹44,554 cr in FY22.", ["debt"])
        self.assertEqual(p["figure"], "Net debt ₹62,497 cr")

    def test_figure_cant_mix_two_sources(self):
        p = case_point("80% target vs ₹62,497 cr", "Both went the wrong way.", ["TATAPOWER-15", "debt"])
        self.assertEqual(p["figure"], "FY26 Mundra plant availability: 80% target, missed")

    def test_five_year_is_a_tenor_not_a_count(self):
        p = case_point("Net debt ₹62,497 cr", "Net debt rose over the five-year period.", ["debt"])
        self.assertIsNotNone(p)


class UnitsAndScale(unittest.TestCase):
    def test_billions_in_a_crore_target(self):  # Polycab: "₹12 billion" stored as ₹12 cr
        p = _normalize({"kind": "hard", "low": 12, "high": 16, "unit": "Rs crore", "metric": "capex",
                        "quote": "in line with our Project Spring guidance of investing ₹ 12 billion - ₹ 16 billion annually"})
        self.assertEqual((p["low"], p["high"]), (1200, 1600))

    def test_per_ton_is_not_crore(self):  # UltraTech: "EBITDA per metric ton in excess of INR1,000"
        p = _normalize({"kind": "hard", "low": 1000, "high": None, "unit": "Rs crore", "metric": "ebitda",
                        "quote": "we are confident of reaching an EBITDA per metric ton in excess of INR1,000 by FY'28."})
        self.assertEqual(p["unit"], "Rs per ton")

    def test_number_from_the_next_clause(self):  # BEL: R&D target took capex's ₹1,000 cr
        p = _normalize({"kind": "hard", "low": 1000, "high": None, "unit": "Rs crore", "metric": "capex",
                        "metric_label": "R&D investment",
                        "quote": "R&D investments more than INR1,600 crores, capex more than INR1,000 crores"})
        self.assertIsNone(p["low"])
        p = _normalize({"kind": "hard", "low": 1000, "high": None, "unit": "Rs crore", "metric": "capex",
                        "metric_label": "Capex",
                        "quote": "R&D investments more than INR1,600 crores, capex more than INR1,000 crores"})
        self.assertEqual(p["low"], 1000)

    def test_annual_vs_multi_year_is_not_a_move(self):  # UltraTech capex ≤₹1,800 cr -> ≥₹10,000 cr
        a = stmt(None, 1800, "We'll remain within our capex plan of INR1,800 crores", unit="Rs crore", period="FY26")
        b = stmt(10000, None, "I will have about INR10,000 crores minimum per year, outgo.", unit="Rs crore", period="multi-year")
        self.assertFalse(_comparable(a, b))

    def test_hundredfold_jump_is_not_a_move(self):
        a = stmt(1200, 1600, "roughly ₹ 1,200 crores to ₹ 1,600 crores on an annual basis", unit="Rs crore", period="FY26")
        b = stmt(12, 16, "guidance of investing 12 to 16 annually", unit="Rs crore", period="FY26")
        self.assertEqual(moves(a, b), ["reaffirmed"])

    def test_real_raise_still_counts(self):
        a = stmt(1000, 1000, "capex of about Rs 1,000 crore this year", unit="Rs crore", period="FY27")
        b = stmt(1200, 1200, "we are raising capex to Rs 1,200 crore", unit="Rs crore", period="FY27")
        self.assertEqual(moves(a, b), ["raised"])


def calls_with(*promises):
    """One call per promise, so ids are c0p0, c1p0, ..."""
    return [{"call": call, "promises": [p]} for call, p in promises]


def prom(metric, low, high, unit, period, kind="hard", label=None):
    return {"metric": metric, "metric_label": label or metric, "low": low, "high": high, "unit": unit,
            "period": period, "kind": kind}


class OneThreadOneCommitment(unittest.TestCase):
    def test_capex_figure_leaves_the_revenue_thread(self):  # BEL-0 showed a ₹1,000 cr target
        calls = calls_with(("May 2025", prom("revenue_growth", 15, 15, "%", "FY26", label="Revenue growth")),
                           ("Aug 2025", prom("revenue_growth", 15, None, "%", "FY26", label="Revenue growth")),
                           ("Jan 2026", prom("capex", 1000, 1000, "Rs crore", "FY26", label="CAPEX")),
                           ("Feb 2026", prom("capex", 1000, None, "Rs crore", "FY26", label="Capex")))
        threads = [{"title": "FY26 revenue growth", "ids": ["c0p0", "c1p0", "c2p0"]},
                   {"title": "FY26 Capex", "ids": ["c3p0"]}]
        out = link.regroup([part for t in threads for part in link.split(t, calls)], calls)
        self.assertEqual([(t["title"], t["ids"]) for t in out],
                         [("FY26 revenue growth", ["c0p0", "c1p0"]), ("FY26 Capex", ["c2p0", "c3p0"])])

    def test_different_years_split(self):  # Tata Steel capex ₹15,000 cr FY26, ₹20,000 cr FY27
        calls = calls_with(("May 2025", prom("capex", 15000, 15000, "Rs crore", "FY26", label="Company capex")),
                           ("May 2026", prom("capex", 20000, 20000, "Rs crore", "FY27", label="Company capex")))
        out = link.split({"title": "Company capex", "ids": ["c0p0", "c1p0"]}, calls)
        self.assertEqual([t["title"] for t in out], ["FY26 Company capex", "FY27 Company capex"])

    def test_share_and_size_split(self):  # Bajaj gold loans: ₹27,000–30,000 cr and 5% of the book
        calls = calls_with(("Dec 2025", prom("other", 27000, 30000, "Rs crore", "FY27", label="Gold loan book size")),
                           ("Apr 2026", prom("loan_growth", 5, 5, "%", "FY27")),
                           ("May 2026", prom("loan_growth", 5, 5, "%", "FY27")))
        self.assertEqual(len(link.split({"title": "Gold loans", "ids": ["c0p0", "c1p0", "c2p0"]}, calls)), 2)

    def test_slipping_capacity_deadline_stays_one_thread(self):
        calls = calls_with(("May 2025", prom("capacity", 10, 10, "MTPA", "FY27")),
                           ("May 2026", prom("capacity", 10, 10, "MTPA", "FY28")))
        self.assertEqual(len(link.split({"title": "Plant capacity", "ids": ["c0p0", "c1p0"]}, calls)), 1)

    def test_statement_filed_under_the_wrong_thread_moves(self):  # BEL capex under R&D
        calls = calls_with(("May 2025", prom("capex", 1600, None, "Rs crore", "FY26", label="R&D investment")),
                           ("Aug 2025", prom("capex", 1600, None, "Rs crore", "FY26", label="R&D investment")),
                           ("Nov 2025", prom("capex", 1000, None, "Rs crore", "FY26", label="Capex")),
                           ("Jan 2026", prom("capex", 1000, 1000, "Rs crore", "FY26", label="Capex")))
        out = link.regroup([{"title": "FY26 R&D investment", "ids": ["c0p0", "c1p0", "c2p0"]},
                            {"title": "FY26 Capex", "ids": ["c3p0"]}], calls)
        self.assertEqual([t["ids"] for t in out], [["c0p0", "c1p0"], ["c2p0", "c3p0"]])

    def test_mw_and_gw_are_one_dimension(self):
        self.assertEqual(link.dimension("MW"), link.dimension("GW"))
        self.assertEqual(link.dimension("megawatts"), "power")
        self.assertIsNone(link.dimension("million units"))

    def test_far_year_target_is_the_long_range_plan(self):  # Polycab "FY30" and "multi-year"
        calls = calls_with(("Jul 2025", prom("ebitda_margin", 10, 12, "%", "multi-year")),
                           ("Oct 2025", prom("ebitda_margin", 10, 12, "%", "FY30")))
        self.assertEqual(len(link.split({"title": "FMEG margin by FY30", "ids": ["c0p0", "c1p0"]}, calls)), 1)


class AnalystsAreNotManagement(unittest.TestCase):
    """Eternal: an analyst's "you've maintained your $3 billion NOV guidance" stored as a promise."""

    TEXT = ("[[page 1]]\nModerator:\nThe next question is from the line of Manish Adukia from Goldman Sachs.\n"
            "Manish Adukia:\nYou've maintained your $3 billion NOV guidance for going-out in FY30?\n"
            "Deepinder Goyal:\nYes, we expect going-out NOV of $3 billion by FY30.\n"
            "Manish Adukia:\nThanks. And on quick commerce margins?\n"
            "Deepinder Goyal:\nWe expect to break even next year.\n"
            "Moderator:\nThe next question is from the line of Sachin Dixit from JM Financial.\n"
            "Sachin Dixit:\nOn competition, any change?\nSachin Dixit:\nThank you.\n")

    def test_questioner_found_from_the_moderator(self):
        turns = prefilter.turns(self.TEXT)
        self.assertEqual(prefilter.questioners(turns), {"Manish Adukia", "Sachin Dixit"})

    def test_quote_attributed_by_where_it_sits(self):
        from finn.promises.extract import _norm
        turns = prefilter.turns(self.TEXT)
        self.assertEqual(prefilter.speaker_of(turns, "You've maintained your $3 billion NOV guidance", _norm), "Manish Adukia")
        self.assertEqual(prefilter.speaker_of(turns, "we expect going-out NOV of $3 billion by FY30", _norm), "Deepinder Goyal")


class FilingsRound2(unittest.TestCase):
    def test_word_common_to_many_promises_doesnt_link(self):  # BEL 'order', Tata Power 'project'
        threads = [{"id": f"BEL-{i}", "kind": "hard", "status": "open", "title": t, "scope": "company"}
                   for i, t in enumerate(["QRSAM order acquisition", "NGC order inflow", "FY27 order inflow", "Export orders"])]
        self.assertEqual(related_promises({"subject": "Receipt of orders", "headline": "BEL secures orders worth ₹572 cr",
                                           "summary": ""}, {"threads": threads}), [])

    def test_specific_word_still_links(self):
        threads = [{"id": "BEL-0", "kind": "milestone", "status": "open", "title": "QRSAM order acquisition", "scope": "company"},
                   {"id": "BEL-1", "kind": "hard", "status": "open", "title": "NGC order inflow", "scope": "company"},
                   {"id": "BEL-2", "kind": "hard", "status": "open", "title": "FY27 order inflow", "scope": "company"}]
        self.assertEqual(related_promises({"subject": "Receipt of order", "headline": "BEL bags QRSAM order",
                                           "summary": ""}, {"threads": threads}), ["BEL-0"])

    def test_same_figures_fold(self):  # Bajaj Finance QIP: board outcome and Reg 30 notice, one event
        base = {"type": "fund_raise", "people": [], "importance": 75, "pdf": "x.pdf"}
        a = {**base, "at": "2026-10-01T23:27:15", "subject": "Preferential Issue",
             "summary": "Board approved raising up to Rs 11,700 crore via QIP and Rs 5,800 crore through warrants."}
        b = {**base, "at": "2026-10-01T23:10:46", "subject": "Board Meeting Outcome for Capital Raise",
             "summary": "Board approved raising up to Rs 11,700 crore via QIP and Rs 5,800 crore via warrants."}
        self.assertEqual(len(_fold_repeats([a, b])), 1)

    def test_same_summary_folds(self):  # Tata Steel NCLT order, Aarti ₹1,500 cr contract
        base = {"type": "press_release", "people": [], "importance": 50, "pdf": "x.pdf"}
        a = {**base, "at": "2026-10-13T14:58:28", "subject": "Agreement With A Global Speciality Chemical Company.",
             "summary": "Aarti Industries entered into a 10+ year supply agreement worth Rs. 1500 crores with a global speciality chemical company."}
        b = {**base, "at": "2026-10-13T14:37:15", "subject": "Press Release / Media Release",
             "summary": "Aarti Industries entered a 10+ year supply agreement with a global speciality chemical company, yielding ₹1,500 crores."}
        self.assertEqual(len(_fold_repeats([a, b])), 1)

    def test_non_pharma_regulatory_tone(self):  # Infosys PERM suspension
        self.assertEqual(regulatory_tone("the suspension of its U.S. Permanent Labor Certification applications"), "negative")
        self.assertEqual(regulatory_tone("show-cause notice received from the tax authority"), "negative")

    def test_failed_company_keeps_last_filings(self):  # Trent lost all 9 when the model quota ran out
        from datetime import datetime, timedelta
        from unittest import mock
        recent = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
        expired = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
        first = push.PORTFOLIO[0].symbol
        previous = {"items": [{"symbol": first, "at": recent, "routine": False},
                              {"symbol": first, "at": expired, "routine": False}]}

        def fetch(c, ledger, days):
            if c.symbol == first:
                raise RuntimeError("no free model available")
            return []
        with mock.patch.object(push, "digest_company", fetch), mock.patch("builtins.print"):
            d = push.build_digest({}, previous=previous)
        self.assertEqual(d["failed"], [first])
        self.assertEqual([i["at"] for i in d["items"]], [recent])  # still in the window; the 40-day-old one ages out


class ProgressCountsFiledResults(unittest.TestCase):
    """BEL's Q2FY27 filing was out, but promise progress still read Q1FY27 from screener."""
    def fin(self):
        q = lambda s, op, pbt, tax: {"Sales": s, "Operating Profit": op, "Profit before tax": pbt,
                                     "Net Profit": pbt * 0.75, "Tax %": tax}
        return Financials("BEL", quarters={(26, 1): q(100, 25, 24, 25), (26, 2): q(100, 25, 24, 25),
                                           (27, 1): q(125, 31, 30, 25)})
    filing = {"quarter": "Q2FY27", "source": "the company's results filing (statutory consolidated)",
              "rows": [{"label": "Revenue", "value": 110}, {"label": "EBITDA", "value": 33},
                       {"label": "PBT", "value": 32}, {"label": "PAT", "value": 24}]}

    def test_growth_pace_uses_the_filed_quarter(self):
        p = {"kind": "hard", "metric": "revenue_growth", "low": 15, "high": 15, "unit": "%", "period": "FY27"}
        pace = resolve.financial_check(p, self.fin().with_filing(self.filing))["pace"]
        self.assertEqual(pace["so_far_label"], "H1FY27")
        self.assertAlmostEqual(pace["so_far"], 17.5)

    def test_measure_the_filing_lacks_stops_at_screener(self):  # no tax line in the filing
        p = {"kind": "hard", "metric": "other", "metric_label": "tax rate", "low": None, "high": 26, "unit": "%",
             "period": "FY27"}
        self.assertEqual(resolve.financial_check(p, self.fin().with_filing(self.filing))["pace"]["so_far_label"], "Q1FY27")

    def test_only_the_next_quarter_or_a_filing_source(self):
        self.assertFalse(self.fin().with_filing({**self.filing, "quarter": "Q3FY27"}).filed)
        self.assertFalse(self.fin().with_filing({**self.filing, "source": "screener.in"}).filed)

    def test_refresh_pace_rewrites_running_promises(self):
        p = {"kind": "hard", "metric": "revenue_growth", "low": 15, "high": 15, "unit": "%", "period": "FY27"}
        old = resolve.financial_check(p, self.fin())["pace"]
        ledger = {"latest_results": "Q1FY27", "threads": [{"check": p, "pace": old}]}
        self.assertEqual(resolve.refresh_pace(ledger, self.fin().with_filing(self.filing)), 1)
        self.assertEqual(ledger["threads"][0]["pace"]["so_far_label"], "H1FY27")
        self.assertEqual(ledger["latest_results"], "Q2FY27")

    def test_silence_waits_for_the_call_after_the_period(self):  # BEL H1FY27 NGC inflow, Q2 filed before the call
        s = [stmt(None, None, "NGC order in H1", kind="milestone", period="H1FY27", call="Jan 2026", call_index=0)]
        fin = self.fin().with_filing(self.filing)
        self.assertNotEqual(resolve.verdict(s, [], fin, 3, None, heard_through=(27, 1))["status"], "gone_quiet")
        self.assertEqual(resolve.verdict(s, [], fin, 3, None, heard_through=(27, 2))["status"], "gone_quiet")


class PricesAdjustForSplits(unittest.TestCase):
    def test_unadjusted_bonus_is_rescaled(self):  # Trent, 1 Jan 2026: 4,279 -> 2,865 on normal volume
        rows = [["2025-12-%02d" % d, 4279.0, 500000] for d in range(1, 22)] + [["2026-01-01", 2864.93, 560000]]
        out = push.adjust_splits(rows)
        self.assertAlmostEqual(out[0][1], 2852.67, places=1)
        self.assertEqual(out[-1][1], 2864.93)

    def test_real_crash_on_heavy_volume_stays(self):
        rows = [["2025-12-%02d" % d, 100.0, 1000] for d in range(1, 22)] + [["2026-01-01", 50.0, 9000]]
        self.assertEqual(push.adjust_splits(rows)[0][1], 100.0)

    def test_drop_that_isnt_a_split_fraction_stays(self):
        rows = [["2025-12-%02d" % d, 100.0, 1000] for d in range(1, 22)] + [["2026-01-01", 72.0, 1000]]
        self.assertEqual(push.adjust_splits(rows)[0][1], 100.0)


class LatestSessionIsNotDropped(unittest.TestCase):
    # Yahoo, 4 Nov 2026: INFY's 3 Nov row had no close, but meta had that session's price and volume
    def chart(self, closes, vols, last_day_ts, price=1148.1, volume=10494010):
        days = [1793331900, 1793591100, 1793677500]  # 30 Oct, 2 Nov, 3 Nov, 09:15 IST
        return {"timestamp": days, "indicators": {"quote": [{"close": closes, "volume": vols}]},
                "meta": {"regularMarketPrice": price, "regularMarketVolume": volume, "regularMarketTime": last_day_ts}}

    def test_empty_latest_close_uses_the_sessions_last_price(self):
        rows, _ = push.parse_chart(self.chart([1185.0, 1161.0, None], [9e6, 8e6, None], 1793699979))  # 3 Nov 15:29
        self.assertEqual(rows[-1], ["2026-11-03", 1148.1, 10494010])
        self.assertEqual(len(rows), 3)

    def test_meta_from_another_day_is_not_used(self):
        rows, _ = push.parse_chart(self.chart([1185.0, 1161.0, None], [9e6, 8e6, None], 1793613579))  # 2 Nov 15:29
        self.assertEqual(rows[-1][0], "2026-11-02")

    def test_only_the_latest_day_is_filled(self):
        rows, _ = push.parse_chart(self.chart([1185.0, None, 1148.1], [9e6, None, 1e7], 1793699979))
        self.assertEqual([r[0] for r in rows], ["2026-10-30", "2026-11-03"])


class AskAnswersOnlyFromTheRecord(unittest.TestCase):
    srcs = {"BEL-3": {"kind": "promise", "label": "FY26 R&D investment", "ref": "BEL-3",
                      "text": "Promise 'FY26 R&D investment': target >=₹1,600 cr; status gone quiet."},
            "results": {"kind": "results", "label": "Q2FY27 results", "text": "Revenue ₹7,328 cr, +26.5% YoY"}}

    def test_cited_and_backed_sentence_stays(self):
        out = ask.check({"sentences": [{"text": "They promised R&D of ₹1,600 cr and then went quiet.", "sources": ["BEL-3"]}]}, self.srcs)
        self.assertEqual(len(out), 1)

    def test_number_not_in_cited_source_is_dropped(self):
        out = ask.check({"sentences": [{"text": "Revenue grew 31% YoY.", "sources": ["results"]}]}, self.srcs)
        self.assertEqual(out, [])

    def test_unknown_source_or_no_source_is_dropped(self):
        out = ask.check({"sentences": [{"text": "Margins will improve.", "sources": ["X-9"]},
                                       {"text": "Margins will improve.", "sources": []}]}, self.srcs)
        self.assertEqual(out, [])

    def test_saying_what_the_record_lacks_needs_no_source(self):
        out = ask.check({"sentences": [{"text": "The record doesn't give a reason for the fall.", "sources": []},
                                       {"text": "The record doesn't show the 12% fall.", "sources": []}]}, self.srcs)
        self.assertEqual([x["text"] for x in out], ["The record doesn't give a reason for the fall."])

    def test_advice_is_dropped(self):
        out = ask.check({"sentences": [{"text": "Given 26.5% growth, this is a good buy.", "sources": ["results"]}]}, self.srcs)
        self.assertEqual(out, [])


    def test_finns_note_isnt_put_in_managements_mouth(self):  # Max Healthcare's tax-credit note
        srcs = {"results": {"kind": "results", "label": "Q2FY27 results",
                            "text": "PAT ₹414 cr, -15.6% YoY. FINN's note: year-ago quarter had a tax credit."},
                "MAX-1": {"kind": "promise", "label": "Margin", "text": "Said: \"margins will hold\""}}
        out = ask.check({"sentences": [{"text": "Management's explanation is that the year-ago quarter had a tax credit.",
                                        "sources": ["results"]}]}, srcs)
        self.assertEqual(out[0]["text"], "The record's explanation is that the year-ago quarter had a tax credit.")
        out = ask.check({"sentences": [{"text": "Management believes growth will recover.", "sources": ["results"]}]}, srcs)
        self.assertEqual(out, [])
        out = ask.check({"sentences": [{"text": "Management said margins will hold.", "sources": ["MAX-1"]}]}, srcs)
        self.assertEqual(len(out), 1)

    def test_a_number_under_the_wrong_results_line_is_dropped(self):  # Max: "PBT was ₹414 cr" (414 is PAT)
        lines = {"pbt": [541, 21.3, 24.0], "pat": [414, -15.6, 28.3], "revenue": [2569, 20.3, 8.6], "margin": [27.7, 26.9]}
        self.assertTrue(ask.mislabelled("PBT was ₹414 cr, down 15.6% YoY", lines))
        self.assertFalse(ask.mislabelled("PAT was ₹414 cr, down 15.6% YoY", lines))
        self.assertFalse(ask.mislabelled("revenue of ₹2,569 cr, up 20.3% YoY, with an EBITDA margin of 27.7% vs 26.9%", lines))
        self.assertFalse(ask.mislabelled("a year ago PAT was ₹491 cr above PBT of ₹446 cr", lines, [491, 446]))

    def test_portfolio_questions_find_the_companies_they_name(self):
        ledgers = {"BEL": {"name": "Bharat Electronics Ltd"}, "TATASTEEL": {"name": "Tata Steel Limited"},
                   "TATAPOWER": {"name": "Tata Power Company Ltd"}, "M&M": {"name": "Mahindra & Mahindra Ltd"}}
        self.assertEqual(ask.named("How are Tata Steel and bel doing?", ledgers), ["BEL", "TATASTEEL"])
        self.assertEqual(ask.named("what about M&M", ledgers), ["M&M"])
        self.assertEqual(ask.named("which of my companies missed guidance?", ledgers), [])

    def test_two_companies_results_dont_trip_the_line_check(self):
        srcs = {"A": {"kind": "company", "label": "A", "text": "PAT ₹414 cr", "lines": {"pat": [414]}},
                "B": {"kind": "company", "label": "B", "text": "PAT ₹852 cr", "lines": {"pat": [852]}}}
        out = ask.check({"sentences": [{"text": "PAT was ₹414 cr at A and ₹852 cr at B.", "sources": ["A", "B"]}]}, srcs)
        self.assertEqual(len(out), 1)

    def test_a_verdict_must_be_in_the_named_companys_own_source(self):  # "Infosys … behind" when only BEL was
        srcs = {"INFY": {"kind": "company", "label": "Infosys Ltd", "ref": "INFY", "text": "FY27 operating margin 20–22%, on pace."},
                "BEL": {"kind": "company", "label": "Bharat Electronics Ltd", "ref": "BEL", "text": "FY27 EBITDA margin 28%, behind."}}
        bad = {"text": "Infosys and Bharat Electronics are behind pace on FY27 margins.", "sources": ["INFY", "BEL"]}
        good = {"text": "Bharat Electronics is behind pace on its FY27 EBITDA margin.", "sources": ["BEL"]}
        self.assertEqual([x["text"] for x in ask.check({"sentences": [bad, good]}, srcs)], [good["text"]])

    def test_guidance_figures_near_a_results_word_arent_mislabelled(self):  # Infosys guide 1.5–3% -> 1–2%
        lines = {"revenue": [49244, 10.7, 3.1], "pat": [7838, 6.3]}
        self.assertFalse(ask.mislabelled("Infosys lowered its FY27 revenue growth target from 1.5–3% to 1–2%", lines))
        self.assertTrue(ask.mislabelled("revenue was ₹7,838 cr", lines))

    def test_model_glyph_slips_are_tidied(self):  # Max Healthcare: "⁷2,569 cr"; Cipla: "down -2.4%"
        self.assertEqual(ask.tidy("revenue of ⁷2,569 cr"), "revenue of ₹2,569 cr")
        self.assertEqual(ask.tidy("revenue was down -2.4% YoY"), "revenue was down 2.4% YoY")
        self.assertEqual(ask.tidy("growth of -2.4%"), "growth of -2.4%")
        self.assertEqual(ask.tidy("which is -2.4% lower YoY"), "which is 2.4% lower YoY")
        self.assertEqual(ask.tidy("revenue of \u20247,404 cr"), "revenue of ₹7,404 cr")


class FiscalCalendar(unittest.TestCase):
    def test_reported_quarter(self):
        self.assertEqual(fiscal.reported_quarter(fiscal.parse_label("Oct 2026")), (27, 2))
        self.assertEqual(fiscal.reported_quarter(fiscal.parse_label("May 2026")), (26, 4))
        self.assertEqual(fiscal.screener_quarter("Sep 2026"), (27, 2))


if __name__ == "__main__":
    unittest.main()
