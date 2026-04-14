# agent/financial_agent.py
#
# PURPOSE
# -------
# Implement a ReAct (Reasoning + Acting) agent that answers financial research
# questions by iteratively reasoning over SEC 10-K filings stored in ChromaDB.
#
# WHAT IS ReAct?
# --------------
# ReAct (Yao et al., 2022 — https://arxiv.org/abs/2210.03629) is a prompting
# strategy that interleaves chain-of-thought *reasoning* with *acting* (tool
# calls), forming a loop:
#
#   Thought  → the model explains what it needs to do next
#   Action   → the model calls a tool with specific inputs
#   Observation → the tool's return value is injected back into the context
#   (repeat until the model produces a Final Answer)
#
# This loop gives the agent two capabilities a single LLM call lacks:
#   1. Multi-step retrieval — it can call search_filings for AAPL, then again
#      for MSFT, then synthesise both results, rather than getting one shot.
#   2. Self-correction — if an observation is unhelpful, the Thought step can
#      recognise that and try a different query or tool before answering.
#
# HOW LangChain 1.2 create_agent WORKS
# --------------------------------------
# LangChain 1.2 replaced the old text-based ReAct loop (create_react_agent +
# AgentExecutor) with a LangGraph-backed tool-calling loop (create_agent).
# The mechanics differ in implementation but the reasoning pattern is the same:
#
#   model call  → generates a tool-call request (structured JSON, not text)
#   tool node   → executes the requested tool, captures the result
#   model call  → sees the result as a ToolMessage, decides next step
#   (repeat until the model produces a plain AIMessage with no tool calls)
#
# Advantages of the new approach:
#   - No text parsing — tool calls are structured (no "Action: X" regex needed)
#   - Native parallel tool calls — model can request multiple tools at once
#   - Built-in recursion guard — recursion_limit replaces max_iterations
#
# ARCHITECTURE
# ------------
#   ┌──────────────────────────────────────────────────────┐
#   │  create_agent (compiled LangGraph StateGraph)        │
#   │   recursion_limit=20 (≈10 tool-call round trips)    │
#   │  ┌──────────────────────────────────────────────┐    │
#   │  │   LLM: ChatGoogleGenerativeAI (Gemini)       │    │
#   │  │   system_prompt: financial analyst persona   │    │
#   │  │   Tools: search_filings                      │    │
#   │  │           list_available_companies            │    │
#   │  │           compare_companies                  │    │
#   │  └──────────────────────────────────────────────┘    │
#   │                      │                               │
#   │             ┌────────▼────────┐                      │
#   │             │  ChromaDB       │                      │
#   │             │  (SEC 10-K      │                      │
#   │             │   embeddings)   │                      │
#   │             └─────────────────┘                      │
#   └──────────────────────────────────────────────────────┘

import logging
import os

from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from ingestion.embedder import build_vectorstore

load_dotenv()

logger = logging.getLogger(__name__)

# ── Model configuration ───────────────────────────────────────────────────────

LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")

# Tickers that have been ingested into the vector store.  Used by
# list_available_companies() so the agent (and its callers) can discover what
# data is available without querying ChromaDB.
INDEXED_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]

# Number of chunks returned per similarity search call.  5 gives the agent
# enough evidence to answer a single-company question without flooding the
# context window.  compare_companies() calls search per ticker so the total
# context scales proportionally with the number of companies requested.
TOP_K = 5

# ── Vectorstore (module-level singleton) ─────────────────────────────────────
#
# Building the vectorstore is deferred to first use inside each tool rather
# than at import time, so the module can be imported safely in environments
# where the ChromaDB index has not yet been built (tests, CI, partial installs).
# The reference is cached here to avoid rebuilding on every tool call within a
# single agent run.
_vectorstore = None


def _get_vectorstore():
    """Return the module-level ChromaDB vectorstore, building it on first call.

    Using a module-level singleton avoids the ~1-2 second overhead of loading
    the sentence-transformer model and opening the ChromaDB SQLite files on
    every tool invocation.  Within a single agent run there may be 5–10 tool
    calls; caching the instance turns that overhead into a one-time cost.
    """
    global _vectorstore
    if _vectorstore is None:
        logger.info("Initialising ChromaDB vectorstore …")
        _vectorstore = build_vectorstore()
    return _vectorstore


# ── Tools ─────────────────────────────────────────────────────────────────────
#
# Each tool is decorated with @tool, which wraps the function so LangChain can:
#   - Extract its name (function name → tool name the LLM writes in "Action:")
#   - Extract its description (docstring → shown to the LLM in {tools} slot)
#   - Call it and capture its return value as an Observation
#
# IMPORTANT: docstrings are the tool's "API contract" with the LLM.  They must
# be precise about inputs and outputs or the model will misuse the tool.

@tool
def search_filings(query: str) -> str:
    """Search SEC 10-K filings for passages relevant to the given query.

    Performs a semantic (embedding-based) similarity search across all indexed
    filings and returns the top 5 most relevant text chunks together with their
    source metadata.

    Use this tool for open-ended questions that do not require per-company
    filtering (e.g. "What are the main risk factors across the portfolio?").
    For questions that explicitly compare specific companies, prefer
    compare_companies instead.

    Parameters
    ----------
    query:
        A natural-language question or topic, e.g.
        "Apple revenue fiscal year 2024" or "cloud segment growth drivers".

    Returns
    -------
    A formatted string listing up to 5 chunks.  Each entry contains:
      - Rank, ticker symbol, and chunk index (for citation)
      - A 300-character snippet of the passage text
    Returns "No results found." if the vector store is empty or the query
    matches nothing above the similarity threshold.
    """
    vs = _get_vectorstore()
    docs = vs.similarity_search(query, k=TOP_K)

    if not docs:
        return "No results found."

    lines = []
    for i, doc in enumerate(docs, start=1):
        ticker    = doc.metadata.get("ticker", "unknown")
        chunk_idx = doc.metadata.get("chunk_idx", "?")
        snippet   = doc.page_content[:500].replace("\n", " ").strip()
        lines.append(f"[{i}] ticker={ticker}  chunk_idx={chunk_idx}\n    {snippet}")

    return "\n\n".join(lines)


@tool
def list_available_companies() -> str:
    """Return the list of company tickers whose 10-K filings are indexed.

    Use this tool first when a user asks a question that could apply to any
    company, or when you need to confirm which companies' data is available
    before deciding which ones to include in a comparison.

    Returns
    -------
    A comma-separated string of stock tickers, e.g. "AAPL, MSFT, GOOGL, AMZN, META".
    """
    return ", ".join(INDEXED_TICKERS)


@tool
def compare_companies(question: str, tickers: str) -> str:
    """Retrieve 10-K filing passages for each specified company and return them together.

    For each ticker, this tool runs a separate similarity search filtered to
    that company's filings, then combines all results into a single response.
    This gives the LLM the raw evidence it needs to write a side-by-side
    comparison rather than mixing chunks from different companies arbitrarily.

    Use this tool when the user explicitly asks to compare two or more companies
    (e.g. "Compare Apple and Microsoft revenue") or when per-company context
    separation is important for accuracy.

    Parameters
    ----------
    question:
        The comparison question or topic to search for within each company's
        filings, e.g. "total revenue fiscal year 2024" or "cloud segment growth".
    tickers:
        Comma-separated ticker symbols to include, e.g. "AAPL, MSFT".
        Each ticker must match one of the values returned by
        list_available_companies().

    Returns
    -------
    A formatted string with a labelled section for each company containing up
    to 5 relevant chunks.  Returns a note for any ticker that has no results.
    """
    vs = _get_vectorstore()

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return "No tickers provided. Please supply a comma-separated list such as 'AAPL, MSFT'."

    search_query = f"total net sales {question}"
    sections = []
    for ticker in ticker_list:
        docs = vs.similarity_search(
            search_query,
            k=TOP_K,
            filter={"ticker": ticker},
        )

        if not docs:
            sections.append(f"=== {ticker} ===\nNo results found for this ticker.")
            continue

        lines = []
        for i, doc in enumerate(docs, start=1):
            chunk_idx = doc.metadata.get("chunk_idx", "?")
            snippet   = doc.page_content[:500].replace("\n", " ").strip()
            lines.append(f"  [{i}] chunk_idx={chunk_idx}\n      {snippet}")

        sections.append(f"=== {ticker} ===\n" + "\n\n".join(lines))

    return "\n\n".join(sections)


# ── System prompt ─────────────────────────────────────────────────────────────
#
# create_agent() in LangChain 1.2 takes a plain system_prompt string rather
# than a PromptTemplate.  The tool list and tool schemas are bound to the LLM
# automatically via tool-calling (structured JSON), so there is no need for
# {tools} / {tool_names} / {agent_scratchpad} placeholders.
#
# WHY a custom prompt?
# --------------------
# The default is no system prompt at all.  A domain-specific prompt improves
# answer quality in two ways:
#   1. Persona — "senior financial research analyst" primes the model to use
#      financial vocabulary and to demand numerical precision.
#   2. Grounding rules — instructing the model to cite tickers and chunk
#      indices, reproduce figures verbatim, and decline to speculate reduces
#      hallucination in financial Q&A where wrong numbers are actively harmful.

_SYSTEM_PROMPT = (
    "You are a senior financial research analyst specialising in SEC 10-K annual "
    "filings. Answer questions using ONLY the evidence you retrieve via the "
    "available tools. Rules:\n"
    "1. Never speculate or use knowledge not present in the retrieved passages.\n"
    "2. Reproduce financial figures (revenue, EPS, margins, etc.) exactly as "
    "written in the source — do not round or paraphrase numbers.\n"
    "3. Cite the ticker symbol and chunk_idx of every passage you rely on "
    "(e.g. 'AAPL chunk 42, MSFT chunk 17').\n"
    "4. If the retrieved context is insufficient, say so explicitly rather than "
    "guessing.\n"
    "5. When searching for financial figures, use specific terms like total net "
    "sales, operating income, net income rather than generic terms like revenue. "
    "Include the company name and fiscal year in your search queries. For example, "
    "search for total net sales Apple fiscal year 2025 rather than just revenue.\n"
    "6. Always use your tools to search for information before asking clarifying "
    "questions. If a query is ambiguous about the fiscal year, search for the "
    "most recent data available. If a query asks to compare companies without "
    "specifying which ones, use list_available_companies first to discover what's "
    "available, then proceed. Never ask the user for clarification when you can "
    "resolve the ambiguity by searching."
)


# ── Agent factory ─────────────────────────────────────────────────────────────

def build_agent_executor():
    """Construct and return a ready-to-use LangGraph agent (LangChain 1.2 API).

    LangChain 1.2 replaced create_react_agent + AgentExecutor with create_agent,
    which compiles a LangGraph StateGraph.  The reasoning loop is equivalent —
    the model iterates tool calls until it produces a final answer — but tool
    invocations are structured JSON rather than parsed text, eliminating the
    formatting errors that required handle_parsing_errors in the old API.

    Assembly steps
    --------------
    1. Build the Gemini LLM (temperature=0 for deterministic financial answers).
    2. Collect the three domain tools into a list.
    3. Call create_agent() with the LLM, tools, and system prompt.  Internally
       this builds a two-node LangGraph (model node ↔ tool node) that loops
       until the model emits an AIMessage with no tool calls.
    4. The recursion_limit passed at invoke time caps iterations.  Each full
       tool-call round trip uses 2 graph steps (model → tool node), so
       recursion_limit=20 ≈ 10 tool-call iterations.

    Returns
    -------
    A compiled LangGraph StateGraph ready to accept
    ``.invoke({"messages": [("human", question)]})`` calls.

    Raises
    ------
    EnvironmentError
        If GEMINI_API_KEY is not set in the environment.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file:  GEMINI_API_KEY=your-key-here\n"
            "Get a free key at https://aistudio.google.com/app/apikey"
        )

    llm = ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=api_key,
        # temperature=0 keeps the model deterministic.  In a tool-calling loop
        # the model must reliably decide when to stop calling tools and produce
        # a final answer.  Any temperature above 0 risks stochastic variation
        # that can cause unnecessary extra tool calls or premature stopping.
        temperature=0,
    )

    tools = [search_filings, list_available_companies, compare_companies]

    # create_react_agent() from langgraph.prebuilt binds the LLM and tools,
    # then compiles a LangGraph StateGraph that drives the model→tools→model
    # loop automatically.  prompt is prepended as a system message each turn.
    return create_react_agent(model=llm, tools=tools, prompt=_SYSTEM_PROMPT)


def run_agent(question: str) -> str:
    """Run the financial agent on a single question and return its answer.

    This is the primary public entry point for the agent layer.  It builds a
    fresh compiled graph on each call (the vectorstore singleton is reused) and
    returns the content of the model's final AIMessage.

    The graph is invoked with a messages list — the standard LangGraph input
    format.  After the tool-calling loop completes, the result contains the
    full conversation history; we return only the last message's content, which
    is the model's final answer after all tool observations have been seen.

    Parameters
    ----------
    question:
        A natural-language question about the SEC 10-K filings in the index,
        e.g. "What was Microsoft's operating income in fiscal year 2024?"

    Returns
    -------
    The agent's final answer as a plain string.
    """
    agent = build_agent_executor()
    logger.info("Running agent on question: %r", question)
    result = agent.invoke(
        {"messages": [("human", question)]},
        # recursion_limit caps the number of LangGraph node visits.  Each
        # tool-call round trip uses 2 steps (model node + tool node), so
        # limit=20 allows up to ~10 tool calls before the graph stops.
        config={"recursion_limit": 20},
    )
    return result["messages"][-1].content


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Smoke test: run a multi-company comparison question that exercises all
    # three tools — list_available_companies (to confirm tickers), then
    # compare_companies (to retrieve per-company evidence), then synthesis.
    #   python -m agent.financial_agent
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_question = "Compare Apple and Microsoft revenue for their most recent fiscal year"

    print(f"\nQuestion: {test_question}")
    print("─" * 70)

    answer = run_agent(test_question)

    print("\n" + "─" * 70)
    print(f"Final Answer:\n{answer}")
