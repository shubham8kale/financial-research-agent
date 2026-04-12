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
# WHY text-embedding-3-small?
# ---------------------------
# OpenAI offers three embedding models as of early 2025:
#
#   Model                    | Dimensions | $/1M tokens | MTEB avg
#   -------------------------|------------|-------------|----------
#   text-embedding-ada-002   |   1 536    |   $0.10     |  61.0
#   text-embedding-3-small   |   1 536    |   $0.02     |  62.3
#   text-embedding-3-large   |   3 072    |   $0.13     |  64.6
#
# text-embedding-3-small is the right default for this project because:
#   1. Cost: 5× cheaper than ada-002 and 6.5× cheaper than 3-large.  A full
#      corpus of 5 company 10-Ks (~2–3 million tokens) costs ≈ $0.05 to embed.
#   2. Quality: slightly *beats* ada-002 on the MTEB benchmark despite the
#      lower price — OpenAI's newer architecture is more efficient.
#   3. Dimension parity with ada-002: both produce 1 536-dim vectors, so
#      switching from ada-002 requires no schema changes to the vector store.
#   4. Upgrade path: if retrieval quality needs a boost later, swapping to
#      text-embedding-3-large is a one-line change and a re-index.
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

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

load_dotenv()

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# The embedding model identifier.  Defined as a constant so every module that
# needs to know the model name (e.g. for logging, cost estimation) imports it
# from one place — no magic strings scattered through the codebase.
EMBEDDING_MODEL = "text-embedding-3-small"

# Directory where ChromaDB persists its SQLite + binary index files.
# Keeping this outside the Python package directory prevents accidental
# inclusion in source distributions or Docker build contexts.
CHROMA_PERSIST_DIR = Path(__file__).resolve().parent.parent / "data" / "chroma_db"

# The name of the ChromaDB collection that stores the financial filing chunks.
# A single collection is fine for this corpus; add more collections if you
# later ingest different document types (e.g. earnings transcripts, analyst
# reports) and want to search them independently.
COLLECTION_NAME = "sec_filings"

# How many chunks to send per embedding API call.
# OpenAI allows up to 2 048 inputs per request.  100 is a conservative batch
# size that:
#   - Keeps individual request payloads small (avoids 413 errors on large chunks)
#   - Allows the SDK to retry a failed batch without re-embedding thousands of
#     chunks
EMBED_BATCH_SIZE = 100


def build_embeddings() -> OpenAIEmbeddings:
    """Construct and return the OpenAI embeddings client.

    Centralised in a factory so callers (embedder, retriever, query engine)
    all use the same model and configuration without duplicating parameters.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY is not set.  Copy .env.example to .env and add "
            "your key before running the ingestion pipeline."
        )

    return OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        # chunk_size here is the *API* batch size (how many texts per HTTP
        # request), NOT the document chunk size from chunker.py.  The naming
        # collision in the LangChain API is unfortunate but intentional — it
        # matches the OpenAI SDK parameter name.
        chunk_size=EMBED_BATCH_SIZE,
        openai_api_key=api_key,
    )


def build_vectorstore(embeddings: OpenAIEmbeddings | None = None) -> Chroma:
    """Open (or create) the persistent ChromaDB collection.

    Parameters
    ----------
    embeddings:
        Pre-built embeddings instance.  If None, one is created automatically.
        Pass an explicit instance when you want to reuse the client across
        multiple calls (saves repeated env-var lookups and object construction).

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
        Pre-built Chroma instance.  If None, one is built from the environment.

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

    # add_texts handles batching internally (respecting EMBED_BATCH_SIZE) and
    # returns a list of document IDs assigned by ChromaDB.
    ids = vectorstore.add_texts(texts=chunks, metadatas=metadatas)
    logger.info("Upserted %d vectors into collection '%s'.", len(ids), COLLECTION_NAME)

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
    # Smoke test: embed a single synthetic chunk and confirm it round-trips.
    logging.basicConfig(level=logging.INFO)
    test_chunks = [
        "Apple Inc. reported record revenue of $394 billion in fiscal year 2023.",
        "Microsoft Azure cloud revenue grew 28% year-over-year in Q4 2023.",
    ]
    vs = embed_ticker_chunks("TEST", test_chunks, vectorstore=None)
    results = vs.similarity_search("cloud revenue growth", k=1)
    print("Top result:", results[0].page_content[:120])
