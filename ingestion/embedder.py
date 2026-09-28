# ingestion/embedder.py
#
# PURPOSE
# -------
# Convert text chunks into dense vector embeddings and upsert them into the
# ChromaDB vector store so they can be retrieved by semantic similarity at
# query time.
#
# MODEL CHOICE
# ------------
# all-MiniLM-L6-v2, run locally: 22 M parameters, 384 dimensions, a few
# seconds per filing on a laptop CPU, no key, and pinned so the same text
# always embeds to the same vector — which is what makes the retrieval
# benchmarks reproducible.  It is a general-web model, so financial
# terminology (GAAP line items, segment names) is its known weakness; the
# measured answer to that in this repository was a cross-encoder reranker
# over its candidates (eval/EVALUATION.md, "Retrieval ablation"), not a
# bigger encoder.  bge-small-en-v1.5 and gte-small are the candidates if the
# encoder itself is ever revisited; EMBEDDING_MODEL is the one constant to
# change, followed by a re-index.  First use downloads ~80 MB from the
# Hugging Face Hub into the local cache (HF_HOME in the Dockerfile).
#
# WHY ChromaDB?
# -------------
# ChromaDB runs fully in-process (no separate server) and persists to disk
# automatically.  For a research prototype that runs on a laptop, this removes
# the operational overhead of running a Pinecone / Weaviate / Qdrant service.
# When the project needs to scale (concurrent users, billions of vectors), the
# LangChain Chroma wrapper can be swapped for any other VectorStore with
# minimal code changes.

import logging
import os
from pathlib import Path

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# The sentence-transformers model to use for embedding.  This string is passed
# directly to SentenceTransformer(), which accepts any model name from the
# Hugging Face Hub or a local directory path.  Changing this one constant and
# re-running the pipeline is all that is needed to switch models.
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Directory where ChromaDB persists its SQLite + binary index files.
# Keeping this outside the Python package directory prevents accidental
# inclusion in source distributions or Docker build contexts.
# CHROMA_PERSIST_DIR in the environment points every reader at a different
# index — a rebuild under test, or the CI slice (eval/ci_corpus.py) — without
# touching the shipped one.  retrieval/sparse.py and ingestion/xbrl.py keep
# their derived files (the BM25 pickle, the fact table) next to whichever
# index this names.
_DEFAULT_CHROMA_DIR = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
CHROMA_PERSIST_DIR = Path(os.getenv("CHROMA_PERSIST_DIR") or _DEFAULT_CHROMA_DIR).resolve()

# The name of the ChromaDB collection that stores the financial filing chunks.
# A single collection is fine for this corpus; add more collections if you
# later ingest different document types (e.g. earnings transcripts, analyst
# reports) and want to search them independently.
COLLECTION_NAME = "sec_filings"

# Number of chunks processed per encode() call.
# Unlike the OpenAI API (where batch_size is a network request limit),
# here it controls how many texts are passed to the model's forward pass at
# once.  32 is a safe default for CPU inference on a laptop:
#   - Small enough that 32 × 512-char chunks fits comfortably in RAM.
#   - Large enough to amortise the per-batch Python overhead across many chunks.
# Increase to 64–128 if you have a GPU or ample RAM to speed up indexing.
ENCODE_BATCH_SIZE = 32

# Maximum number of chunks passed to a single vectorstore.add_texts() call.
# ChromaDB can become slow or run out of memory when adding very large batches
# in one shot.  Splitting into chunks of 5 000 keeps each call manageable while
# still amortising the per-call overhead across many embeddings.
CHROMA_BATCH_SIZE = 5000


def build_embeddings() -> HuggingFaceEmbeddings:
    """Construct and return the local HuggingFace embeddings client.

    On first call, sentence-transformers downloads the model weights from
    the Hugging Face Hub (~80 MB) and caches them locally.  All subsequent
    calls load from the cache instantly.

    Centralised in a factory so callers (embedder, retriever, query engine)
    all use the same model and configuration without duplicating parameters.
    """
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        # model_kwargs are forwarded to the SentenceTransformer constructor.
        # Explicitly setting device="cpu" avoids a CUDA/MPS detection step on
        # machines without a GPU, which can otherwise add a second of startup
        # time.  Change to "cuda" or "mps" to use a GPU if one is available.
        model_kwargs={"device": "cpu"},
        # encode_kwargs are forwarded to SentenceTransformer.encode().
        # normalize_embeddings=True scales every vector to unit length before
        # storing it.  ChromaDB uses cosine similarity by default, and cosine
        # similarity is only well-defined on unit vectors — without
        # normalisation, vectors with different magnitudes would produce
        # misleading similarity scores, causing irrelevant chunks to rank above
        # relevant ones purely because of text length differences.
        encode_kwargs={
            "normalize_embeddings": True,
            "batch_size": ENCODE_BATCH_SIZE,
        },
    )


def build_vectorstore(embeddings: HuggingFaceEmbeddings | None = None) -> Chroma:
    """Open (or create) the persistent ChromaDB collection.

    Parameters
    ----------
    embeddings:
        Pre-built embeddings instance.  If None, one is created automatically.
        Pass an explicit instance when you want to reuse the client across
        multiple calls (saves repeated model-loading overhead).

    Returns
    -------
    A LangChain Chroma vector store that is ready for `add_texts` or
    `similarity_search` calls.
    """
    if embeddings is None:
        embeddings = build_embeddings()

    CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)

    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        # persist_directory tells Chroma to write its index to disk instead of
        # keeping it in memory.  Without this, all embeddings are lost when
        # the Python process exits — you'd have to re-embed the entire corpus
        # on every run.
        persist_directory=str(CHROMA_PERSIST_DIR),
    )


def embed_chunks(
    chunks: list[str],
    metadatas: list[dict] | None = None,
    vectorstore: Chroma | None = None,
) -> Chroma:
    """Embed a list of text chunks and upsert them into ChromaDB.

    Parameters
    ----------
    chunks:
        The text chunks produced by `chunker.chunk_text` or
        `chunker.chunk_file`.
    metadatas:
        Optional list of metadata dicts (one per chunk).  Recommended fields:
          - "ticker"    : stock symbol, e.g. "AAPL"
          - "source"    : absolute path to the source file
          - "chunk_idx" : integer index within the source document
        Metadata is stored alongside each vector and returned in search results,
        allowing the agent to cite the exact filing and section.
    vectorstore:
        Pre-built Chroma instance.  If None, one is built automatically.

    Returns
    -------
    The Chroma vectorstore instance (useful if the caller wants to chain
    additional operations, e.g. immediately run a similarity search to verify
    that embeddings were stored correctly).
    """
    if vectorstore is None:
        vectorstore = build_vectorstore()

    if not chunks:
        logger.warning("embed_chunks called with an empty chunk list — nothing to do.")
        return vectorstore

    logger.info("Embedding %d chunks with model '%s' …", len(chunks), EMBEDDING_MODEL)

    all_ids: list[str] = []
    total_batches = (len(chunks) + CHROMA_BATCH_SIZE - 1) // CHROMA_BATCH_SIZE
    for batch_num, start in enumerate(range(0, len(chunks), CHROMA_BATCH_SIZE), start=1):
        batch_texts = chunks[start:start + CHROMA_BATCH_SIZE]
        batch_metas = metadatas[start:start + CHROMA_BATCH_SIZE] if metadatas else None
        logger.info(
            "Upserting batch %d/%d (%d chunks, indices %d–%d) …",
            batch_num, total_batches, len(batch_texts), start, start + len(batch_texts) - 1,
        )
        batch_ids = vectorstore.add_texts(texts=batch_texts, metadatas=batch_metas)
        all_ids.extend(batch_ids)

    logger.info("Upserted %d vectors into collection '%s'.", len(all_ids), COLLECTION_NAME)

    return vectorstore


def embed_ticker_chunks(
    ticker: str,
    chunks: list[str],
    source_path: str | None = None,
    vectorstore: Chroma | None = None,
) -> Chroma:
    """Convenience wrapper: embed chunks for a specific ticker with auto-metadata.

    Parameters
    ----------
    ticker:
        Stock symbol (e.g. "MSFT").  Stored in metadata so queries can be
        filtered to a specific company.
    chunks:
        Text chunks for this filing.
    source_path:
        Optional path to the source .txt file, stored in metadata for citation.
    vectorstore:
        Shared Chroma instance; created if not provided.

    Returns
    -------
    The Chroma vectorstore instance.
    """
    metadatas = [
        {
            "ticker": ticker,
            "source": source_path or "",
            "chunk_idx": i,
        }
        for i in range(len(chunks))
    ]
    return embed_chunks(chunks, metadatas=metadatas, vectorstore=vectorstore)


if __name__ == "__main__":
    # Smoke test: embed two synthetic chunks and confirm similarity search
    # returns the more relevant one for a financial query.
    #   python -m ingestion.embedder
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_chunks = [
        "Apple Inc. reported record revenue of $394 billion in fiscal year 2023.",
        "Microsoft Azure cloud revenue grew 28% year-over-year in Q4 2023.",
    ]
    print(f"Embedding {len(test_chunks)} test chunks with {EMBEDDING_MODEL} …")
    vs = embed_ticker_chunks("TEST", test_chunks, vectorstore=None)

    query = "What was Apple's annual revenue?"
    results = vs.similarity_search(query, k=1)
    print(f"\nQuery : {query!r}")
    print(f"Top-1 : {results[0].page_content!r}")
