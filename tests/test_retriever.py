# tests/test_retriever.py
#
# Unit tests for the configurable retriever (upgrade 2): rank fusion, BM25
# tokenisation and search, ticker inference, and the Retriever's use of each
# switch on RetrievalConfig.  Dense search, the BM25 index and the reranker
# are injected as fakes, so nothing here needs the index, a model download or
# the network — but rank_bm25 itself IS exercised on a four-document corpus,
# because its scoring is what the hybrid mode fuses.
#
# The behaviours pinned are the ones an ablation would silently get wrong:
# fetch_k not reaching the vector store, fusion ignoring a weight, the ticker
# filter firing on a two-company question, a reranker that reorders but
# forgets to truncate.

import pytest
from langchain_core.documents import Document

from retrieval import tickers
from retrieval.retriever import RetrievalConfig, Retriever, retrieve, rrf_fuse
from retrieval.sparse import BM25Index, SparseHit, tokenize


# ── fakes ────────────────────────────────────────────────────────────────────

def _doc(ticker, idx, text):
    return Document(page_content=text, metadata={"ticker": ticker, "chunk_idx": idx})


class FakeVectorstore:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def similarity_search(self, query, k=5, filter=None):
        self.calls.append({"query": query, "k": k, "filter": filter})
        docs = self.docs
        if filter:
            docs = [d for d in docs if d.metadata["ticker"] == filter["ticker"]]
        return docs[:k]


class FakeBM25:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, k, ticker=None):
        self.calls.append({"query": query, "k": k, "ticker": ticker})
        hits = [h for h in self.hits if ticker is None or h.ticker == ticker]
        return hits[:k]


class ReversingReranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, candidates, k):
        self.calls.append((query, [c.chunk_id for c in candidates], k))
        return list(reversed(candidates))[:k]


DENSE_DOCS = [_doc("AAPL", 1, "a1"), _doc("AAPL", 2, "a2"), _doc("MSFT", 3, "m3"), _doc("AAPL", 4, "a4")]
SPARSE_HITS = [
    SparseHit("MSFT_10K_chunk_3", "MSFT", 3, "m3", 9.0, 1),
    SparseHit("AAPL_10K_chunk_9", "AAPL", 9, "a9", 8.0, 2),
    SparseHit("AAPL_10K_chunk_1", "AAPL", 1, "a1", 7.0, 3),
]


# ── fusion ───────────────────────────────────────────────────────────────────

def test_rrf_fuse_scores_and_orders():
    fused = rrf_fuse([["a", "b"], ["b", "c"]], rrf_k=60)
    ids = [cid for cid, _ in fused]
    assert ids == ["b", "a", "c"]                      # b appears in both lists
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61) and scores["c"] == pytest.approx(1 / 62)


def test_rrf_fuse_respects_weights_and_ties_by_first_seen():
    fused = rrf_fuse([["a", "b"], ["c", "d"]], weights=[1.0, 0.0])
    assert [cid for cid, _ in fused][:2] == ["a", "b"]  # sparse list weighted to nothing
    fused = rrf_fuse([["x"], ["y"]])                     # equal scores: first seen wins
    assert [cid for cid, _ in fused] == ["x", "y"]


# ── sparse ───────────────────────────────────────────────────────────────────

def test_tokenize_keeps_numbers_whole():
    assert tokenize("Net sales were $128,725 million, up 4.6% in FY2025.") == [
        "net", "sales", "were", "128,725", "million", "up", "4.6", "in", "fy2025",
    ]
    assert tokenize("") == []


def test_bm25_index_search_ranks_exact_tokens_and_filters_by_ticker(tmp_path):
    index = BM25Index(
        ["MSFT_10K_chunk_857", "MSFT_10K_chunk_1", "AAPL_10K_chunk_5", "AMZN_10K_chunk_2"],
        ["MSFT", "MSFT", "AAPL", "AMZN"],
        ["On October 13, 2023, we completed our acquisition of Activision Blizzard",
         "Microsoft cloud revenue grew",
         "Apple net sales by product iPhone Mac",
         "AWS 90,757 107,556 128,725"],
    )
    hits = index.search("when was the Activision Blizzard acquisition completed", k=3)
    assert hits[0].chunk_id == "MSFT_10K_chunk_857" and hits[0].rank == 1
    assert all(h.score > 0 for h in hits)
    # an exact numeric token finds the table row
    assert index.search("AWS 128,725", k=1)[0].chunk_id == "AMZN_10K_chunk_2"
    # ticker restriction removes every other company even when they score higher
    assert [h.ticker for h in index.search("Activision Apple", k=3, ticker="AAPL")] == ["AAPL"]
    assert index.search("zzzz", k=3) == []            # no matching token, no hits
    # persistence round trip
    path = index.save(tmp_path / "bm25.pkl")
    loaded = BM25Index.load(path)
    assert len(loaded) == 4 and loaded.search("Activision", k=1)[0].chunk_id == "MSFT_10K_chunk_857"


# ── ticker inference ─────────────────────────────────────────────────────────

def test_infer_tickers_by_alias_and_symbol():
    assert tickers.infer_tickers("Compare Apple and Microsoft cloud revenue") == ["AAPL", "MSFT"]
    assert tickers.infer_tickers("What was AWS net sales in 2025?") == ["AMZN"]
    assert tickers.infer_tickers("googl risk factors") == ["GOOGL"]
    assert tickers.infer_tickers("Which company reported the highest net income?") == []


def test_infer_single_ticker_refuses_to_guess():
    assert tickers.infer_single_ticker("Meta's headcount") == "META"
    assert tickers.infer_single_ticker("Apple versus Amazon") is None
    assert tickers.infer_single_ticker("total revenue") is None


# ── retriever ────────────────────────────────────────────────────────────────

def test_config_validation_and_env(monkeypatch):
    with pytest.raises(ValueError):
        RetrievalConfig(mode="magic")
    with pytest.raises(ValueError):
        RetrievalConfig(ticker_filter="always")
    for name in ("MODE", "K", "FETCH_K", "RRF_K", "DENSE_WEIGHT", "SPARSE_WEIGHT", "RERANK", "RERANK_MODEL", "TICKER_FILTER"):
        monkeypatch.delenv(f"RETRIEVAL_{name}", raising=False)
    assert RetrievalConfig.from_env() == RetrievalConfig()       # shipped behaviour by default
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("RETRIEVAL_RERANK", "true")
    monkeypatch.setenv("RETRIEVAL_FETCH_K", "50")
    monkeypatch.setenv("RETRIEVAL_TICKER_FILTER", "inferred")
    cfg = RetrievalConfig.from_env()
    assert (cfg.mode, cfg.rerank, cfg.fetch_k, cfg.ticker_filter) == ("hybrid", True, 50, "inferred")
    assert cfg.as_dict()["rerank_model"]


def test_dense_mode_fetches_exactly_k_unless_reranking():
    vs = FakeVectorstore(DENSE_DOCS)
    r = Retriever(RetrievalConfig(mode="dense", k=2, fetch_k=10), vectorstore=vs)
    out = r.retrieve("q")
    assert vs.calls == [{"query": "q", "k": 2, "filter": None}]   # shipped behaviour: k, not fetch_k
    assert [c.chunk_id for c in out] == ["AAPL_10K_chunk_1", "AAPL_10K_chunk_2"]
    assert [c.rank for c in out] == [1, 2]


def test_explicit_ticker_always_filters_and_inferred_filter_only_on_single_company():
    vs = FakeVectorstore(DENSE_DOCS)
    r = Retriever(RetrievalConfig(mode="dense", k=5, ticker_filter="inferred"), vectorstore=vs)
    r.retrieve("What were Apple's net sales?")
    assert vs.calls[-1]["filter"] == {"ticker": "AAPL"}
    r.retrieve("Compare Apple and Microsoft net sales")
    assert vs.calls[-1]["filter"] is None
    r.retrieve("anything", ticker="msft")                       # explicit wins, and is upper-cased
    assert vs.calls[-1]["filter"] == {"ticker": "MSFT"}
    none = Retriever(RetrievalConfig(mode="dense", k=5, ticker_filter="none"), vectorstore=vs)
    none.retrieve("What were Apple's net sales?")
    assert vs.calls[-1]["filter"] is None


def test_bm25_mode_uses_sparse_index():
    bm25 = FakeBM25(SPARSE_HITS)
    r = Retriever(RetrievalConfig(mode="bm25", k=2, fetch_k=3), bm25=bm25)
    out = r.retrieve("q", ticker="AAPL")
    assert bm25.calls == [{"query": "q", "k": 3, "ticker": "AAPL"}]
    assert [c.chunk_id for c in out] == ["AAPL_10K_chunk_9", "AAPL_10K_chunk_1"]


def test_hybrid_mode_fuses_both_sources():
    vs, bm25 = FakeVectorstore(DENSE_DOCS), FakeBM25(SPARSE_HITS)
    r = Retriever(RetrievalConfig(mode="hybrid", k=3, fetch_k=4, rrf_k=60), vectorstore=vs, bm25=bm25)
    out = [c.chunk_id for c in r.retrieve("q")]
    # AAPL_1 is rank 1 dense + rank 3 sparse; MSFT_3 is rank 3 dense + rank 1 sparse: both beat single-source ids
    assert set(out[:2]) == {"AAPL_10K_chunk_1", "MSFT_10K_chunk_3"}
    assert len(out) == 3
    # a chunk only BM25 found still carries its text
    r2 = Retriever(RetrievalConfig(mode="hybrid", k=4, fetch_k=4, dense_weight=0.0), vectorstore=vs, bm25=bm25)
    out2 = r2.retrieve("q")
    assert out2[0].chunk_id == "MSFT_10K_chunk_3" and out2[1].text == "a9"


def test_rerank_reorders_fetch_k_candidates_and_truncates_to_k():
    vs, rr = FakeVectorstore(DENSE_DOCS), ReversingReranker()
    r = Retriever(RetrievalConfig(mode="dense", k=2, fetch_k=4, rerank=True), vectorstore=vs, reranker=rr)
    out = r.retrieve("q")
    assert rr.calls[0][1] == ["AAPL_10K_chunk_1", "AAPL_10K_chunk_2", "MSFT_10K_chunk_3", "AAPL_10K_chunk_4"]
    assert [c.chunk_id for c in out] == ["AAPL_10K_chunk_4", "MSFT_10K_chunk_3"]
    assert [c.rank for c in out] == [1, 2]


def test_functional_retrieve_without_config_is_plain_dense():
    vs = FakeVectorstore(DENSE_DOCS)
    out = retrieve("q", 3, vectorstore=vs)
    assert vs.calls == [{"query": "q", "k": 3, "filter": None}]
    assert [c.chunk_id for c in out] == ["AAPL_10K_chunk_1", "AAPL_10K_chunk_2", "MSFT_10K_chunk_3"]
