# retrieval/retriever.py
#
# PURPOSE
# -------
# The one place that turns a query into a ranked list of chunks, so that the
# evaluation harness and (from upgrade 2) the agent tools retrieve through the
# same code and the same configuration.  Today it wraps the dense ChromaDB
# similarity search exactly as the tools call it — same embedding model, same
# metadata filter shape — so the numbers measured through here describe the
# shipped system.  Upgrade 2 adds sparse, hybrid and reranked modes behind
# RetrievalConfig without changing this interface.

from dataclasses import asdict, dataclass, field

from agent.observations import chunk_id
from retrieval.query_engine import TOP_K


@dataclass(frozen=True)
class RetrievalConfig:
    """Everything that decides WHICH chunks come back for a query.

    Included in every results file's config hash, so two runs that retrieved
    differently can never share a hash.
    """
    mode: str = "dense"          # upgrade 2: "bm25", "hybrid"
    k: int = TOP_K               # chunks returned per query
    ticker_filter: bool = False  # apply a metadata filter when a ticker is known
    extra: dict = field(default_factory=dict)  # mode-specific knobs (rrf_k, reranker, ...)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    ticker: str
    chunk_idx: int
    text: str
    rank: int


def retrieve(query: str, k: int, ticker: str | None = None, vectorstore=None) -> list[RetrievedChunk]:
    """Dense retrieval: the top-*k* chunks by embedding similarity, best first.

    *ticker*, when given, becomes a Chroma ``where`` filter — the same call
    compare_companies makes.  *vectorstore* is injectable for tests; the
    default is the shared module-level instance the agent tools use.
    """
    if vectorstore is None:
        from agent.financial_agent import _get_vectorstore
        vectorstore = _get_vectorstore()
    kwargs = {"k": k}
    if ticker:
        kwargs["filter"] = {"ticker": ticker.upper()}
    docs = vectorstore.similarity_search(query, **kwargs)
    out: list[RetrievedChunk] = []
    for rank, doc in enumerate(docs, start=1):
        meta = doc.metadata or {}
        tkr = str(meta.get("ticker", "UNKNOWN")).upper()
        idx = meta.get("chunk_idx", -1)
        out.append(RetrievedChunk(
            chunk_id=chunk_id(tkr, idx),
            ticker=tkr,
            chunk_idx=int(idx) if str(idx).lstrip("-").isdigit() else -1,
            text=doc.page_content,
            rank=rank,
        ))
    return out
