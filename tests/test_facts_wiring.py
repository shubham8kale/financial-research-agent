# tests/test_facts_wiring.py
#
# The seam between the fact tools and everything that reads their output:
# the shared observation parser (facts are citable, calculations are not,
# neither is an index chunk), the eval harness's context prefixes, the API's
# citation shaping, and the two @tool functions themselves against a fact
# store built from the synthetic filing in tests/test_facts.py.

import pytest

import retrieval.facts as facts_mod
from agent.financial_agent import compute_metric, lookup_financial_fact
from agent.observations import parse_observation, retrieved_chunk_ids
from api.main import _chunk_idx_of, _parse_tool_content
from eval import run_eval
from ingestion import xbrl
from retrieval.facts import FactStore
from tests.test_facts import SUBMISSION

FACT_OBS = (
    "XBRL facts for AAPL: 'total net sales' resolved by synonym to us-gaap:Revenues; fiscal year 2025.\n"
    "\n"
    "[1] ticker=AAPL  fact_id=123\n"
    "    us-gaap:Revenues | FY2025 (2024-09-29 to 2025-09-27) | $416,161 million | consolidated | source: AAPL 10-K inline XBRL\n"
    "\n"
    "[2] ticker=AAPL  fact_id=124\n"
    "    us-gaap:Revenues | FY2024 (2023-10-01 to 2024-09-28) | $391,035 million | consolidated\n"
)
CALC_OBS = "[1] calc=pct_change\n    (a - b) / b * 100 with a=416,161, b=391,035 = 6.43\n"
CHUNK_OBS = "[1] ticker=AAPL  chunk_idx=395\n    Total net sales were $416,161 million.\n"


def test_parser_reads_facts_and_calculations_as_their_own_kinds():
    facts = parse_observation(FACT_OBS)
    assert [(c.kind, c.chunk_id, c.label) for c in facts] == [
        ("fact", "AAPL_10K_fact_123", "XBRL fact 123"), ("fact", "AAPL_10K_fact_124", "XBRL fact 124")]
    assert facts[0].text.startswith("us-gaap:Revenues | FY2025")
    calc = parse_observation(CALC_OBS)
    assert [(c.kind, c.chunk_id, c.label, c.ticker) for c in calc] == [("calc", "calc_pct_change", "calculator pct_change", "")]
    assert calc[0].text.endswith("= 6.43")


def test_only_index_chunks_count_as_retrieved():
    assert retrieved_chunk_ids([FACT_OBS, CALC_OBS, CHUNK_OBS]) == ["AAPL_10K_chunk_395"]


def test_harness_contexts_carry_fact_and_calculator_provenance():
    ctx = run_eval.split_contexts([FACT_OBS, CALC_OBS, CHUNK_OBS])
    assert ctx[0].startswith("[AAPL 10-K, XBRL fact 123] us-gaap:Revenues")
    assert ctx[2].startswith("[calculator pct_change] (a - b) / b * 100")
    assert ctx[3].startswith("[AAPL 10-K, chunk 395] Total net sales")


def test_api_cites_facts_but_not_calculations():
    sources = _parse_tool_content(FACT_OBS + "\n" + CALC_OBS + "\n" + CHUNK_OBS)
    assert [s.source_file for s in sources] == ["AAPL_10K_fact_123", "AAPL_10K_fact_124", "AAPL_10K_chunk_395"]
    assert _chunk_idx_of("AAPL_10K_fact_123") == "fact 123"
    assert _chunk_idx_of("AAPL_10K_chunk_395") == "395"
    assert _chunk_idx_of("calc_pct_change") == ""


@pytest.fixture
def fake_store(tmp_path, monkeypatch):
    filings = tmp_path / "filings" / "TEST" / "10-K" / "0000000001-25-000001"
    filings.mkdir(parents=True)
    (filings / "full-submission.txt").write_text(SUBMISSION, encoding="utf-8")
    db = tmp_path / "facts.sqlite"
    xbrl.build_facts_db(filings_dir=tmp_path / "filings", db_path=db)
    store = FactStore(db)
    monkeypatch.setattr(facts_mod, "_default_store", store)
    yield store
    monkeypatch.setattr(facts_mod, "_default_store", None)


def test_lookup_tool_returns_parseable_fact_observations(fake_store):
    obs = lookup_financial_fact.invoke({"ticker": "test", "concept": "total net sales"})
    assert obs.startswith("XBRL facts for TEST: 'total net sales' resolved by synonym to us-gaap:Revenues; fiscal year 2025 (the most recent")
    parsed = parse_observation(obs)
    assert len(parsed) == 1 and parsed[0].kind == "fact" and "$1,234 million" in parsed[0].text
    obs = lookup_financial_fact.invoke({"ticker": "TEST", "concept": "revenue", "fiscal_year": 2024})
    assert "$1,000 million" in obs and "most recent" not in obs
    obs = lookup_financial_fact.invoke({"ticker": "TEST", "concept": "revenue", "segment": "widget"})
    assert "$500 million" in obs and "ProductOrService=Widget" in obs


def test_lookup_tool_explains_misses_instead_of_guessing(fake_store):
    assert lookup_financial_fact.invoke({"ticker": "TEST", "concept": "wombat population"}).startswith(
        "No XBRL concept matches 'wombat population' for TEST.")
    obs = lookup_financial_fact.invoke({"ticker": "TEST", "concept": "revenue", "fiscal_year": 2019})
    assert "no annual value is tagged for fiscal year 2019" in obs and "2024, 2025" in obs
    assert parse_observation(obs) == []


def test_lookup_tool_without_database_points_to_search(monkeypatch, tmp_path):
    monkeypatch.setattr(facts_mod, "_default_store", None)
    monkeypatch.setattr(facts_mod, "FACTS_DB", tmp_path / "missing.sqlite")
    monkeypatch.setattr(facts_mod.FactStore.__init__, "__defaults__", (tmp_path / "missing.sqlite",))
    out = lookup_financial_fact.invoke({"ticker": "AAPL", "concept": "revenue"})
    assert out.startswith("Fact database unavailable")
    monkeypatch.setattr(facts_mod, "_default_store", None)


def test_compute_tool_returns_a_calculator_observation():
    out = compute_metric.invoke({"operation": "pct_change", "a": 416161, "b": 391035})
    assert out.startswith("[1] calc=pct_change\n    (a - b) / b * 100 with a=416,161, b=391,035 = 6.43")
    assert parse_observation(out)[0].kind == "calc"
    assert compute_metric.invoke({"operation": "ratio", "a": 1, "b": 0}).startswith("Error: division by zero")
