# retrieval/facts.py
#
# PURPOSE
# -------
# Exact lookup of tagged financial facts, and deterministic arithmetic over
# them.  This is the structured half of the agent: where a question asks for
# a headline figure, the answer comes from the XBRL fact table
# (ingestion/xbrl.py) with its concept, period and unit attached, not from a
# passage of prose the model then has to read a year off.
#
# THREE RULES THE TOOLS ENFORCE
# -----------------------------
# 1. The fiscal year is a filter, not a choice.  A lookup with a year returns
#    that year; a lookup without one returns the most recent year the filing
#    reports, and says so.  The prior-year figure is never the default.
# 2. The resolver never guesses silently.  "revenue" maps to the handful of
#    US-GAAP concepts companies use for it; anything the synonym table does not
#    know is matched against concept names and the CANDIDATES are returned to
#    the agent, which picks.  An empty result is an empty result.
# 3. The model never does arithmetic.  compute() takes the operation and the
#    operands and returns the number with the formula it used.

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from ingestion.xbrl import FACTS_DB

# Plain-language names -> the US-GAAP concepts that carry them, most common first.
CONCEPT_SYNONYMS: dict[str, list[str]] = {
    "revenue": ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:Revenues",
                "us-gaap:SalesRevenueNet"],
    "net income": ["us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"],
    "operating income": ["us-gaap:OperatingIncomeLoss"],
    "gross profit": ["us-gaap:GrossProfit"],
    "cost of revenue": ["us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold"],
    "research and development": ["us-gaap:ResearchAndDevelopmentExpense"],
    "selling general and administrative": ["us-gaap:SellingGeneralAndAdministrativeExpense"],
    "income tax": ["us-gaap:IncomeTaxExpenseBenefit"],
    "effective tax rate": ["us-gaap:EffectiveIncomeTaxRateContinuingOperations"],
    "eps diluted": ["us-gaap:EarningsPerShareDiluted"],
    "eps basic": ["us-gaap:EarningsPerShareBasic"],
    "total assets": ["us-gaap:Assets"],
    "total liabilities": ["us-gaap:Liabilities"],
    "stockholders equity": ["us-gaap:StockholdersEquity",
                            "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["us-gaap:CashAndCashEquivalentsAtCarryingValue"],
    "long-term debt": ["us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebt"],
    "operating cash flow": ["us-gaap:NetCashProvidedByUsedInOperatingActivities"],
    "capital expenditures": ["us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"],
    "share repurchases": ["us-gaap:PaymentsForRepurchaseOfCommonStock"],
    "dividends paid": ["us-gaap:PaymentsOfDividends", "us-gaap:PaymentsOfDividendsCommonStock"],
    "depreciation and amortization": ["us-gaap:DepreciationDepletionAndAmortization",
                                      "us-gaap:DepreciationAmortizationAndAccretionNet"],
    "stock-based compensation": ["us-gaap:ShareBasedCompensation", "us-gaap:AllocatedShareBasedCompensationExpense"],
    "goodwill": ["us-gaap:Goodwill"],
    "inventory": ["us-gaap:InventoryNet"],
    "accounts receivable": ["us-gaap:AccountsReceivableNetCurrent"],
    "interest expense": ["us-gaap:InterestExpense", "us-gaap:InterestExpenseNonoperating"],
    "shares outstanding": ["dei:EntityCommonStockSharesOutstanding", "us-gaap:CommonStockSharesOutstanding"],
    "weighted average diluted shares": ["us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"],
}

# Alternative phrasings folded into the synonym keys above.
_ALIASES: dict[str, str] = {
    "revenues": "revenue", "total revenue": "revenue", "total revenues": "revenue", "net sales": "revenue",
    "total net sales": "revenue", "sales": "revenue", "net revenue": "revenue", "net revenues": "revenue",
    "turnover": "revenue",
    "profit": "net income", "net earnings": "net income", "earnings": "net income", "net profit": "net income",
    "income from operations": "operating income", "operating profit": "operating income",
    "gross margin": "gross profit",
    "cost of sales": "cost of revenue", "cost of goods sold": "cost of revenue", "cogs": "cost of revenue",
    "r&d": "research and development", "rd": "research and development",
    "sg&a": "selling general and administrative", "sga": "selling general and administrative",
    "income taxes": "income tax", "provision for income taxes": "income tax", "tax expense": "income tax",
    "diluted eps": "eps diluted", "earnings per share": "eps diluted", "eps": "eps diluted",
    "basic eps": "eps basic",
    "assets": "total assets", "liabilities": "total liabilities",
    "shareholders equity": "stockholders equity", "equity": "stockholders equity", "total equity": "stockholders equity",
    "cash and cash equivalents": "cash",
    "debt": "long-term debt", "long term debt": "long-term debt",
    "cash from operations": "operating cash flow", "cash flow from operations": "operating cash flow",
    "net cash provided by operating activities": "operating cash flow",
    "capex": "capital expenditures", "purchases of property and equipment": "capital expenditures",
    "buybacks": "share repurchases", "repurchases": "share repurchases", "stock repurchases": "share repurchases",
    "dividends": "dividends paid",
    "d&a": "depreciation and amortization", "depreciation": "depreciation and amortization",
    "sbc": "stock-based compensation", "share-based compensation": "stock-based compensation",
    "receivables": "accounts receivable", "inventories": "inventory",
}


# Segment names people use -> the token that appears in the member name.
_SEGMENT_ALIASES: dict[str, str] = {
    "aws": "amazonwebservices", "amazon web services": "amazonwebservices",
    "fba": "familyofapps", "family of apps": "familyofapps", "reality labs": "realitylabs",
    "google services": "googleservices", "google cloud": "googlecloud", "other bets": "otherbets",
    "intelligent cloud": "intelligentcloud", "productivity and business processes": "productivityandbusinessprocesses",
    "more personal computing": "morepersonalcomputing", "iphone": "iphone", "mac": "mac", "ipad": "ipad",
    "wearables": "wearableshomeandaccessories", "services": "service", "north america": "northamerica",
    "international": "international", "americas": "americas", "europe": "europe", "greater china": "greaterchina",
    "japan": "japan", "rest of asia pacific": "restofasiapacific",
}


def _segment_token(segment: str) -> str:
    key = _norm(segment)
    return _SEGMENT_ALIASES.get(key, key.replace(" ", ""))


def segment_matches(dims: str, token: str) -> bool:
    """True if *token* appears in the MEMBER of any dimension in *dims* ("axis=member;...").

    The member is what names a segment ("us-gaap:ServiceMember"); the axis
    names the breakdown ("srt:ProductOrServiceAxis").  Matching the whole
    string let the token "service" hit every product row through the axis
    name, so a lookup for Apple's services revenue returned Products first.
    """
    for pair in (dims or "").split(";"):
        _, _, member = pair.partition("=")
        if token and token in member.split(":")[-1].lower():
            return True
    return False


def split_segment(concept_query: str) -> tuple[str, str | None]:
    """'Google Cloud revenue' -> ('revenue', 'google cloud').

    The model often folds the segment into the concept phrase instead of using
    the segment parameter.  When a known segment alias appears in the phrase it
    is lifted out, longest alias first, and the rest is the concept.
    """
    q = _norm(concept_query)
    for alias in sorted(_SEGMENT_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", q):
            rest = re.sub(rf"\b{re.escape(alias)}\b", " ", q)
            rest = re.sub(r"\s+", " ", rest).strip()
            return (rest or "revenue"), alias
    return concept_query, None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9&]+", " ", (text or "").lower()).strip()


def _words(concept: str) -> str:
    """'us-gaap:OperatingIncomeLoss' -> 'operating income loss'."""
    local = concept.split(":", 1)[-1]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", local).lower()


@dataclass(frozen=True)
class FactRow:
    id: int
    ticker: str
    concept: str
    value: float
    raw_text: str
    unit: str
    scale: int
    period_type: str
    period_start: str | None
    period_end: str
    fiscal_year: int
    dims: str

    @property
    def fact_id(self) -> str:
        return f"{self.ticker}_10K_fact_{self.id}"

    def segment_label(self) -> str:
        if not self.dims:
            return "consolidated"
        parts = []
        for pair in self.dims.split(";"):
            axis, _, member = pair.partition("=")
            parts.append(f"{axis.split(':')[-1].replace('Axis', '')}={member.split(':')[-1].replace('Member', '')}")
        return "; ".join(parts)

    def formatted_value(self) -> str:
        u = self.unit.lower()
        if "usd" in u and "shares" in u:
            return f"${self.value:,.2f} per share"
        if "usd" in u:
            sign = "-" if self.value < 0 else ""
            v = abs(self.value)
            if v >= 1e6:
                whole = v == round(v / 1e6) * 1e6
                return f"{sign}${v / 1e6:,.0f} million" if whole else f"{sign}${v / 1e6:,.1f} million"
            return f"{sign}${v:,.0f}"
        if "shares" in u:
            return f"{self.value:,.0f} shares"
        if "pure" in u:
            return f"{self.value:g}" + ("" if abs(self.value) > 1.5 else f" ({self.value * 100:.1f}%)")
        return f"{self.value:,.4g} {self.unit}"

    def period_label(self) -> str:
        if self.period_type == "instant":
            return f"as of {self.period_end}"
        return f"FY{self.fiscal_year} ({self.period_start} to {self.period_end})"


class FactStore:
    """Read-only access to the XBRL fact table."""

    def __init__(self, db_path: Path = FACTS_DB):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"{self.db_path} not found — build it with `python -m ingestion.xbrl`")
        self._con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        self._con.row_factory = sqlite3.Row

    # ── introspection ─────────────────────────────────────────────────

    def tickers(self) -> list[str]:
        return [r[0] for r in self._con.execute("SELECT ticker FROM filings ORDER BY ticker")]

    def latest_fiscal_year(self, ticker: str) -> int | None:
        r = self._con.execute("SELECT fiscal_year_focus FROM filings WHERE ticker=?", (ticker.upper(),)).fetchone()
        return int(r[0]) if r and r[0] is not None else None

    def concepts(self, ticker: str) -> list[str]:
        return [r[0] for r in self._con.execute(
            "SELECT DISTINCT concept FROM facts WHERE ticker=? ORDER BY concept", (ticker.upper(),))]

    # ── resolving ────────────────────────────────────────────────────

    def resolve_concept(self, query: str, ticker: str) -> tuple[list[str], str]:
        """Concepts for *query* at *ticker*: (matches, how).

        how is "exact" (a concept name was given), "synonym" (known phrase),
        "search" (name match; several candidates possible) or "none".
        """
        q = (query or "").strip()
        available = set(self.concepts(ticker))
        if ":" in q:
            hit = next((c for c in available if c.lower() == q.lower()), None)
            return ([hit], "exact") if hit else ([], "none")
        key = _norm(q)
        key = _ALIASES.get(key, key)
        if key in CONCEPT_SYNONYMS:
            hits = [c for c in CONCEPT_SYNONYMS[key] if c in available]
            if hits:
                return hits, "synonym"
        tokens = [t for t in key.split() if len(t) > 2]
        if not tokens:
            return [], "none"
        scored = []
        for c in available:
            words = _words(c)
            n = sum(1 for t in tokens if t in words)
            if n:
                scored.append((n / len(tokens), -len(words), c))
        scored.sort(reverse=True)
        best = [c for score, _, c in scored if score >= max(0.5, scored[0][0] - 1e-9)][:8] if scored else []
        return best, ("search" if best else "none")

    # ── lookup ────────────────────────────────────────────────────────

    def lookup(self, ticker: str, concept_query: str, fiscal_year: int | None = None,
               segment: str | None = None, annual_only: bool = True, limit: int = 12) -> tuple[list[FactRow], dict]:
        """Facts for a plain-language concept at one company.

        Returns (rows, info).  info carries the resolved concepts, how they
        were resolved, the fiscal year actually used and whether it was
        defaulted, so the tool can say all of that to the agent.
        """
        ticker = ticker.upper()
        if segment is None:
            concept_query, segment = split_segment(concept_query)
        concepts, how = self.resolve_concept(concept_query, ticker)
        info = {"ticker": ticker, "concepts": concepts, "resolved_by": how,
                "fiscal_year": fiscal_year, "fiscal_year_defaulted": False, "segment": segment}
        if not concepts:
            return [], info
        if fiscal_year is None:
            fiscal_year = self.latest_fiscal_year(ticker)
            info["fiscal_year"], info["fiscal_year_defaulted"] = fiscal_year, True
        sql = ["SELECT * FROM facts WHERE ticker=? AND concept IN (%s) AND fiscal_year=?" % ",".join("?" * len(concepts))]
        args: list = [ticker, *concepts, fiscal_year]
        if annual_only:
            sql.append("AND (period_type='instant' OR days > 300)")
        if segment:
            # Coarse SQL prefilter on the whole dims string, then the exact test on
            # the member names in Python: the prefilter alone matched axis names.
            sql.append("AND lower(dims) LIKE ?")
            args.append(f"%{_segment_token(segment)}%")
        else:
            sql.append("AND dim_count = 0")
        sql.append("ORDER BY dim_count, days DESC, abs(value) DESC")
        rows = [self._row(r) for r in self._con.execute(" ".join(sql), args)]
        if segment:
            token = _segment_token(segment)
            rows = [r for r in rows if segment_matches(r.dims, token)]
        rows = rows[:limit]
        info["years_available"] = self.years_available(ticker, concepts)
        if not rows and not segment:
            # Nothing consolidated: offer the segment breakdown instead of silence.
            annual = "AND (period_type='instant' OR days > 300)" if annual_only else ""
            sql = ("SELECT * FROM facts WHERE ticker=? AND concept IN (%s) AND fiscal_year=? AND dim_count=1 "
                   "%s ORDER BY abs(value) DESC LIMIT ?") % (",".join("?" * len(concepts)), annual)
            rows = [self._row(r) for r in self._con.execute(sql, [ticker, *concepts, fiscal_year, limit])]
            info["note"] = "no consolidated value; segment values returned" if rows else None
        return rows, info

    def years_available(self, ticker: str, concepts: list[str]) -> list[int]:
        sql = "SELECT DISTINCT fiscal_year FROM facts WHERE ticker=? AND concept IN (%s) ORDER BY fiscal_year" % ",".join("?" * len(concepts))
        return [int(r[0]) for r in self._con.execute(sql, [ticker.upper(), *concepts])]

    def get(self, fact_id: int) -> FactRow | None:
        r = self._con.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r) -> FactRow:
        return FactRow(
            id=r["id"], ticker=r["ticker"], concept=r["concept"], value=r["value"], raw_text=r["raw_text"],
            unit=r["unit"], scale=r["scale"] or 0, period_type=r["period_type"], period_start=r["period_start"],
            period_end=r["period_end"], fiscal_year=r["fiscal_year"], dims=r["dims"] or "",
        )


# ── arithmetic ───────────────────────────────────────────────────────────────

OPERATIONS = {
    "difference": ("a - b", lambda a, b: a - b),
    "sum": ("a + b", lambda a, b: a + b),
    "ratio": ("a / b", lambda a, b: a / b),
    "pct_change": ("(a - b) / b * 100", lambda a, b: (a - b) / b * 100),
    "margin_pct": ("a / b * 100", lambda a, b: a / b * 100),
    "cagr_pct": ("((a / b) ** (1 / n) - 1) * 100 over n periods", None),
}


def compute(operation: str, a: float, b: float, n: int | None = None) -> dict:
    """Deterministic arithmetic.  Returns {"result", "formula", "operation"} or {"error"}."""
    op = (operation or "").strip().lower()
    if op not in OPERATIONS:
        return {"error": f"unknown operation {operation!r}; choose one of {sorted(OPERATIONS)}"}
    formula, fn = OPERATIONS[op]
    try:
        if op == "cagr_pct":
            if not n or n <= 0:
                return {"error": "cagr_pct needs n (number of periods) > 0"}
            if b == 0 or (a / b) <= 0:
                # A fractional power of a negative ratio is a complex number, not a growth rate.
                return {"error": "cagr_pct needs a and b of the same sign and non-zero (the ratio a/b must be positive)"}
            result = ((a / b) ** (1 / n) - 1) * 100
        else:
            result = fn(a, b)
    except ZeroDivisionError:
        return {"error": "division by zero: b is 0"}
    return {"operation": op, "a": a, "b": b, "n": n, "formula": formula, "result": result}


def format_fact_observation(rows: list[FactRow], info: dict, concept_query: str) -> str:
    """The text a fact-lookup tool returns: one "[n] ticker=… fact_id=…" block per fact.

    The header lines mirror the retrieval tools' "[n] ticker=… chunk_idx=…" so
    the shared parser (agent/observations.py) reads facts as citable sources
    and the eval harness scores them as contexts.
    """
    ticker = info["ticker"]
    if not info["concepts"]:
        return (f"No XBRL concept matches {concept_query!r} for {ticker}. Try a standard line item "
                "(revenue, net income, operating income, gross profit, diluted EPS, total assets, "
                "operating cash flow, capital expenditures) or a segment name, or search the filing text.")
    concepts = ", ".join(info["concepts"])
    fy = info["fiscal_year"]
    fy_note = (" (the most recent fiscal year in the filing; pass fiscal_year for another)"
               if info.get("fiscal_year_defaulted") else "")
    if not rows:
        seg = f" segment {info['segment']!r}" if info.get("segment") else ""
        return (f"Resolved {concept_query!r} to {concepts} for {ticker}, but no annual value is tagged for "
                f"fiscal year {fy}{seg}{fy_note}. The filing covers fiscal years "
                f"{', '.join(str(y) for y in info.get('years_available', []))}.")
    lines = [f"XBRL facts for {ticker}: {concept_query!r} resolved by {info['resolved_by']} to {concepts}; "
             f"fiscal year {fy}{fy_note}."]
    if info.get("note"):
        lines[0] += f" Note: {info['note']}."
    for i, r in enumerate(rows, start=1):
        lines.append(
            f"[{i}] ticker={r.ticker}  fact_id={r.id}\n"
            f"    {r.concept} | {r.period_label()} | {r.formatted_value()} | {r.segment_label()} | "
            f"tagged \"{r.raw_text}\" x10^{r.scale} {r.unit} | source: {r.ticker} 10-K inline XBRL"
        )
    return "\n\n".join(lines)


_default_store: FactStore | None = None
_default_store_lock = threading.Lock()


def get_fact_store() -> FactStore:
    """The process-wide fact store; one is opened even when several threads ask first at once."""
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = FactStore()
    return _default_store
