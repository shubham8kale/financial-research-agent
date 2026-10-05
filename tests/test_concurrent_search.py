# tests/test_concurrent_search.py
#
# LangGraph's ToolNode runs the tool calls of ONE model step on worker threads.
# Before the batching upgrade only fact lookups had ever run that way in a
# committed run; search_filings and compare_companies (Chroma, the embedding
# model, the cross-encoder) had always run one at a time.  Measured on the real
# index, steady-state concurrent searches were identical to sequential ones, but
# the FIRST concurrent use of a cold process was not: six of eight threads died
# with "Could not connect to tenant default_tenant" because each built its own
# Chroma client.  The first half of this file pins the fix (every lazily built
# singleton is built exactly once under racing threads, with fakes, so it runs
# everywhere); the second half repeats the measurement against the real index
# and skips with its reason where there is none.

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import agent.financial_agent as fa
import retrieval.facts as facts_mod
import retrieval.retriever as retriever_mod
from retrieval.rerank import DEFAULT_RERANKER, CrossEncoderReranker

THREADS = 8


def race(fn, n=THREADS):
    """Call fn() from n threads released together; returns the results (exceptions are re-raised)."""
    barrier = threading.Barrier(n)

    def one(_):
        barrier.wait(timeout=10)
        return fn()

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one, range(n)))


class SlowBuild:
    """A constructor stand-in that takes long enough for every racing thread to be inside it at once."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self._lock:
            self.calls += 1
        time.sleep(0.05)
        return object()


# ── every lazily built singleton is built once under racing threads ─────────────────────────────────────────────

def test_the_vectorstore_is_built_once_when_threads_race_to_the_first_use(monkeypatch):
    build = SlowBuild()
    monkeypatch.setattr(fa, "_vectorstore", None)
    monkeypatch.setattr(fa, "build_vectorstore", build)
    results = race(fa._get_vectorstore)
    assert build.calls == 1 and len({id(r) for r in results}) == 1


def test_the_process_wide_retriever_is_built_once(monkeypatch):
    built = []

    def make(config):
        time.sleep(0.05)
        built.append(config)
        return object()

    monkeypatch.setattr(retriever_mod, "_default_retriever", None)
    monkeypatch.setattr(retriever_mod, "Retriever", make)
    results = race(retriever_mod.get_retriever)
    assert len(built) == 1 and len({id(r) for r in results}) == 1


def test_the_reranker_and_the_sparse_index_are_each_built_once_per_retriever(monkeypatch):
    reranker, bm25 = SlowBuild(), SlowBuild()
    monkeypatch.setattr("retrieval.rerank.CrossEncoderReranker", lambda model: reranker(model))
    monkeypatch.setattr("retrieval.sparse.load_or_build", bm25)
    retriever = retriever_mod.Retriever(retriever_mod.RetrievalConfig(), vectorstore=object())
    assert len({id(r) for r in race(lambda: retriever.reranker)}) == 1 and reranker.calls == 1
    assert len({id(r) for r in race(lambda: retriever.bm25)}) == 1 and bm25.calls == 1


def test_the_cross_encoder_model_is_loaded_once(monkeypatch):
    import sys
    import types

    loads = SlowBuild()
    fake = types.ModuleType("sentence_transformers")
    fake.CrossEncoder = loads
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    reranker = CrossEncoderReranker()
    models = race(reranker._load)
    assert loads.calls == 1 and len({id(m) for m in models}) == 1


def test_the_fact_store_is_opened_once(monkeypatch):
    opens = SlowBuild()
    monkeypatch.setattr(facts_mod, "_default_store", None)
    monkeypatch.setattr(facts_mod, "FactStore", opens)
    stores = race(facts_mod.get_fact_store)
    assert opens.calls == 1 and len({id(s) for s in stores}) == 1


def test_a_warm_singleton_does_not_touch_the_lock(monkeypatch):
    # the lock is for the first build only: a warm process must never queue behind it
    sentinel = object()
    monkeypatch.setattr(fa, "_vectorstore", sentinel)
    got = []
    caller = threading.Thread(target=lambda: got.append(fa._get_vectorstore()))
    with fa._vectorstore_lock:                       # held by "someone else"; a warm call must not wait for it
        caller.start()
        caller.join(timeout=2)                       # a regression fails here instead of hanging the suite
        queued = caller.is_alive()
    caller.join(timeout=2)
    assert not queued and got == [sentinel], "a warm call queued behind the lock"


# ── the real index ──────────────────────────────────────────────────────────────

def _real_index_or_skip():
    from ingestion.embedder import CHROMA_PERSIST_DIR, EMBEDDING_MODEL

    if not (CHROMA_PERSIST_DIR / "chroma.sqlite3").exists():
        pytest.skip(f"needs the built Chroma index at {CHROMA_PERSIST_DIR} (python -m ingestion.embedder); the unit "
                    "job in CI has none, the retrieval gate builds it")
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        pytest.skip("huggingface_hub is not importable")
    for model in (DEFAULT_RERANKER, EMBEDDING_MODEL if "/" in EMBEDDING_MODEL else f"sentence-transformers/{EMBEDDING_MODEL}"):
        if not isinstance(try_to_load_from_cache(model, "config.json"), str):
            pytest.skip(f"the model {model} is not in the local Hugging Face cache and this test does not download it")


QUERIES = ["Apple total net sales fiscal year 2025", "Microsoft Azure revenue growth",
           "Alphabet Google Cloud revenue 2025", "Amazon AWS operating income 2025"]


def test_concurrent_searches_on_the_real_index_equal_sequential_ones_from_a_cold_start(monkeypatch):
    _real_index_or_skip()
    monkeypatch.setenv("RETRIEVAL_RERANK", "true")
    monkeypatch.setenv("RETRIEVAL_FETCH_K", "50")
    monkeypatch.setenv("RETRIEVAL_TICKER_FILTER", "inferred")
    monkeypatch.setattr(fa, "_vectorstore", None)                   # a cold process: nothing built yet
    monkeypatch.setattr(retriever_mod, "_default_retriever", None)

    workers = len(QUERIES)
    barrier = threading.Barrier(workers)

    def search(query):
        barrier.wait(timeout=60)
        return fa.search_filings.invoke({"query": query})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        concurrent = list(pool.map(search, QUERIES))                # the first use of everything, all at once
    sequential = [fa.search_filings.invoke({"query": q}) for q in QUERIES]
    assert concurrent == sequential
    assert all(text.startswith("[1] ticker=") for text in concurrent)

    # steady state: the same calls again, interleaved, twice as many as threads
    barrier2 = threading.Barrier(workers)

    def search_again(query):
        barrier2.wait(timeout=60)
        return fa.search_filings.invoke({"query": query})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        again = list(pool.map(search_again, QUERIES))
    assert again == sequential


def test_concurrent_company_comparisons_on_the_real_index_equal_sequential_ones(monkeypatch):
    _real_index_or_skip()
    monkeypatch.setenv("RETRIEVAL_RERANK", "true")
    monkeypatch.setenv("RETRIEVAL_FETCH_K", "50")
    monkeypatch.setenv("RETRIEVAL_TICKER_FILTER", "inferred")
    monkeypatch.setattr(fa, "_vectorstore", None)
    monkeypatch.setattr(retriever_mod, "_default_retriever", None)

    calls = [{"question": q, "tickers": t} for q, t in (
        ("total net sales", "AAPL, MSFT"), ("cloud revenue growth", "MSFT, GOOGL"),
        ("employees", "AMZN, META"), ("net income", "AAPL, GOOGL"))]
    barrier = threading.Barrier(len(calls))

    def compare(args):
        barrier.wait(timeout=60)
        return fa.compare_companies.invoke(args)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        concurrent = list(pool.map(compare, calls))
    sequential = [fa.compare_companies.invoke(args) for args in calls]
    assert concurrent == sequential
    assert all("=== " in text for text in concurrent)
