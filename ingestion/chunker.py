# ingestion/chunker.py
#
# PURPOSE
# -------
# Split the raw text of SEC filings into smaller, overlapping chunks that can
# be independently embedded and stored in the vector database.
#
# WHY CHUNK AT ALL?
# -----------------
# Large Language Models have a fixed context window (current Gemini flash models
# accept ~1 M tokens, but a single 10-K filing here cleans to ~3.6 M characters,
# and retrieval still has to select what is actually relevant).
# Even when a model *could* fit the whole document, passing the entire filing
# to the LLM on every query is:
#   1. Expensive  – you pay per token, and most of the document is irrelevant
#                   to a given question.
#   2. Slow       – more tokens = more latency.
#   3. Less accurate – research shows retrieval quality degrades when the
#                   context window is packed ("lost in the middle" problem).
#
# Chunking lets us retrieve only the ~3–5 most relevant passages for each
# query, keeping cost and latency low while improving answer quality.
#
# WHY RecursiveCharacterTextSplitter?
# ------------------------------------
# LangChain offers several splitters:
#   • CharacterTextSplitter  – splits on a single separator (e.g. "\n\n").
#                              Brittle: one long paragraph blows the chunk size.
#   • TokenTextSplitter      – splits on exact token counts.
#                              Ignores semantic boundaries like paragraphs.
#   • RecursiveCharacterTextSplitter (RCTS) – tries a list of separators in
#                              order ("\n\n", "\n", " ", "") and recurses until
#                              each piece fits within chunk_size.
#
# RCTS is the best general-purpose choice because it *prefers* natural
# boundaries (paragraph → sentence → word) before resorting to mid-word splits.
# SEC filings are heavily formatted with headers and paragraphs, so RCTS
# exploits that structure to keep semantically coherent chunks together.

import logging
from pathlib import Path
from typing import Generator

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# ── Chunk parameters ──────────────────────────────────────────────────────────

# CHUNK_SIZE: maximum number of *characters* per chunk.
#
# Why 512?
#   - The embedding model (all-MiniLM-L6-v2) truncates at 256 word-piece tokens,
#     roughly 1 000 characters, so a 512-character chunk always fits whole —
#     anything materially larger would be silently cut off mid-passage.
#     Embedding quality also peaks at shorter, focused passages: academic work
#     (e.g. Shi et al. 2023 "REPLUG") shows that ~200–600 token chunks yield
#     the best retrieval precision for long-document Q&A.
#   - 512 characters ≈ 100–150 tokens for English prose, well within the
#     embedding window and small enough to stay semantically tight.
#   - Going larger (e.g. 2 048 chars) risks mixing multiple topics in one
#     chunk, which degrades cosine-similarity matching at query time.
CHUNK_SIZE = 512

# CHUNK_OVERLAP: number of characters shared between consecutive chunks.
#
# Why 50?
#   - Without overlap, a sentence that straddles a chunk boundary is split in
#     two and neither chunk contains its full meaning.  The overlap acts as a
#     "safety net" that keeps boundary content intact.
#   - 50 chars ≈ half a sentence — enough to preserve context at the seam
#     without inflating the corpus size significantly.
#   - Rule of thumb: overlap should be ~10% of chunk_size.  50 / 512 ≈ 9.8%.
CHUNK_OVERLAP = 50


def build_splitter() -> RecursiveCharacterTextSplitter:
    """Construct and return a configured text splitter instance.

    Returning the splitter from a factory function (rather than instantiating
    it inline) makes it easy to inject a different splitter in tests or to
    swap parameters from config without hunting through calling code.
    """
    return RecursiveCharacterTextSplitter(
        # chunk_size: the *target* maximum length of each chunk in characters.
        # The splitter may produce slightly shorter chunks at natural boundaries
        # but will never produce a chunk longer than this value.
        chunk_size=CHUNK_SIZE,
        # chunk_overlap: how many characters from the end of one chunk are
        # repeated at the start of the next chunk.  This prevents information
        # loss at chunk boundaries (see rationale above).
        chunk_overlap=CHUNK_OVERLAP,
        # length_function: the callable used to measure chunk size.
        # Using `len` (character count) keeps things fast and dependency-free.
        # Alternative: pass `tiktoken`'s encode function to measure in *tokens*
        # instead — useful if you want chunk_size to map exactly to the model's
        # token budget, at the cost of a per-character tokenisation overhead.
        length_function=len,
        # separators: the ordered list of delimiters the splitter tries in
        # sequence.  It starts with the most paragraph-like separator and falls
        # back to finer granularity only when a piece is still too large.
        #   "\n\n" → paragraph break (most common in SEC filings)
        #   "\n"   → line break
        #   " "    → word boundary
        #   ""     → character (last resort, rarely reached at chunk_size=512)
        separators=["\n\n", "\n", " ", ""],
    )


def chunk_text(text: str) -> list[str]:
    """Split a single string into chunks.

    Parameters
    ----------
    text:
        The raw document text to split.

    Returns
    -------
    A list of string chunks, each at most CHUNK_SIZE characters long, with
    CHUNK_OVERLAP characters of overlap between consecutive chunks.
    """
    splitter = build_splitter()
    chunks = splitter.split_text(text)
    logger.debug("Split document into %d chunks (size=%d, overlap=%d)",
                 len(chunks), CHUNK_SIZE, CHUNK_OVERLAP)
    return chunks


def chunk_file(file_path: Path) -> list[str]:
    """Read a text file from disk and return its chunks.

    Parameters
    ----------
    file_path:
        Path to a plain-text (.txt) file, typically a 10-K filing that has
        been pre-processed to strip HTML/XBRL tags.

    Returns
    -------
    List of text chunks ready for embedding.
    """
    logger.info("Chunking file: %s", file_path)
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    return chunk_text(text)


def chunk_directory(directory: Path) -> Generator[tuple[Path, list[str]], None, None]:
    """Recursively chunk all .txt files under a directory.

    Yields (file_path, chunks) pairs so the caller can associate each chunk
    with its source file for metadata tagging in the vector store.  Storing
    the source path alongside each chunk lets the agent cite the exact filing
    when answering questions.

    Parameters
    ----------
    directory:
        Root directory to walk (e.g. the `data/sec_filings/` folder).
    """
    txt_files = list(directory.rglob("*.txt"))
    logger.info("Found %d .txt files under %s", len(txt_files), directory)

    for file_path in txt_files:
        yield file_path, chunk_file(file_path)


if __name__ == "__main__":
    # Quick sanity check: chunk a small synthetic document and print results.
    sample = (
        "This is the first paragraph of the annual report.  It discusses revenue.\n\n"
        "This is the second paragraph.  It covers operating expenses and margins.\n\n"
        "Third paragraph talks about risk factors including macroeconomic headwinds."
    )
    chunks = chunk_text(sample)
    print(f"Produced {len(chunks)} chunk(s):")
    for i, c in enumerate(chunks):
        print(f"  [{i}] ({len(c)} chars) {c[:80]!r} …")
