# tests/test_facts.py
#
# The structured-facts layer (upgrade 3): ingestion/xbrl.py parses inline XBRL
# into SQLite, retrieval/facts.py looks facts up by plain-language concept and
# does the arithmetic.  A synthetic 10-K submission covers every tag shape the
# real filings showed — scale, sign, parentheses, fixed-zero, nested markup,
# per-share units, dimensional contexts, DEI markers, duplicates — so the
# parser is pinned without needing the 90 MB of real submissions.

import sqlite3

import pytest

from ingestion import xbrl
from retrieval import facts as facts_mod
from retrieval.facts import FactRow, FactStore, compute

IXBRL = """<html><body>
<ix:header><ix:hidden>
<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c-1"><span>2025</span></ix:nonNumeric>
<ix:nonNumeric name="dei:DocumentType" contextRef="c-1">10-K</ix:nonNumeric>
<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-1">Test Corp</ix:nonNumeric>
</ix:hidden><ix:resources>
<xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-2"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2023-10-01</xbrli:startDate><xbrli:endDate>2024-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-3"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2025-09-30</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="c-4"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">test:WidgetMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
<xbrli:unit id="usdPerShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
  <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
</ix:resources></ix:header>
<p>Revenue <ix:nonFraction name="us-gaap:Revenues" contextRef="c-1" unitRef="usd" scale="6" decimals="-6" format="ixt:num-dot-decimal">1,234</ix:nonFraction></p>
<p>Prior <ix:nonFraction name="us-gaap:Revenues" contextRef="c-2" unitRef="usd" scale="6">1,000</ix:nonFraction></p>
<p>Loss <ix:nonFraction name="us-gaap:NonoperatingIncomeExpense" contextRef="c-1" unitRef="usd" scale="6" sign="-">12</ix:nonFraction></p>
<p>Paren <ix:nonFraction name="us-gaap:OtherNonoperatingIncomeExpense" contextRef="c-1" unitRef="usd" scale="6">(7)</ix:nonFraction></p>
<p>Zero <ix:nonFraction name="us-gaap:Goodwill" contextRef="c-3" unitRef="usd" scale="6" format="ixt:fixed-zero">—</ix:nonFraction></p>
<p>Nested <ix:nonFraction name="us-gaap:EarningsPerShareDiluted" contextRef="c-1" unitRef="usdPerShare" scale="0" decimals="2"><span>7.46</span></ix:nonFraction></p>
<p>Segment <ix:nonFraction name="us-gaap:Revenues" contextRef="c-4" unitRef="usd" scale="6">500</ix:nonFraction></p>
<p>Duplicate <ix:nonFraction name="us-gaap:Revenues" contextRef="c-1" unitRef="usd" scale="6">1,234</ix:nonFraction></p>
<p>Orphan <ix:nonFraction name="us-gaap:Assets" contextRef="c-missing" unitRef="usd" scale="6">9</ix:nonFraction></p>
</body></html>"""

SUBMISSION = (
    "<SEC-DOCUMENT>x.txt\n<DOCUMENT>\n<TYPE>EX-21\n<SEQUENCE>2\n<FILENAME>ex21.htm\n<TEXT>\n<html>subsidiaries</html>\n</TEXT>\n</DOCUMENT>\n"
    "<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<FILENAME>test-10k.htm\n<TEXT>\n" + IXBRL + "\n</TEXT>\n</DOCUMENT>\n"
)


@pytest.fixture
def parsed():
    return xbrl.parse_inline_xbrl(xbrl.primary_document(SUBMISSION), "TEST", "test.txt")


def test_primary_document_picks_the_10k_not_the_exhibit():
    doc = xbrl.primary_document(SUBMISSION)
    assert "ix:nonFraction" in doc and "subsidiaries" not in doc
    with pytest.raises(ValueError):
        xbrl.primary_document("<DOCUMENT>\n<TYPE>EX-21\n<TEXT>x</TEXT>\n</DOCUMENT>")


def test_parser_reads_filing_metadata(parsed):
    filing, facts = parsed
    assert (filing.ticker, filing.cik, filing.fiscal_year_focus) == ("TEST", "0000000001", 2025)
    assert filing.registrant == "Test Corp" and filing.document_type == "10-K"
    assert filing.period_end == "2025-09-30" and filing.notes == []
    assert filing.n_facts == len(facts) == 7      # duplicate and orphan dropped


def test_parser_values_scale_sign_zero_nested_and_dims(parsed):
    _, facts = parsed
    by = {(f.concept, f.context_id): f for f in facts}
    rev = by[("us-gaap:Revenues", "c-1")]
    assert rev.value == 1_234_000_000 and rev.raw_text == "1,234" and rev.unit == "iso4217:USD"
    assert (rev.period_type, rev.period_start, rev.period_end, rev.days, rev.fiscal_year) == ("duration", "2024-10-01", "2025-09-30", 364, 2025)
    assert by[("us-gaap:Revenues", "c-2")].fiscal_year == 2024
    assert by[("us-gaap:NonoperatingIncomeExpense", "c-1")].value == -12_000_000          # sign="-"
    assert by[("us-gaap:OtherNonoperatingIncomeExpense", "c-1")].value == -7_000_000       # parentheses
    assert by[("us-gaap:Goodwill", "c-3")].value == 0.0 and by[("us-gaap:Goodwill", "c-3")].period_type == "instant"
    eps = by[("us-gaap:EarningsPerShareDiluted", "c-1")]
    assert eps.value == 7.46 and eps.unit == "iso4217:USD/xbrli:shares"                   # nested <span>, divide unit
    seg = by[("us-gaap:Revenues", "c-4")]
    assert seg.value == 500_000_000 and seg.dims == "srt:ProductOrServiceAxis=test:WidgetMember" and seg.dim_count == 1


@pytest.fixture
def store(tmp_path):
    filings = tmp_path / "filings" / "TEST" / "10-K" / "0000000001-25-000001"
    filings.mkdir(parents=True)
    (filings / "full-submission.txt").write_text(SUBMISSION, encoding="utf-8")
    db = tmp_path / "facts.sqlite"
    summary = xbrl.build_facts_db(filings_dir=tmp_path / "filings", db_path=db)
    assert [f.ticker for f in summary] == ["TEST"] and summary[0].n_facts == 7
    return FactStore(db)


def test_build_writes_both_tables(store):
    con = sqlite3.connect(store.db_path)
    assert con.execute("SELECT count(*) FROM facts").fetchone()[0] == 7
    assert con.execute("SELECT ticker, fiscal_year_focus, period_end FROM filings").fetchall() == [("TEST", 2025, "2025-09-30")]


def test_lookup_defaults_to_latest_year_and_says_so(store):
    rows, info = store.lookup("test", "total net sales")
    assert info["resolved_by"] == "synonym" and info["concepts"] == ["us-gaap:Revenues"]
    assert info["fiscal_year"] == 2025 and info["fiscal_year_defaulted"] is True
    assert [r.value for r in rows] == [1_234_000_000]
    assert rows[0].formatted_value() == "$1,234 million" and rows[0].period_label() == "FY2025 (2024-10-01 to 2025-09-30)"
    assert rows[0].segment_label() == "consolidated" and rows[0].fact_id.startswith("TEST_10K_fact_")


def test_lookup_respects_explicit_year_and_segment(store):
    rows, info = store.lookup("TEST", "revenue", fiscal_year=2024)
    assert info["fiscal_year_defaulted"] is False and [r.value for r in rows] == [1_000_000_000]
    rows, _ = store.lookup("TEST", "revenue", segment="widget")
    assert [r.value for r in rows] == [500_000_000] and rows[0].segment_label() == "ProductOrService=Widget"
    assert store.lookup("TEST", "revenue", fiscal_year=1999)[0] == []


def test_resolver_exact_search_and_none(store):
    assert store.resolve_concept("us-gaap:EarningsPerShareDiluted", "TEST") == (["us-gaap:EarningsPerShareDiluted"], "exact")
    concepts, how = store.resolve_concept("nonoperating income", "TEST")
    assert how == "search" and set(concepts) == {"us-gaap:NonoperatingIncomeExpense", "us-gaap:OtherNonoperatingIncomeExpense"}
    assert store.resolve_concept("wombat population", "TEST") == ([], "none")
    rows, info = store.lookup("TEST", "wombat population")
    assert rows == [] and info["resolved_by"] == "none"


def test_segment_alias_and_formatting():
    assert facts_mod._segment_token("AWS") == "amazonwebservices"
    assert facts_mod._segment_token("Intelligent Cloud") == "intelligentcloud"
    assert facts_mod.split_segment("Google Cloud revenue") == ("revenue", "google cloud")
    assert facts_mod.split_segment("AWS net sales") == ("net sales", "aws")
    assert facts_mod.split_segment("iPhone") == ("revenue", "iphone")
    assert facts_mod.split_segment("total net sales") == ("total net sales", None)
    neg = FactRow(3, "X", "us-gaap:NonoperatingIncomeExpense", -321_000_000, "321", "iso4217:USD", 6,
                  "duration", "2024-09-29", "2025-09-27", 2025, "")
    assert neg.formatted_value() == "-$321 million"
    row = FactRow(1, "X", "us-gaap:EarningsPerShareDiluted", 7.46, "7.46", "iso4217:USD/xbrli:shares", 0,
                  "duration", "2024-10-01", "2025-09-30", 2025, "")
    assert row.formatted_value() == "$7.46 per share"
    inst = FactRow(2, "X", "us-gaap:Assets", 359_241_000_000, "359,241", "iso4217:USD", 6, "instant", None, "2025-09-27", 2025, "")
    assert inst.formatted_value() == "$359,241 million" and inst.period_label() == "as of 2025-09-27"


def test_compute_is_deterministic_and_shows_its_formula():
    out = compute("pct_change", 416_161, 391_035)
    assert out["result"] == pytest.approx(6.4254, abs=1e-3) and out["formula"] == "(a - b) / b * 100"
    assert compute("margin_pct", 112_010, 416_161)["result"] == pytest.approx(26.9150, abs=1e-3)
    assert compute("difference", 5, 2)["result"] == 3 and compute("sum", 5, 2)["result"] == 7
    assert compute("cagr_pct", 121, 100, n=2)["result"] == pytest.approx(10.0)
    assert "error" in compute("ratio", 1, 0) and "error" in compute("magic", 1, 2) and "error" in compute("cagr_pct", 2, 1)


def test_missing_database_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        FactStore(tmp_path / "nope.sqlite")


# ── review issue 6: the segment filter and the CAGR guard ────────────────────

def test_segment_filter_matches_member_names_not_axis_names():
    tok = facts_mod._segment_token("services")
    assert tok == "service"
    assert facts_mod.segment_matches("srt:ProductOrServiceAxis=us-gaap:ServiceMember", tok)
    assert not facts_mod.segment_matches("srt:ProductOrServiceAxis=us-gaap:ProductMember", tok)
    assert not facts_mod.segment_matches("srt:ProductOrServiceAxis=aapl:IPhoneMember", tok)
    assert facts_mod.segment_matches("us-gaap:StatementBusinessSegmentsAxis=meta:RealityLabsMember",
                                     facts_mod._segment_token("reality labs"))
    assert not facts_mod.segment_matches("", "service")


def test_lookup_segment_does_not_match_through_the_axis_name(store):
    # the synthetic filing tags one product row on srt:ProductOrServiceAxis (test:WidgetMember)
    rows, info = store.lookup("TEST", "revenue", segment="widget")
    assert [r.value for r in rows] == [500e6] and info["segment"] == "widget"
    rows, _ = store.lookup("TEST", "revenue", segment="services")
    assert rows == [], "'service' matched the axis name ProductOrServiceAxis and returned the product row"


def test_cagr_refuses_a_non_positive_ratio():
    assert "error" in compute("cagr_pct", -5.0, 10.0, 3)
    assert "error" in compute("cagr_pct", 5.0, 0.0, 3)
    assert "error" in compute("cagr_pct", 5.0, -10.0, 3)
    assert abs(compute("cagr_pct", 121.0, 100.0, 2)["result"] - 10.0) < 1e-9
