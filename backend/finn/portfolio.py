"""The demo portfolio and the ad-hoc research set.

Chosen to span sectors, sizes and guidance cultures. A promise tracker is only
interesting if some companies guide hard and revise often, some guide on
milestones (capacity, stores, beds), and some barely guide at all, so the
honest "no hard promises" state shows up too. The `why` notes are selection
hypotheses to check against the transcripts, not facts the product relies on.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    symbol: str  # NSE symbol; Yahoo ticker is f"{symbol}.NS"
    name: str
    sector: str
    size: str  # large | mid | small
    why: str
    screener_slug: str | None = None  # when it differs from the NSE symbol

    @property
    def slug(self) -> str:
        return self.screener_slug or self.symbol


PORTFOLIO: list[Company] = [
    Company("INFY", "Infosys", "IT services", "large",
            "Annual revenue and margin bands, revised every quarter: raised/cut/narrowed."),
    Company("LT", "Larsen & Toubro", "Capital goods", "large",
            "Guides order inflow, revenue growth and core margin each year."),
    Company("BAJFINANCE", "Bajaj Finance", "NBFC", "large",
            "AUM growth and credit-cost guidance, revised mid-year."),
    Company("HDFCBANK", "HDFC Bank", "Private bank", "large",
            "Post-merger glide paths (loan/deposit growth) rather than point guidance."),
    Company("ETERNAL", "Eternal", "Consumer internet", "large",
            "Milestone targets (store counts) and profitability ambitions."),
    Company("DIXON", "Dixon Technologies", "Electronics manufacturing", "mid",
            "Revenue targets tied to new capacity and PLI timelines."),
    Company("ULTRACEMCO", "UltraTech Cement", "Cement", "large",
            "Capacity milestones with dates: the purest milestone promises."),
    Company("M&M", "Mahindra & Mahindra", "Autos", "large",
            "Segment growth and margin ambitions; tests segment-level verifiability."),
    Company("CIPLA", "Cipla", "Pharma", "large",
            "EBITDA margin band guidance."),
    Company("HINDUNILVR", "Hindustan Unilever", "FMCG", "large",
            "Mostly soft, directional commentary: the honest 'few hard promises' case."),
    Company("TATAPOWER", "Tata Power", "Power & renewables", "large",
            "Capacity addition and capex plans."),
    Company("BEL", "Bharat Electronics", "Defence PSU", "large",
            "Order inflow, revenue growth and margin guidance."),
    Company("MAXHEALTH", "Max Healthcare", "Hospitals", "mid",
            "Bed-addition milestones with timelines."),
    Company("TRENT", "Trent", "Retail", "large",
            "Store expansion; growth commentary."),
    Company("TATASTEEL", "Tata Steel", "Metals", "large",
            "Volume, cost-reduction and deleveraging promises."),
    Company("AARTIIND", "Aarti Industries", "Specialty chemicals", "mid",
            "EBITDA targets and capex timelines; useful missed/moved cases."),
    Company("KAYNES", "Kaynes Technology", "Electronics manufacturing", "mid",
            "Revenue growth guidance from a younger, fast-growing company."),
    Company("PRAJIND", "Praj Industries", "Engineering", "small",
            "Smaller company with growth and margin commentary."),
]

# Pull-side demo set, deliberately outside the portfolio.
RESEARCH: list[Company] = [
    Company("HCLTECH", "HCLTech", "IT services", "large",
            "Second IT guider: compare promise records side by side with Infosys."),
    Company("POLYCAB", "Polycab India", "Cables & wires", "large",
            "Multi-year revenue targets."),
    Company("PIIND", "PI Industries", "Agrochemicals", "mid",
            "Growth guidance that has been revised."),
    Company("INDIGO", "InterGlobe Aviation", "Airlines", "large",
            "Capacity (ASK) guidance every quarter."),
]

# The three companies used for the extraction test and the gold eval set.
SPIKE = ["INFY", "LT", "AARTIIND"]


def by_symbol(symbol: str) -> Company | None:
    for c in PORTFOLIO + RESEARCH:
        if c.symbol == symbol:
            return c
    return None
