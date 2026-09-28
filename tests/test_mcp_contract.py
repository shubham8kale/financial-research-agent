# tests/test_mcp_contract.py
#
# The MCP path under test for the first time.  The server runs in-process over
# the SDK's in-memory transport with a fake vectorstore and a real (synthetic)
# fact store injected through its lifespan, so tool discovery, the argument
# schemas and the observation format each tool returns are pinned without a
# model, a key or an index.  Every observation must parse through
# agent/observations.py into the same ids the direct tools produce; that is
# what keeps the API's citations identical whichever path answered.

import asyncio

import pytest
from langchain_core.documents import Document
from mcp.shared.memory import create_connected_server_and_client_session

from agent.observations import parse_observation
from ingestion import xbrl
from mcp_server import server as mcp_server
from retrieval.facts import FactStore
from test_facts import SUBMISSION

TOOLS = {"search_filings", "list_available_companies", "compare_companies", "lookup_financial_fact", "compute_metric"}


class _FakeVectorstore:
    def __init__(self):
        self.queries = []

    def similarity_search(self, query, k=5, filter=None):
        self.queries.append((query, k, filter))
        docs = [
            Document(page_content="Total net sales were $416,161 million in fiscal 2025.",
                     metadata={"ticker": "AAPL", "chunk_idx": 42, "source": "aapl.txt"}),
            Document(page_content="AWS net sales were $128,725 million in 2025.",
                     metadata={"ticker": "AMZN", "chunk_idx": 7, "source": "amzn.txt"}),
        ]
        if filter:
            docs = [d for d in docs if d.metadata["ticker"] == filter["ticker"]]
        return docs[:k]


@pytest.fixture
def store(tmp_path):
    filings = tmp_path / "filings" / "TEST" / "10-K" / "0000000001-25-000001"
    filings.mkdir(parents=True)
    (filings / "full-submission.txt").write_text(SUBMISSION, encoding="utf-8")
    db = tmp_path / "facts.sqlite"
    xbrl.build_facts_db(filings_dir=tmp_path / "filings", db_path=db)
    return FactStore(db)


@pytest.fixture
def served(monkeypatch, store):
    """The MCP server with its lifespan pointed at fakes; yields (vectorstore, run) where run(coro) drives a client."""
    vs = _FakeVectorstore()
    monkeypatch.setattr(mcp_server, "build_vectorstore", lambda: vs)
    monkeypatch.setattr(mcp_server, "FactStore", lambda: store)
    for var in ("RETRIEVAL_MODE", "RETRIEVAL_RERANK", "RETRIEVAL_TICKER_FILTER", "RETRIEVAL_K", "RETRIEVAL_FETCH_K"):
        monkeypatch.delenv(var, raising=False)

    def run(fn):
        async def main():
            async with create_connected_server_and_client_session(mcp_server.mcp) as client:
                return await fn(client)
        return asyncio.run(main())
    return vs, run


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def test_discovery_lists_the_five_tools_with_their_schemas(served):
    _, run = served

    async def fn(client):
        return (await client.list_tools()).tools
    tools = {t.name: t for t in run(fn)}
    assert set(tools) == TOOLS
    assert tools["search_filings"].inputSchema["required"] == ["query"]
    assert set(tools["compare_companies"].inputSchema["required"]) == {"question", "tickers"}
    fact = tools["lookup_financial_fact"].inputSchema
    assert set(fact["required"]) == {"ticker", "concept"} and {"fiscal_year", "segment"} <= set(fact["properties"])
    assert set(tools["compute_metric"].inputSchema["required"]) == {"operation", "a", "b"}
    assert tools["search_filings"].annotations.readOnlyHint is True


def test_search_and_compare_return_parseable_chunk_observations(served):
    vs, run = served

    async def fn(client):
        search = await client.call_tool("search_filings", {"query": "Apple net sales"})
        compare = await client.call_tool("compare_companies", {"question": "net sales", "tickers": "AAPL, AMZN"})
        return _text(search), _text(compare)
    search, compare = run(fn)
    assert [c.chunk_id for c in parse_observation(search)] == ["AAPL_10K_chunk_42", "AMZN_10K_chunk_7"]
    assert [(c.ticker, c.chunk_id) for c in parse_observation(compare)] == [
        ("AAPL", "AAPL_10K_chunk_42"), ("AMZN", "AMZN_10K_chunk_7")]
    # k comes from the retriever's configuration (default 5), the same as the direct tools
    assert vs.queries[0][1] == 5 and vs.queries[1][2] == {"ticker": "AAPL"}


def test_fact_and_calculator_observations_carry_fact_and_calc_ids(served):
    _, run = served

    async def fn(client):
        fact = await client.call_tool("lookup_financial_fact", {"ticker": "TEST", "concept": "revenue"})
        calc = await client.call_tool("compute_metric", {"operation": "pct_change", "a": 1234.0, "b": 1000.0})
        listing = await client.call_tool("list_available_companies", {})
        return _text(fact), _text(calc), _text(listing)
    fact, calc, listing = run(fn)
    facts = parse_observation(fact)
    assert facts and facts[0].kind == "fact" and facts[0].chunk_id.startswith("TEST_10K_fact_")
    assert "$1,234 million" in fact
    calcs = parse_observation(calc)
    assert calcs and calcs[0].kind == "calc" and calcs[0].chunk_id == "calc_pct_change" and "23.40" in calc
    assert "AAPL" in listing and "MSFT" in listing


def test_transport_security_names_the_hosts_it_is_reached_by():
    assert mcp_server.mcp.settings.transport_security.enable_dns_rebinding_protection is True
    hosts = mcp_server.mcp.settings.transport_security.allowed_hosts
    assert {"localhost", "127.0.0.1", "mcp-server"} <= set(hosts) and "mcp-server:*" in hosts
