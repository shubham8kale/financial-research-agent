# retrieval/query_engine.py
#
# PURPOSE
# -------
# Accept a natural-language question, retrieve the most relevant passages from
# the ChromaDB vector store, and generate a grounded answer using Google Gemini
# 2.5 Flash-Lite — returning both the answer text and the source chunks for
# citation.  The exact model is controlled by the LLM_MODEL env var and
# defaults to gemini-2.5-flash-lite (see GEMINI_MODEL below).
#
# PIPELINE
# --------
#   question (str)
#       │
#       ▼
#   similarity_search()        ← ChromaDB cosine search, top-K chunks
#       │
#       ▼
#   _build_context_block()     ← formats chunks as numbered, labelled passages
#       │
#       ▼
#   ChatGoogleGenerativeAI     ← Gemini 2.5 Flash-Lite, temperature=0
#       │
#       ▼
#   QueryResult(answer, sources)
#
# WHY GEMINI 2.5 FLASH-LITE?
# --------------------------
# For a RAG system the LLM's job is synthesis and faithfulness, not recall —
# retrieval already surfaces the relevant facts.  Flash-Lite is well suited:
#
#   - Speed  : Flash-Lite is several times faster than the larger Gemini
#              models, which matters when the agent is called interactively.
#              Retrieval latency dominates total response time; a slower LLM
#              would flip that balance.
#
#   - Cost   : Flash is significantly cheaper than Pro per million tokens.
#              In a RAG pipeline each call sends ~5 × 512-char chunks (~640 tokens
#              of context) plus the question (~50 tokens) — small payloads where
#              the cost savings of Flash are most pronounced.
#
#   - Quality: For the constrained task of "read these passages and answer this
#              question" a smaller model performs nearly as well as a larger one.
#              The quality ceiling is set by retrieval precision, not LLM size.
#
#   - Context window: 1 M tokens — far larger than we ever need.  Even if
#              TOP_K were increased to 50, the entire prompt would fit with room
#              to spare.
#
# WHY temperature=0?
# ------------------
# Embedding-based retrieval already narrows the answer space to a handful of
# relevant passages.  The LLM's only remaining job is to synthesise a faithful
# answer from those passages.  Any temperature above 0 introduces stochastic
# variation that can:
#   - Paraphrase financial figures (e.g. "$391 billion" → "nearly $400 billion")
#   - Blend facts from different chunks in unintended ways
#   - Produce non-deterministic outputs that make the system hard to test
# Temperature=0 makes the model as literal and reproducible as possible.
#
# PROMPT DESIGN — ANTI-HALLUCINATION
# ------------------------------------
# Large language models have a strong prior to be "helpful" — if asked a
# question they will try to answer it even when the evidence is absent.  In a
# financial context a made-up revenue figure or fabricated risk factor is
# actively worse than no answer.  The prompt is designed to counteract this:
#
#   a) "ONLY use the provided context" — explicit grounding instruction.
#      Research (e.g. Shi et al. 2023, "REPLUG") shows that framing the
#      context as the authoritative source reduces unsupported generation.
#
#   b) Rules in the system message, not the human message — system-role
#      instructions are treated by the model as standing policy that applies
#      regardless of what the user message contains.  Placing grounding rules
#      in the human message makes them easier to override via prompt injection
#      in adversarial questions.
#
#   c) Explicit fallback phrase — giving the model a sanctioned "I don't know"
#      response removes the pressure to speculate.  Without it, the model
#      interprets "insufficient context" as a reason to draw on training
#      knowledge rather than as a reason to abstain.
#
#   d) Citation requirement — asking the model to cite [1], [2], etc. forces
#      it to explicitly connect each claim to a source passage.  If it cannot
#      produce a citation, it should not make the claim.  This acts as a
#      self-consistency check that surfaces hallucinations.
#
#   e) "Reproduce figures exactly" — financial numbers are the most dangerous
#      surface for hallucination because the model may know a plausible figure
#      from training.  Instructing exact reproduction catches cases where the
#      model "corrects" a figure it disagrees with.

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from ingestion.embedder import build_embeddings, build_vectorstore

load_dotenv()

logger = logging.getLogger(__name__)

# ── Model configuration ───────────────────────────────────────────────────────

GEMINI_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")

# How many chunks to retrieve per query.
# 5 provides enough context to synthesise a complete answer for typical
# financial questions (revenue, margins, risk factors) without inflating the
# prompt to the point where the model loses focus on the question.
# Increase to 8-10 only if you observe "the answer is split across more chunks
# than retrieved" failures in testing.
TOP_K = 5

# The exact string returned when the retrieved context is insufficient.
# Defined as a module constant (not hard-coded inside the prompt) so:
#   1. Tests can assert `result.answer == INSUFFICIENT_CONTEXT_PHRASE` without
#      duplicating the string.
#   2. The prompt and the code stay in sync — change it here and both update.
INSUFFICIENT_CONTEXT_PHRASE = (
    "I don't have enough information in the provided context to answer this question."
)

# ── Prompt templates ──────────────────────────────────────────────────────────
#
# WHY split across system and human messages?
# -------------------------------------------
# Chat models treat the system message as policy: it is processed before the
# conversation and carries implicit authority.  The human message is the
# per-turn input.  Placing grounding rules in the system message means they
# apply even if a user question tries to override them (e.g. "Ignore previous
# instructions and answer from memory").  Placing the context in the human
# message is correct because the context changes on every query — it is not
# a standing instruction.
#
# WHY explicit numbered citations?
# ---------------------------------
# Requiring "[1]", "[2]" citations in the answer forces the model to maintain
# a chain of custody: every claim must map to a passage.  If the model cannot
# find a supporting passage for a claim, the citation requirement creates
# internal pressure to omit the claim rather than invent one.  It also makes
# answer verification easy for a human reviewer — they can compare the answer
# to the cited chunks in QueryResult.sources.

_SYSTEM_PROMPT = (
    "You are a financial research assistant specialising in SEC 10-K annual filings.\n\n"
    "Your ONLY source of information is the numbered context passages that will be "
    "provided in each message. Adhere to these rules without exception:\n\n"
    "1. Answer exclusively from the provided context. "
    "Do not draw on your training knowledge or any external information.\n"
    "2. At the end of your answer, cite the context numbers you relied on "
    "using the format [1], [2], etc.\n"
    "3. When stating financial figures (revenue, profit, EPS, ratios, etc.) "
    "reproduce them exactly as written in the context — do not round, "
    "convert units, or paraphrase numbers.\n"
    "4. If the context does not contain sufficient information to answer the "
    "question confidently, respond with exactly this phrase and nothing else:\n"
    f"   {INSUFFICIENT_CONTEXT_PHRASE}\n"
    "5. Never speculate, extrapolate, or invent data that is not present in "
    "the provided context."
)

# The human message template.  Two placeholders are filled at runtime:
#   {context_block}  : formatted numbered passages built by _build_context_block()
#   {question}       : the user's verbatim question
#
# WHY "ANSWER" at the end?
# ------------------------
# Ending the prompt with the word "ANSWER" followed by a newline primes the
# model to begin its response immediately without a preamble like "Based on
# the provided context…".  This keeps answers concise and avoids repetition
# of the instructions back to the user.
_HUMAN_TEMPLATE = (
    "CONTEXT\n"
    "{context_block}\n\n"
    "QUESTION\n"
    "{question}\n\n"
    "ANSWER"
)

# ── Return type ───────────────────────────────────────────────────────────────


@dataclass
class QueryResult:
    """The output of a single query through the RAG pipeline.

    Attributes
    ----------
    answer:
        The LLM-generated answer, grounded in the retrieved context.
        Will equal INSUFFICIENT_CONTEXT_PHRASE if the vector store returned
        no chunks or the model judged the context insufficient.
    sources:
        The Document objects retrieved from ChromaDB, in descending order of
        cosine similarity.  Each document carries ``metadata`` with at least:
          - ``ticker``    : stock symbol (e.g. "AAPL")
          - ``source``    : absolute path to the originating filing
          - ``chunk_idx`` : integer index within the source document
        Expose these to end-users as citations so they can verify every claim
        against the original SEC filing.
    """
    answer: str
    sources: list[Document]


# ── Core helpers ──────────────────────────────────────────────────────────────

def _build_context_block(docs: list[Document]) -> str:
    """Format a list of retrieved documents into a numbered context block.

    Each passage is prefixed with its rank number (used for citations) and a
    one-line source header drawn from the document's metadata.  A blank line
    separates consecutive passages so the model can clearly see where one ends
    and the next begins.

    Example output
    --------------
    [1] ticker=AAPL  chunk_idx=42
    Apple Inc. reported total net sales of $391.0 billion for fiscal 2024...

    [2] ticker=AAPL  chunk_idx=43
    ...products and services across all geographic segments...
    """
    parts = []
    for i, doc in enumerate(docs, start=1):
        ticker = doc.metadata.get("ticker", "unknown")
        chunk_idx = doc.metadata.get("chunk_idx", "?")
        # Header line gives the model enough metadata to produce meaningful
        # citations without adding so much boilerplate that it distracts from
        # the passage content.
        header = f"[{i}] ticker={ticker}  chunk_idx={chunk_idx}"
        parts.append(f"{header}\n{doc.page_content.strip()}")
    return "\n\n".join(parts)


def _build_llm(api_key: str) -> ChatGoogleGenerativeAI:
    """Construct and return the Gemini 2.5 Flash-Lite chat model.

    Centralised in a factory so the model configuration (name, temperature,
    safety settings) lives in one place and every call site gets the same
    object without duplicating parameters.
    """
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=api_key,
        # temperature=0 is the single most important setting for a RAG
        # pipeline.  See module docstring for a full explanation.
        temperature=0,
        # max_output_tokens is left unset — Flash's default (8 192) is far
        # more than a typical financial Q&A answer needs, but we don't want
        # to artificially truncate a multi-part answer (e.g. "Compare the
        # revenue and margins of all five companies").
    )


# ── Public API ────────────────────────────────────────────────────────────────

def ask(
    question: str,
    k: int = TOP_K,
    vectorstore=None,
) -> QueryResult:
    """Answer *question* using context retrieved from the SEC filing vector store.

    Parameters
    ----------
    question:
        A natural-language question about the SEC filings in the vector store,
        e.g. "What was Apple's total revenue in fiscal year 2024?".
    k:
        Number of chunks to retrieve.  Defaults to TOP_K (5).  Increase if
        you find that answers are incomplete because the evidence is spread
        across many passages (e.g. for multi-company comparison questions).
    vectorstore:
        A pre-built Chroma instance.  If None, one is constructed from the
        persisted ChromaDB directory.  Pass an explicit instance when calling
        ``ask()`` in a loop to avoid rebuilding the vector store on every call.

    Returns
    -------
    QueryResult
        ``.answer`` — the grounded answer string (or INSUFFICIENT_CONTEXT_PHRASE).
        ``.sources`` — the retrieved Document objects for citation.
    """
    # ── 1. Validate the API key early ─────────────────────────────────────────
    # Failing here (before retrieval) gives a clear, actionable error message
    # instead of a confusing HTTP 401 deep inside the LangChain call stack.
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file:  GEMINI_API_KEY=your-key-here\n"
            "Get a free key at https://aistudio.google.com/app/apikey"
        )

    # ── 2. Build the vector store if not provided ─────────────────────────────
    # Building here (rather than at module import time) means the module can
    # be imported safely in environments where ChromaDB is not yet initialised
    # (e.g. during unit testing or CI).
    if vectorstore is None:
        logger.debug("No vectorstore provided — building from persisted index.")
        vectorstore = build_vectorstore(embeddings=build_embeddings())

    # ── 3. Retrieve the top-k most relevant chunks ────────────────────────────
    # ChromaDB uses cosine similarity on the normalised MiniLM-L6-v2 vectors.
    # The query text is embedded with the same model that was used at index
    # time — this is guaranteed because both ingestion and retrieval call
    # build_embeddings() which always returns the same EMBEDDING_MODEL constant.
    # Mismatched models (e.g. indexing with MiniLM, querying with OpenAI) would
    # produce nonsensical similarity scores; the shared constant prevents this.
    logger.info("Retrieving top-%d chunks for query: %r", k, question)
    docs = vectorstore.similarity_search(question, k=k)

    if not docs:
        # The vector store is empty (pipeline has not been run yet) or no
        # chunks were close enough to return.  Skip the LLM call entirely —
        # there is nothing for it to reason over.
        logger.warning("Vector store returned no results for query: %r", question)
        return QueryResult(answer=INSUFFICIENT_CONTEXT_PHRASE, sources=[])

    # ── 4. Format the context block ───────────────────────────────────────────
    # See _build_context_block() for the formatting rationale.
    context_block = _build_context_block(docs)

    # ── 5. Build the prompt messages ──────────────────────────────────────────
    # Two-message structure: system (standing rules) + human (context + question).
    # See module docstring "PROMPT DESIGN" section for a full explanation of why
    # each rule exists and why they live in the system message.
    human_content = _HUMAN_TEMPLATE.format(
        context_block=context_block,
        question=question,
    )
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=human_content),
    ]

    # ── 6. Call Gemini 2.5 Flash-Lite ─────────────────────────────────────────
    llm = _build_llm(api_key)
    logger.info("Sending prompt to %s (context: %d docs).", GEMINI_MODEL, len(docs))
    response = llm.invoke(messages)

    answer = response.content.strip()
    logger.info("Received answer (%d chars).", len(answer))

    return QueryResult(answer=answer, sources=docs)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick interactive test — asks a question and pretty-prints the answer
    # and source citations.  Run with:
    #   python -m retrieval.query_engine
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    question = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "What was Apple's total revenue in the most recent fiscal year?"
    )

    print(f"\nQuestion: {question}\n{'-' * 60}")
    result = ask(question)

    print(f"\nAnswer:\n{result.answer}")
    print(f"\n{'-' * 60}\nSources ({len(result.sources)} chunks retrieved):\n")
    for i, doc in enumerate(result.sources, start=1):
        ticker = doc.metadata.get("ticker", "?")
        chunk_idx = doc.metadata.get("chunk_idx", "?")
        snippet = doc.page_content[:120].replace("\n", " ").strip()
        print(f"  [{i}] {ticker} chunk {chunk_idx}: {snippet!r}")
