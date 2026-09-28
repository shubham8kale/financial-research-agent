# tests/test_tool_wiring.py
#
# The agent's retrieval tools now call retrieval/retriever.py instead of the
# vector store directly.  These tests pin the contract that matters at the
# seam: whatever the configured retriever returns, the tool observation is
# laid out exactly as before — "[n] ticker=... chunk_idx=..." for
# search_filings, "=== TICKER ===" sections for compare_companies — and the
# shared parser in agent/observations.py reads the chunk ids back out of it.
# If that round trip breaks, citations in the API and contexts in the eval
# harness break with it.

import pytest
from langchain_core.documents import Document

import retrieval.retriever as retriever_mod
from agent.financial_agent import compare_companies, search_filings
from agent.observations import parse_observation
from retrieval.retriever import RetrievalConfig, Retriever


class FakeVectorstore:
    def __init__(self):
        self.calls = []
        self.docs = [
            Document(page_content="Total net sales were $416,161 million.\nSecond line.",
                     metadata={"ticker": "AAPL", "chunk_idx": 395}),
            Document(page_content="Azure revenue grew.", metadata={"ticker": "MSFT", "chunk_idx": 146}),
        ]

    def similarity_search(self, query, k=5, filter=None):
        self.calls.append({"query": query, "k": k, "filter": filter})
        docs = self.docs if not filter else [d for d in self.docs if d.metadata["ticker"] == filter["ticker"]]
        return docs[:k]


@pytest.fixture
def fake_vs(monkeypatch):
    vs = FakeVectorstore()
    monkeypatch.setattr(retriever_mod, "_default_retriever", Retriever(RetrievalConfig(), vectorstore=vs))
    yield vs
    monkeypatch.setattr(retriever_mod, "_default_retriever", None)


def test_search_filings_observation_round_trips_through_the_parser(fake_vs):
    obs = search_filings.invoke({"query": "apple net sales"})
    assert fake_vs.calls == [{"query": "apple net sales", "k": 5, "filter": None}]
    assert obs.startswith("[1] ticker=AAPL  chunk_idx=395\n    Total net sales were $416,161 million. Second line.")
    assert [c.chunk_id for c in parse_observation(obs)] == ["AAPL_10K_chunk_395", "MSFT_10K_chunk_146"]


def test_compare_companies_filters_per_ticker_and_round_trips(fake_vs):
    obs = compare_companies.invoke({"question": "cloud revenue", "tickers": "aapl, MSFT, META"})
    assert [c["filter"] for c in fake_vs.calls] == [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "META"}]
    assert "=== AAPL ===" in obs and "=== META ===\nNo results found for this ticker." in obs
    assert [c.chunk_id for c in parse_observation(obs)] == ["AAPL_10K_chunk_395", "MSFT_10K_chunk_146"]


def test_search_filings_reports_no_results(fake_vs):
    fake_vs.docs = []
    assert search_filings.invoke({"query": "nothing"}) == "No results found."


def test_get_retriever_reads_env_once(monkeypatch):
    monkeypatch.setattr(retriever_mod, "_default_retriever", None)
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("RETRIEVAL_RERANK", "1")
    r = retriever_mod.get_retriever()
    assert r.config.mode == "hybrid" and r.config.rerank is True
    monkeypatch.setenv("RETRIEVAL_MODE", "dense")
    assert retriever_mod.get_retriever() is r          # built once per process
    monkeypatch.setattr(retriever_mod, "_default_retriever", None)
