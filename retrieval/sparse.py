# retrieval/sparse.py
#
# PURPOSE
# -------
# A BM25 index over the same chunks the dense index holds, so that exact
# tokens — "Item 7A", a ticker, a GAAP line item, "128,725" — can be matched
# literally.  A 384-dimension embedding blurs those; BM25 does not.  This is
# the sparse half of hybrid retrieval, and it runs with no model and no API.
#
# HOW IT IS BUILT
# ---------------
# The chunks are read straight out of the ChromaDB collection (paged, because
# one get() is capped), tokenised, and handed to rank_bm25.  The result is
# pickled next to the Chroma directory and reused while the collection's
# chunk count is unchanged; a rebuild takes about a minute on CPU.  The pickle
# lives under data/, which is gitignored like the Chroma index it mirrors.
#
# TOKENISATION
# ------------
# Lower-cased alphanumerics, with a number's internal separators kept so that
# "128,725" and "4.6" survive as single tokens.  No stemming and no stop-word
# list: 10-K prose is full of tokens ("cost", "costs", "of") whose exact form
# matters in table rows, and BM25's IDF already discounts the common ones.

import logging
import pickle
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agent.observations import chunk_id
from ingestion.embedder import CHROMA_PERSIST_DIR, COLLECTION_NAME

logger = logging.getLogger(__name__)

BM25_INDEX_FILE = CHROMA_PERSIST_DIR.parent / "bm25_index.pkl"
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


@dataclass(frozen=True)
class SparseHit:
    chunk_id: str
    ticker: str
    chunk_idx: int
    text: str
    score: float
    rank: int


class BM25Index:
    """BM25 over every chunk, searchable with an optional ticker restriction."""

    def __init__(self, chunk_ids: list[str], tickers: list[str], texts: list[str], tokenized=None):
        from rank_bm25 import BM25Okapi

        self.chunk_ids = list(chunk_ids)
        self.tickers = np.array(tickers)
        self.texts = list(texts)
        self._bm25 = BM25Okapi(tokenized if tokenized is not None else [tokenize(t) for t in texts])

    def __len__(self) -> int:
        return len(self.chunk_ids)

    def search(self, query: str, k: int, ticker: str | None = None) -> list[SparseHit]:
        tokens = tokenize(query)
        if not tokens or not self.chunk_ids:
            return []
        scores = np.asarray(self._bm25.get_scores(tokens), dtype=float)
        if ticker:
            scores = np.where(self.tickers == ticker.upper(), scores, -np.inf)
        k = min(k, len(scores))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top], kind="stable")]
        hits: list[SparseHit] = []
        for rank, i in enumerate(top, start=1):
            if not np.isfinite(scores[i]) or scores[i] <= 0:
                break
            tkr, idx = self.chunk_ids[i].rsplit("_10K_chunk_", 1)
            hits.append(SparseHit(self.chunk_ids[i], tkr, int(idx), self.texts[i], float(scores[i]), rank))
        return hits

    # ── persistence ──────────────────────────────────────────────────────

    def save(self, path: Path = BM25_INDEX_FILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"chunk_ids": self.chunk_ids, "tickers": self.tickers.tolist(),
                         "texts": self.texts, "bm25": self._bm25}, f, protocol=pickle.HIGHEST_PROTOCOL)
        return path

    @classmethod
    def load(cls, path: Path = BM25_INDEX_FILE) -> "BM25Index":
        with open(path, "rb") as f:
            blob = pickle.load(f)
        obj = cls.__new__(cls)
        obj.chunk_ids = blob["chunk_ids"]
        obj.tickers = np.array(blob["tickers"])
        obj.texts = blob["texts"]
        obj._bm25 = blob["bm25"]
        return obj

    @classmethod
    def from_chroma(cls, persist_dir: Path = CHROMA_PERSIST_DIR, collection: str = COLLECTION_NAME,
                    page: int = 5000) -> "BM25Index":
        import chromadb

        col = chromadb.PersistentClient(path=str(persist_dir)).get_collection(collection)
        n = col.count()
        ids: list[str] = []
        tickers: list[str] = []
        texts: list[str] = []
        offset = 0
        while offset < n:
            res = col.get(include=["documents", "metadatas"], limit=page, offset=offset)
            for doc, meta in zip(res["documents"], res["metadatas"]):
                ids.append(chunk_id(meta["ticker"], meta["chunk_idx"]))
                tickers.append(str(meta["ticker"]).upper())
                texts.append(doc)
            offset += page
        logger.info("Building BM25 over %d chunks from %s/%s", len(ids), persist_dir, collection)
        return cls(ids, tickers, texts)


def chroma_chunk_count(persist_dir: Path = CHROMA_PERSIST_DIR, collection: str = COLLECTION_NAME) -> int:
    import chromadb

    return chromadb.PersistentClient(path=str(persist_dir)).get_collection(collection).count()


def load_or_build(path: Path = BM25_INDEX_FILE, expected_count: int | None = None) -> BM25Index:
    """The cached BM25 index, rebuilt when missing or when the Chroma chunk count differs."""
    if expected_count is None:
        expected_count = chroma_chunk_count()
    if path.exists():
        index = BM25Index.load(path)
        if len(index) == expected_count:
            logger.info("Loaded BM25 index (%d chunks) from %s", len(index), path)
            return index
        logger.warning("BM25 index at %s has %d chunks, Chroma has %d — rebuilding.",
                       path, len(index), expected_count)
    index = BM25Index.from_chroma()
    index.save(path)
    logger.info("Saved BM25 index (%d chunks) to %s", len(index), path)
    return index
