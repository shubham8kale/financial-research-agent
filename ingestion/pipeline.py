# ingestion/pipeline.py
#
# PURPOSE
# -------
# Orchestrate the full ingestion pipeline from raw SEC filings on disk to a
# queryable ChromaDB vector store.  This is the single entry point for
# (re-)building the knowledge base:
#
#   python -m ingestion.pipeline
#
# PIPELINE STAGES (per ticker)
# ----------------------------
#   1. Locate  – find the full-submission.txt downloaded by downloader.py
#   2. Clean   – strip SEC header, HTML/XBRL tags, and XBRL preamble
#                via clean_filing() in cleaner.py
#   3. Chunk   – split clean prose into 512-char overlapping segments
#                via chunk_text() in chunker.py
#   4. Embed   – encode chunks locally with all-MiniLM-L6-v2 and upsert
#                vectors into ChromaDB via embed_ticker_chunks() in embedder.py
#
# After all tickers are processed a test similarity search is run to give
# immediate confirmation that end-to-end retrieval is working.
#
# DESIGN DECISIONS
# ----------------
# One vectorstore instance, shared across all tickers:
#   Chroma opens its SQLite index file when the Python object is constructed.
#   Creating a new instance per ticker would open/close the same file five
#   times, adding unnecessary I/O and risking concurrent-write conflicts on
#   slower disks.  A single instance also guarantees that all five companies
#   end up in the same collection, which is intentional — cross-company
#   queries ("Compare Apple and Microsoft margins") require a unified index.
#
# One embeddings client, shared across all tickers:
#   HuggingFaceEmbeddings loads the model weights into memory at construction
#   time.  Reusing the instance avoids reloading ~80 MB of weights on every
#   ticker and ensures all batches run through the same in-process model.
#
# Per-ticker error isolation:
#   Each ticker's work is wrapped in try/except so one missing or corrupt
#   filing does not abort the entire run.  The pipeline logs the traceback,
#   continues to the next ticker, and reports failures in a summary at the end.

import logging
import time
from pathlib import Path

from dotenv import load_dotenv

from ingestion.cleaner import clean_filing
from ingestion.chunker import chunk_text
from ingestion.embedder import (
    build_embeddings,
    build_vectorstore,
    embed_ticker_chunks,
    CHROMA_PERSIST_DIR,
)
from ingestion.downloader import TARGET_TICKERS, DATA_DIR

# Load .env so GEMINI_API_KEY and any other secrets are available to
# modules that need them (e.g. retrieval/query_engine.py).
load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
# basicConfig is called here (rather than relying on a parent module) because
# pipeline.py is the top-level entry point.  Child modules (cleaner, chunker,
# embedder) use `logging.getLogger(__name__)` and will inherit this config.
# Level INFO shows per-ticker progress from the sub-modules without the
# verbose per-chunk DEBUG output.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Helper ────────────────────────────────────────────────────────────────────

def find_filing(ticker: str, data_dir: Path = DATA_DIR) -> Path | None:
    """Return the path to the most recent full-submission.txt for *ticker*.

    sec-edgar-downloader saves files under:
      <data_dir>/sec-edgar-filings/<TICKER>/10-K/<accession>/full-submission.txt

    Accession numbers are formatted as XXXXXXXXXX-YY-NNNNNN where YY is the
    two-digit filing year.  Lexicographic sort gives chronological order, so
    ``matches[-1]`` is always the most recent filing — no date parsing needed.
    When NUM_FILINGS=1 (the default) there will only ever be one match, but
    this handles the multi-year case gracefully.

    Returns None if no filing exists, which the caller treats as a skip signal.
    """
    pattern = f"sec-edgar-filings/{ticker}/10-K/*/full-submission.txt"
    matches = sorted(data_dir.glob(pattern))
    return matches[-1] if matches else None


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(
    tickers: list[str] = TARGET_TICKERS,
    data_dir: Path = DATA_DIR,
) -> None:
    """Run the end-to-end ingestion pipeline for all *tickers*.

    Parameters
    ----------
    tickers:
        Ticker symbols to process.  Defaults to the five companies defined in
        downloader.py (AAPL, MSFT, GOOGL, AMZN, META).
    data_dir:
        Root of the SEC filings tree on disk.  Defaults to the same path
        that downloader.py writes to so no configuration is required.
    """
    total = len(tickers)
    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []

    # ── Initialise shared infrastructure ─────────────────────────────────────
    # Build the embeddings client and vector store once and pass them into
    # every embed_ticker_chunks() call.  This is the point where the OpenAI
    # API key is validated — if it is missing the pipeline fails here with a
    # clear EnvironmentError rather than partway through the first ticker.
    print("\nInitialising embedding model and vector store...")
    embeddings = build_embeddings()
    vectorstore = build_vectorstore(embeddings=embeddings)
    print("  model : text-embedding-3-small")
    print(f"  store : {CHROMA_PERSIST_DIR}\n")

    pipeline_start = time.perf_counter()

    for idx, ticker in enumerate(tickers, start=1):
        print(f"[{idx}/{total}] {ticker}")
        ticker_start = time.perf_counter()

        try:
            # ── Stage 1: Locate the filing ────────────────────────────────────
            # find_filing() returns None if downloader.py has not been run yet.
            # Raising FileNotFoundError here (rather than letting a subsequent
            # open() fail) produces a message that tells the user exactly how
            # to fix the problem.
            filing_path = find_filing(ticker, data_dir)
            if filing_path is None:
                raise FileNotFoundError(
                    f"No full-submission.txt found for {ticker} under {data_dir}. "
                    "Run `python -m ingestion.downloader` first."
                )
            # Print the accession directory name (not the full path) so the
            # output stays readable without wrapping.
            accession = filing_path.parent.name
            print(f"  filing   : {accession}/full-submission.txt")

            # ── Stage 2: Clean the filing ─────────────────────────────────────
            # clean_filing() performs four operations:
            #   a) Read as Latin-1 (SEC EDGAR encoding)
            #   b) Strip the <SEC-HEADER> block (metadata, not prose)
            #   c) BeautifulSoup tag removal (HTML, XBRL, CSS, JS)
            #   d) Skip XBRL preamble lines (~760 identifier-only lines)
            #   e) Collapse whitespace
            # The char count printed here gives a rough cost estimate:
            # ~4 chars/token, $0.02/1M tokens → 4M chars ≈ $0.02 to embed.
            print("  cleaning ...", end="", flush=True)
            t0 = time.perf_counter()
            clean_text = clean_filing(filing_path)
            print(f" {len(clean_text):,} chars  ({time.perf_counter() - t0:.1f}s)")

            # ── Stage 3: Chunk the clean text ─────────────────────────────────
            # chunk_text() uses RecursiveCharacterTextSplitter (512 chars,
            # 50-char overlap).  The chunk count * ~128 tokens/chunk gives an
            # estimate of total tokens to be embedded.
            print("  chunking ...", end="", flush=True)
            t0 = time.perf_counter()
            chunks = chunk_text(clean_text)
            print(f" {len(chunks):,} chunks  ({time.perf_counter() - t0:.1f}s)")

            # ── Stage 4: Embed and store ──────────────────────────────────────
            # embed_ticker_chunks() attaches three metadata fields to every chunk:
            #   - "ticker"    : enables per-company filtered retrieval
            #   - "source"    : absolute path for citation in agent responses
            #   - "chunk_idx" : preserves original document order for re-ranking
            # The shared vectorstore means all five companies end up in a single
            # ChromaDB collection, enabling cross-company similarity search.
            print("  embedding ...", end="", flush=True)
            t0 = time.perf_counter()
            embed_ticker_chunks(
                ticker=ticker,
                chunks=chunks,
                source_path=str(filing_path),
                vectorstore=vectorstore,
            )
            print(f" done  ({time.perf_counter() - t0:.1f}s)")

            elapsed = time.perf_counter() - ticker_start
            print(f"  [OK] {ticker} complete  ({elapsed:.1f}s total)\n")
            succeeded.append(ticker)

        except Exception as exc:
            # Log the full stack trace at DEBUG so it is available when
            # --verbose is needed, but keep the console output to one line.
            logger.debug("Full traceback for %s:", ticker, exc_info=True)
            print(f"  [FAIL] {ticker}: {exc}\n")
            failed.append((ticker, str(exc)))

    # ── Summary ───────────────────────────────────────────────────────────────
    total_elapsed = time.perf_counter() - pipeline_start
    print("=" * 60)
    print(f"Pipeline finished in {total_elapsed:.1f}s")
    print(f"  Succeeded : {', '.join(succeeded) or 'none'}")
    if failed:
        print(f"  Failed    : {', '.join(t for t, _ in failed)}")
    print("=" * 60)

    # ── Verification search ───────────────────────────────────────────────────
    # Run a test query against the live vector store to confirm that:
    #   1. Vectors were actually written (not silently dropped by a failed
    #      batch that returned an HTTP error without raising an exception).
    #   2. The retrieval path works end-to-end before the user hands off to
    #      the API / query layer.
    #   3. Metadata (ticker, chunk_idx) is being stored and returned correctly,
    #      since it will be used for citations in agent responses.
    #
    # "What was Apple's revenue?" is a good probe because:
    #   - It is specific enough that the top result should come from AAPL chunks.
    #   - Revenue figures appear in multiple sections (MD&A, income statement,
    #      segment tables), so retrieval should work even if one section was
    #      chunked differently than expected.
    if succeeded:
        _run_verification_search(vectorstore)


def _run_verification_search(vectorstore) -> None:
    """Run a test query and print the top 3 results with metadata.

    A standalone function (rather than inline code) so it can be called
    independently during debugging without re-running the full pipeline.
    """
    query = "What was Apple's revenue?"
    k = 3

    print(f"\nVerification search: {query!r}  (top {k} results)\n")

    try:
        results = vectorstore.similarity_search(query, k=k)
    except Exception as exc:
        print(f"  Search failed: {exc}")
        logger.exception("Verification search raised an exception")
        return

    if not results:
        print("  No results returned — the vector store may be empty.")
        return

    for rank, doc in enumerate(results, start=1):
        ticker = doc.metadata.get("ticker", "unknown")
        chunk_idx = doc.metadata.get("chunk_idx", "?")
        # Flatten newlines so the snippet fits on one readable block.
        snippet = doc.page_content[:200].replace("\n", " ").strip()
        print(f"  [{rank}] ticker={ticker}  chunk_idx={chunk_idx}")
        print(f"      {snippet!r}")
        print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # `python -m ingestion.pipeline` runs with default settings (all five
    # tickers, standard data directory).  To process a subset, import and call
    # run_pipeline() directly:
    #
    #   from ingestion.pipeline import run_pipeline
    #   run_pipeline(tickers=["AAPL", "MSFT"])
    run_pipeline()
