# ingestion/embedder.py
#
# PURPOSE
# -------
# Convert text chunks into dense vector embeddings and upsert them into the
# ChromaDB vector store so they can be retrieved by semantic similarity at
# query time.
#
# WHAT IS AN EMBEDDING?
# ---------------------
# An embedding is a fixed-length list of floating-point numbers (a vector)
# that encodes the *meaning* of a piece of text in a high-dimensional space.
# Two chunks that discuss the same concept (e.g. "cloud revenue growth" and
# "Azure segment expansion") will have vectors that are geometrically close,
# even if they share no keywords.  This is what enables semantic search — the
# agent can find relevant passages even when the query words don't appear
# verbatim in the document.
#
# LOCAL INFERENCE vs. API EMBEDDINGS
# ------------------------------------
# There are two broad approaches to generating embeddings:
#
#   Approach          | Model               | Dims | Cost        | Privacy
#   ------------------|---------------------|------|-------------|--------
#   OpenAI API        | text-embedding-3-small | 1536 | $0.02/1M tok | data leaves machine
#   Local (this file) | all-MiniLM-L6-v2   |  384 | free        | data stays local
#
# We use a LOCAL model for the following reasons:
#
#   1. Cost — embedding five 10-K filings (~2–3 M tokens each time the index
#      is rebuilt) costs nothing locally vs. ~$0.05–$0.10 per run with OpenAI.
#      For a research prototype that may be rebuilt many times this adds up.
#
#   2. Privacy — SEC filings are public documents, but in a real deployment
#      a financial agent often processes proprietary research notes or internal
#      communications.  Running embeddings locally ensures that sensitive text
#      never leaves the machine or network perimeter.
#
#   3. Latency and offline use — local inference needs no network round-trip.
#      The pipeline can run fully offline after the model is downloaded once.
#
#   4. Reproducibility — API models can be updated or deprecated by the
#      provider.  A pinned local model produces identical vectors forever,
#      which is important for deterministic retrieval benchmarks.
#
# WHY all-MiniLM-L6-v2?
# ----------------------
# all-MiniLM-L6-v2 is the most widely used sentence-transformer model for
# RAG prototypes because it sits at the right point on the speed/quality curve:
#
#   Model               | Dims | MTEB avg | Params  | CPU speed
#   --------------------|------|----------|---------|----------
#   all-MiniLM-L6-v2   |  384 |  56.3    |  22 M   |  fast
#   all-MiniLM-L12-v2  |  384 |  59.8    |  33 M   |  medium
#   all-mpnet-base-v2  |  768 |  63.3    |  109 M  |  slow
#   OpenAI 3-small      | 1536 |  62.3    |   -     |  API only
#
#   - 22 M parameters fits comfortably in CPU RAM; no GPU required.
#   - 384 dimensions is a third the size of OpenAI's vectors, so ChromaDB
#     uses less disk space and similarity queries run faster.
#   - MTEB score of 56.3 is competitive with models 5× its size on the
#     kinds of factual Q&A tasks a financial agent performs.
#
# TRADEOFF TO BE AWARE OF:
#   all-MiniLM-L6-v2 was trained on general web text, not financial prose.
#   If retrieval quality on domain-specific terminology (GAAP line items,
#   bond covenants, segment accounting) proves insufficient, consider:
#     - BAAI/bge-small-en-v1.5  (similar size, higher MTEB, Apache-2 licence)
#     - thenlper/gte-small       (strong financial domain performance)
#     - Swapping back to OpenAI text-embedding-3-small for production
#   The model name is a single constant (EMBEDDING_MODEL below), so swapping
#   requires changing exactly one line plus a re-index.
#
# FIRST-RUN NOTE:
#   On first use, sentence-transformers downloads the model weights (~80 MB)
#   from the Hugging Face Hub and caches them at:
#     Windows : C:\Users\<user>\.cache\huggingface\hub\
#     Linux   : ~/.cache/huggingface/hub/
#   Subsequent runs load directly from the cache with no network access.
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
CHROMA_PERSIST_DIR = Path(__file__).resolve().parent.parent / "data" / "chroma_db"

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
