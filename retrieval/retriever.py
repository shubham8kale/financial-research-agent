# retrieval/retriever.py
#
# PURPOSE
# -------
# The one place that turns a query into a ranked list of chunks, so that the
# evaluation harness and the agent tools retrieve through the same code and
# the same configuration.  Every retrieval technique is a field on
# RetrievalConfig, defaulting to the behaviour the system shipped with (dense
# similarity, top 5, no filter), so an ablation is a configuration change and
# a regression can be switched off without a code change.
#
# MODES
# -----
#   dense    ChromaDB cosine similarity over MiniLM embeddings (the original).
#   bm25     Exact-token BM25 over the same chunks (retrieval/sparse.py).
#   hybrid   Both, fused with weighted Reciprocal Rank Fusion:
#            score(d) = Σ_source w_source / (rrf_k + rank_source(d)).
#            RRF needs no score calibration between a cosine and a BM25 score,
#            which is why it is the fusion of choice when the two scales have
#            nothing in common.
# Any mode can then be re-ordered by a cross-encoder (rerank=True): fetch_k
# candidates are scored against the query jointly and the best k kept.
#
# TICKER FILTER
# -------------
# A caller may pass a ticker explicitly (compare_companies always does).  When
# it does not, ticker_filter decides: "none" searches everything, "inferred"
# restricts to the one company the query names (retrieval/tickers.py) and
# searches everything when it names zero or several.  "oracle" is the eval
# runner's label for passing the benchmark's ticker column explicitly; it is
# an upper bound the agent cannot reach on its own.

import os
from dataclasses import asdict, dataclass

from agent.observations import chunk_id
from retrieval.query_engine import TOP_K
from retrieval.rerank import DEFAULT_RERANKER
from retrieval.tickers import infer_single_ticker

MODES = ("dense", "bm25", "hybrid")
TICKER_FILTERS = ("none", "inferred", "oracle")


@dataclass(frozen=True)
class RetrievalConfig:
    """Everything that decides WHICH chunks come back for a query.

    Included in every results file's config hash, so two runs that retrieved
    differently can never share a hash.
    """
    mode: str = "dense"
    k: int = TOP_K                 # chunks returned
    fetch_k: int = 25              # candidates taken from each source before fusion / reranking
    rrf_k: int = 60                # RRF constant; larger flattens the rank curve
    dense_weight: float = 1.0
    sparse_weight: float = 1.0
    rerank: bool = False
    rerank_model: str = DEFAULT_RERANKER
    ticker_filter: str = "none"    # none | inferred | oracle

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.ticker_filter not in TICKER_FILTERS:
            raise ValueError(f"ticker_filter must be one of {TICKER_FILTERS}, got {self.ticker_filter!r}")

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_env(cls) -> "RetrievalConfig":
        """The shipped configuration, overridable per field by RETRIEVAL_* env vars."""
        def _get(name, default, cast):
            raw = os.getenv(f"RETRIEVAL_{name}")
            return cast(raw) if raw not in (None, "") else default

        def _bool(v):
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        return cls(
            mode=_get("MODE", "dense", str),
            k=_get("K", TOP_K, int),
            fetch_k=_get("FETCH_K", 25, int),
            rrf_k=_get("RRF_K", 60, int),
            dense_weight=_get("DENSE_WEIGHT", 1.0, float),
            sparse_weight=_get("SPARSE_WEIGHT", 1.0, float),
            rerank=_get("RERANK", False, _bool),
            rerank_model=_get("RERANK_MODEL", DEFAULT_RERANKER, str),
            ticker_filter=_get("TICKER_FILTER", "none", str),
        )


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    ticker: str
    chunk_idx: int
    text: str
    rank: int


def rrf_fuse(rankings: list[list[str]], weights: list[float] | None = None, rrf_k: int = 60) -> list[tuple[str, float]]:
    """Weighted Reciprocal Rank Fusion of several ranked id lists, best first.

    Ties are broken by first appearance so the result is deterministic.
    """
    weights = weights or [1.0] * len(rankings)
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for ranking, w in zip(rankings, weights):
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + w / (rrf_k + rank)
            if cid not in first_seen:
                first_seen[cid] = order
                order += 1
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))


def _dense_chunks(vectorstore, query: str, k: int, ticker: str | None) -> list[RetrievedChunk]:
    kwargs = {"k": k}
    if ticker:
        kwargs["filter"] = {"ticker": ticker.upper()}
    out: list[RetrievedChunk] = []
    for rank, doc in enumerate(vectorstore.similarity_search(query, **kwargs), start=1):
        meta = doc.metadata or {}
        tkr = str(meta.get("ticker", "UNKNOWN")).upper()
        idx = meta.get("chunk_idx", -1)
        out.append(RetrievedChunk(
            chunk_id=chunk_id(tkr, idx), ticker=tkr,
            chunk_idx=int(idx) if str(idx).lstrip("-").isdigit() else -1,
            text=doc.page_content, rank=rank,
        ))
    return out


class Retriever:
    """A configured retriever.  Heavy resources are built on first use and can be injected for tests."""

    def __init__(self, config: RetrievalConfig | None = None, vectorstore=None, bm25=None, reranker=None):
        self.config = config or RetrievalConfig()
        self._vectorstore = vectorstore
        self._bm25 = bm25
        self._reranker = reranker

    # ── lazy resources ───────────────────────────────────────────────────

    @property
    def vectorstore(self):
        if self._vectorstore is not None:
            return self._vectorstore
        # Not cached here: the agent module already holds the singleton, and
        # tests swap that one out per test.
        from agent.financial_agent import _get_vectorstore
        return _get_vectorstore()

    @property
    def bm25(self):
        if self._bm25 is None:
            from retrieval.sparse import load_or_build
            self._bm25 = load_or_build()
        return self._bm25

    @property
    def reranker(self):
        if self._reranker is None:
            from retrieval.rerank import CrossEncoderReranker
            self._reranker = CrossEncoderReranker(self.config.rerank_model)
        return self._reranker

    # ── retrieval ────────────────────────────────────────────────────────

    def retrieve(self, query: str, k: int | None = None, ticker: str | None = None) -> list[RetrievedChunk]:
        cfg = self.config
        k = k or cfg.k
        if ticker is None and cfg.ticker_filter == "inferred":
            ticker = infer_single_ticker(query)
        # Plain dense retrieval fetches exactly k, as the tools always did, so
        # the shipped configuration is byte-for-byte the shipped behaviour.
        fetch_k = k if (cfg.mode == "dense" and not cfg.rerank) else max(cfg.fetch_k, k)

        candidates: list[RetrievedChunk]
        if cfg.mode == "dense":
            candidates = _dense_chunks(self.vectorstore, query, fetch_k, ticker)
        elif cfg.mode == "bm25":
            candidates = [
                RetrievedChunk(h.chunk_id, h.ticker, h.chunk_idx, h.text, h.rank)
                for h in self.bm25.search(query, fetch_k, ticker)
            ]
        else:  # hybrid
            dense = _dense_chunks(self.vectorstore, query, fetch_k, ticker)
            sparse = self.bm25.search(query, fetch_k, ticker)
            by_id = {c.chunk_id: c for c in dense}
            for h in sparse:
                by_id.setdefault(h.chunk_id, RetrievedChunk(h.chunk_id, h.ticker, h.chunk_idx, h.text, h.rank))
            fused = rrf_fuse(
                [[c.chunk_id for c in dense], [h.chunk_id for h in sparse]],
                [cfg.dense_weight, cfg.sparse_weight], cfg.rrf_k,
            )
            candidates = [by_id[cid] for cid, _ in fused[:fetch_k]]

        if cfg.rerank:
            candidates = self.reranker.rerank(query, candidates, k)
        else:
            candidates = candidates[:k]
        return [RetrievedChunk(c.chunk_id, c.ticker, c.chunk_idx, c.text, rank)
                for rank, c in enumerate(candidates, start=1)]


_default_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    """The process-wide retriever built from the environment (RETRIEVAL_* vars)."""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = Retriever(RetrievalConfig.from_env())
    return _default_retriever


def retrieve(query: str, k: int, ticker: str | None = None, vectorstore=None,
             config: RetrievalConfig | None = None) -> list[RetrievedChunk]:
    """Functional entry point.  With no *config*, dense retrieval exactly as the tools shipped it."""
    if config is None and vectorstore is not None:
        return _dense_chunks(vectorstore, query, k, ticker)
    retriever = Retriever(config or RetrievalConfig(), vectorstore=vectorstore)
    return retriever.retrieve(query, k, ticker)
