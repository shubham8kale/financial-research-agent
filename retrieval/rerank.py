# retrieval/rerank.py
#
# PURPOSE
# -------
# Second-opinion scoring of retrieval candidates with a cross-encoder.  The
# first-stage retrievers (dense, BM25) score a query and a chunk separately and
# compare the results; a cross-encoder reads the query and the chunk together
# and is markedly better at deciding whether this chunk answers this question.
# It is too slow to run over 67,521 chunks, so it re-orders a short candidate
# list — fetch 25 or 50, keep 5.
#
# The default model is the smallest widely used MS MARCO cross-encoder
# (~90 MB), which runs on CPU inside the free-tier Space's budget.  A larger
# reranker is a config change, and its latency is measured alongside its gain
# by eval/run_retrieval_eval.py before anyone decides to ship it.

import logging
import threading

logger = logging.getLogger(__name__)

DEFAULT_RERANKER = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    """Lazily loads a sentence-transformers CrossEncoder and re-orders candidates by its score."""

    def __init__(self, model_name: str = DEFAULT_RERANKER, batch_size: int = 32):
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None
        self._load_lock = threading.Lock()

    def _load(self):
        if self._model is None:
            # Concurrent first calls (a ToolNode's worker threads) must not each load a copy of the model.
            with self._load_lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder

                    logger.info("Loading cross-encoder reranker %s", self.model_name)
                    self._model = CrossEncoder(self.model_name)
        return self._model

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        model = self._load()
        scores = model.predict([(query, t) for t in texts], batch_size=self.batch_size, show_progress_bar=False)
        return [float(s) for s in scores]

    def rerank(self, query: str, candidates: list, k: int) -> list:
        """Top-*k* of *candidates* (objects with a ``.text``) by cross-encoder score, best first."""
        if not candidates:
            return []
        scores = self.score(query, [c.text for c in candidates])
        order = sorted(range(len(candidates)), key=lambda i: -scores[i])
        return [candidates[i] for i in order[:k]]
