# agent/financial_agent.py
#
# PURPOSE
# -------
# Implement a ReAct (Reasoning + Acting) agent that answers financial research
# questions by iteratively reasoning over SEC 10-K filings stored in ChromaDB.
#
# ReAct (Yao et al., 2022) alternates a tool call with a look at its result
# until the model answers.  That loop is what lets the agent search once per
# company for a comparison and try another query when the first one misses;
# the cost is that the query text is model output, which eval/EVALUATION.md
# finding 3 measures.
#
# HOW langgraph.prebuilt.create_react_agent WORKS
# --------------------------------------
# LangChain 1.2 replaced the old text-based ReAct loop (create_react_agent +
# AgentExecutor) with a LangGraph-backed tool-calling loop. This file uses
# langgraph.prebuilt.create_react_agent, which compiles that loop directly.
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
#   │  create_react_agent (compiled LangGraph StateGraph) │
#   │   recursion_limit=20 (≈10 tool-call round trips)    │
#   │  ┌──────────────────────────────────────────────┐    │
#   │  │   LLM: ChatGoogleGenerativeAI (Gemini)       │    │
#   │  │   system_prompt: financial analyst persona   │    │
#   │  │   Tools: search_filings                      │    │
#   │  │          list_available_companies            │    │
#   │  │          compare_companies                   │    │
#   │  │          lookup_financial_fact               │    │
#   │  │          compute_metric                      │    │
#   │  └──────────────────────────────────────────────┘    │
#   │                      │                               │
#   │      ┌───────────────▼──────────────┐                │
#   │      │  ChromaDB (chunk embeddings)  │                │
#   │      │  SQLite   (tagged XBRL facts) │                │
#   │      └──────────────────────────────┘                │
#   └──────────────────────────────────────────────────────┘

import logging
import os
import threading

from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from ingestion.embedder import build_vectorstore

load_dotenv()

logger = logging.getLogger(__name__)

# ── Model configuration ───────────────────────────────────────────────────────

LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")

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
# Guards that first build.  LangGraph's ToolNode runs the calls of one model step on worker threads, so two
# search_filings calls in the first step of a process (or of a restarted Space) both found the singleton empty and
# both built a Chroma client at once; six of eight failed with "Could not connect to tenant default_tenant".  The
# lock is taken only while the singleton is empty, so a warm process pays nothing for it.
_vectorstore_lock = threading.Lock()


def _get_vectorstore():
    """Return the module-level ChromaDB vectorstore, building it on first call.

    Using a module-level singleton avoids the ~1-2 second overhead of loading
    the sentence-transformer model and opening the ChromaDB SQLite files on
    every tool invocation.  Within a single agent run there may be 5–10 tool
    calls; caching the instance turns that overhead into a one-time cost.
    Safe to call from several threads at once: one of them builds it.
    """
    global _vectorstore
    if _vectorstore is None:
        with _vectorstore_lock:
            if _vectorstore is None:
                logger.info("Initialising ChromaDB vectorstore …")
                _vectorstore = build_vectorstore()
    return _vectorstore


# ── Terminal failure states ───────────────────────────────────────────────────
#
# A tool-calling loop can stop for reasons that are NOT an answer, and both of
# them were observed in evaluation on this corpus:
#
#   empty_answer     The model returns a final AIMessage with empty content and
#                    no tool call.  finish_reason is STOP — the API deliberately
#                    returned nothing.  Measured at 10/66 items (15%) on
#                    gemini-2.5-flash-lite; 0/10 on gemini-3.1-flash-lite and
#                    gemini-3.6-flash at the same prompt, k and retrieval.
#   recursion_limit  The graph exhausts recursion_limit before the model emits a
#                    final answer, and LangGraph returns its own placeholder
#                    string instead.
#
# Neither is a usable answer, and neither used to be detected anywhere: the
# agent returned the empty string, the API served it as a 200, and the eval
# harness handed it to RAGAS, which scored faithfulness as NaN and then EXCLUDED
# it from the mean — so the system's worst failures silently raised its score.
#
# Naming them here, once, gives every layer the same vocabulary.

RECURSION_LIMIT_MARKER = "need more steps to process this request"

OUTCOME_EMPTY_ANSWER = "empty_answer"
OUTCOME_RECURSION_LIMIT = "recursion_limit"


class AgentTerminalFailure(RuntimeError):
    """The agent stopped without producing a usable answer.

    Carries ``outcome`` so callers can branch on the specific failure without
    string-matching an error message.
    """

    outcome = "unknown"


class EmptyAnswerError(AgentTerminalFailure):
    """The agent's final message contained no text."""

    outcome = OUTCOME_EMPTY_ANSWER


class RecursionLimitError(AgentTerminalFailure):
    """The agent exhausted its step budget before answering."""

    outcome = OUTCOME_RECURSION_LIMIT


def content_text(content) -> str:
    """Flatten an AIMessage/BaseMessage content payload to plain text.

    Message content is not reliably a string.  Older Gemini models return it as
    one, but newer ones return a LIST of content blocks:

        [{"type": "text", "text": "...", "extras": {...}}]

    On the shipped agent model this is the common case, not the edge case — the
    66-item rerun recorded list content on 60 of 66 items
    (``eval/results/rerun66-af83fa6.json``, ``final_content_type``).  Anything
    that calls ``.strip()`` on the raw payload therefore raises AttributeError
    on most questions, and anything that ``str()``s it leaks a Python repr into
    the answer.

    This lives here, beside the terminal-state vocabulary, because the flattened
    string is what every layer must agree on: the two CLI entry points, both API
    routes, the MCP agent and the eval harness all classify and display the same
    text.  The API and the eval harness had each grown their own copy of this;
    the two CLI entry points and the MCP agent had none, and broke on list
    content.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def classify_terminal_state(answer) -> str | None:
    """Return a named terminal-failure outcome for *answer*, or None if usable.

    Accepts either a plain string or a raw message content payload, flattening
    it first, so that every caller — both CLI entry points, both API routes, the
    MCP agent and the eval harness — classifies the identical string the user
    would otherwise have been shown.
    """
    text = content_text(answer).strip()
    if not text:
        return OUTCOME_EMPTY_ANSWER
    if RECURSION_LIMIT_MARKER in text.lower():
        return OUTCOME_RECURSION_LIMIT
    return None


def raise_for_terminal_state(answer) -> str:
    """Return *answer* as flattened text, or raise the matching AgentTerminalFailure.

    Returns the flattened string rather than the input, so callers that pass raw
    message content get back something they can print, slice and ``.strip()``.
    """
    outcome = classify_terminal_state(answer)
    if outcome == OUTCOME_EMPTY_ANSWER:
        raise EmptyAnswerError(
            "The agent returned an empty final answer (model stopped without "
            "producing text)."
        )
    if outcome == OUTCOME_RECURSION_LIMIT:
        raise RecursionLimitError(
            "The agent exhausted its step budget before producing an answer."
        )
    return content_text(answer)


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
      - A 500-character snippet of the passage text
    Returns "No results found." if the vector store is empty or the query
    matches nothing above the similarity threshold.
    """
    # Every retrieval goes through retrieval/retriever.py, configured from the
    # RETRIEVAL_* environment.  The default is dense top-k over the shared
    # vectorstore — exactly what this tool always did — so a retrieval variant
    # is a configuration change, measured before it is switched on.
    from retrieval.retriever import get_retriever
    chunks = get_retriever().retrieve(query)   # k from RetrievalConfig (TOP_K unless RETRIEVAL_K is set)

    if not chunks:
        return "No results found."

    lines = []
    for i, c in enumerate(chunks, start=1):
        snippet = c.text[:500].replace("\n", " ").strip()
        lines.append(f"[{i}] ticker={c.ticker}  chunk_idx={c.chunk_idx}\n    {snippet}")

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
    from retrieval.retriever import get_retriever
    retriever = get_retriever()

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return "No tickers provided. Please supply a comma-separated list such as 'AAPL, MSFT'."

    # The question is passed to the retriever unmodified. It used to be
    # prefixed with "total net sales", which injected revenue vocabulary into
    # every comparison - so "compare Meta and Alphabet headcount" was embedded
    # as "total net sales compare Meta and Alphabet headcount" and retrieved
    # against the wrong passages. The prefix assumed all comparisons are about
    # revenue. Query composition is already the largest uncontrolled variable
    # in the evaluation (eval/EVALUATION.md finding 3); deliberately corrupting
    # the query the agent composed made it worse.
    search_query = question
    sections = []
    for ticker in ticker_list:
        chunks = retriever.retrieve(search_query, ticker=ticker)

        if not chunks:
            sections.append(f"=== {ticker} ===\nNo results found for this ticker.")
            continue

        lines = []
        for i, c in enumerate(chunks, start=1):
            snippet = c.text[:500].replace("\n", " ").strip()
            lines.append(f"  [{i}] chunk_idx={c.chunk_idx}\n      {snippet}")

        sections.append(f"=== {ticker} ===\n" + "\n\n".join(lines))

    return "\n\n".join(sections)


@tool
def lookup_financial_fact(ticker: str, concept: str, fiscal_year: int | None = None,
                          segment: str | None = None) -> str:
    """Look up an exact financial figure from the company's 10-K XBRL data. Every call needs BOTH ticker and concept: concept is REQUIRED, and one call returns one concept for one fiscal year, so make one call per figure and year, e.g. lookup_financial_fact(ticker="AAPL", concept="total net sales", fiscal_year=2024).

    Every headline number in a 10-K is machine-tagged with its concept, period
    and unit. This tool returns those tagged values, so it is the reliable way
    to get a figure: use it FIRST for revenue, net sales, net income, operating
    income, gross profit, EPS, total assets, cash, debt, operating cash flow,
    capital expenditures, share repurchases, and segment or product revenue
    (e.g. iPhone, Intelligent Cloud, Google Cloud, AWS, Reality Labs).

    Parameters
    ----------
    ticker:
        One of the indexed tickers, e.g. "AAPL".
    concept:
        REQUIRED on every call. Plain language ("total net sales", "net income",
        "diluted EPS") or an exact concept name ("us-gaap:Revenues").
    fiscal_year:
        The fiscal year wanted, e.g. 2025. Leave unset for the most recent
        fiscal year in the filing; the result says which year it used.
    segment:
        A segment or product name to restrict to, e.g. "iPhone", "AWS",
        "Google Cloud", "Intelligent Cloud". Leave unset for the consolidated
        total.

    Returns
    -------
    One "[n] ticker=... fact_id=..." entry per tagged value with its concept,
    period, value, unit and segment; or an explanation of what could not be
    resolved and what is available.
    """
    from retrieval.facts import format_fact_observation, get_fact_store

    try:
        store = get_fact_store()
    except FileNotFoundError as exc:
        return f"Fact database unavailable ({exc}). Use search_filings instead."
    rows, info = store.lookup(ticker, concept, fiscal_year, segment)
    return format_fact_observation(rows, info, concept)


@tool
def compute_metric(operation: str, a: float, b: float, n: int | None = None) -> str:
    """Do arithmetic on figures you looked up. Never compute in your head.

    operation is one of:
      difference   a - b
      sum          a + b
      ratio        a / b
      pct_change   (a - b) / b * 100      a = newer figure, b = older figure
      margin_pct   a / b * 100            a = profit line, b = revenue
      cagr_pct     compound annual growth from b to a over n periods

    Pass the figures exactly as returned by lookup_financial_fact (in the same
    unit for both). Quote the result as returned.
    """
    from retrieval.facts import compute

    out = compute(operation, a, b, n)
    if "error" in out:
        return f"Error: {out['error']}"
    n_note = f", n={n}" if n else ""
    return (f"[1] calc={out['operation']}\n"
            f"    {out['formula']} with a={a:,.10g}, b={b:,.10g}{n_note} = {out['result']:,.2f}")


# Every tool the agent is bound to, in binding order.  One list, so the agent factory and the schema fingerprint
# below can never disagree about what the model is shown.
TOOLS = [search_filings, list_available_companies, compare_companies, lookup_financial_fact, compute_metric]


# ── System prompt ─────────────────────────────────────────────────────────────
#
# create_react_agent() takes a plain `prompt` string rather
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
    "3. Do NOT put citations, ticker symbols, or chunk references in your answer "
    "text. The interface shows the exact sources separately, so keep the prose "
    "clean, with no inline or parenthetical references like '(AAPL chunk 42)'.\n"
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
    "resolve the ambiguity by searching.\n"
    "7. For any exact financial figure - revenue or net sales, net income, "
    "operating income, gross profit, EPS, total assets, cash, debt, cash flow, "
    "capital expenditures, buybacks, or a segment or product figure such as "
    "iPhone, Intelligent Cloud, Google Cloud, AWS or Reality Labs - call "
    "lookup_financial_fact FIRST. It returns the value tagged in the filing's "
    "XBRL with its fiscal year, which is more reliable than reading a table out "
    "of search results. Every lookup_financial_fact call needs both ticker and "
    "concept: concept is REQUIRED, even when a call differs from the previous one "
    "only by fiscal_year, so to compare two years or two figures make one call "
    "for each and repeat the concept, for example lookup_financial_fact(ticker="
    "\"AAPL\", concept=\"total net sales\", fiscal_year=2024). Pass fiscal_year "
    "when the question names one. When it "
    "does not, the tool uses the most recent fiscal year in the filing: report "
    "that figure and state the year. Use search_filings for narrative, "
    "qualitative or policy questions, and when the fact lookup finds nothing.\n"
    "8. Never do arithmetic yourself. For a growth rate, difference, margin or "
    "ratio, call compute_metric with the figures you looked up and report its "
    "result."
)

# Appended to the system prompt ONLY when AGENT_BATCH_RULE=on (system_prompt() below).  LangGraph's tool node already
# runs the calls of one model step concurrently, and the model sometimes issues several at once; this asks it to do
# so whenever a question needs the same lookup for several companies, years or concepts.  It repeats that every call
# in the batch needs its required arguments so that asking for several calls at once cannot make the omitted-`concept`
# failure (finding 23) more likely.  Off by default: measured on the full benchmark it saved model calls and one
# answer it changed was wrong and verified, so the default is off (eval/EVALUATION.md, finding 24).
_BATCH_RULE = (
    "\n9. When a question needs the same lookup for several companies, years or "
    "concepts, issue all of those calls together in ONE step rather than one per "
    "step, and give every call all of its required arguments (for "
    "lookup_financial_fact that includes concept). For a figure the filings tag, "
    "make one lookup_financial_fact call per company or year in that step. For "
    "anything else about several companies (headcount, state of incorporation, a "
    "policy), make ONE compare_companies call that lists every ticker instead of "
    "searching one company at a time. Answer from what you have before the step "
    "budget runs out."
)

_ON = ("on", "true", "1", "yes")
_OFF = ("off", "false", "0", "no", "")


def batch_rule_enabled() -> bool:
    """AGENT_BATCH_RULE=on|off (default off).  Anything else is an error rather than a silent off."""
    raw = (os.getenv("AGENT_BATCH_RULE") or "").strip().lower()
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    raise ValueError(f"AGENT_BATCH_RULE must be on or off, got {raw!r}")


def system_prompt() -> str:
    """The system prompt the agent runs with: _SYSTEM_PROMPT, plus the batching rule when AGENT_BATCH_RULE is on.

    With the switch off this is _SYSTEM_PROMPT itself (rule 7 carries the concept-is-REQUIRED wording of finding 23),
    so the recorded prompt version is the hash of that text alone; the rule only ever appends to it.
    """
    return _SYSTEM_PROMPT + (_BATCH_RULE if batch_rule_enabled() else "")


# ── Agent factory ─────────────────────────────────────────────────────────────

def build_llm() -> ChatGoogleGenerativeAI:
    """The agent's model at temperature 0.

    One factory for every model call the system makes — the agent loop here,
    and the output contract's structuring call (agent/contract.py) — so the
    two can never drift onto different models or settings.

    Raises
    ------
    EnvironmentError
        If GEMINI_API_KEY is not set.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file:  GEMINI_API_KEY=your-key-here\n"
            "Get a free key at https://aistudio.google.com/app/apikey"
        )
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=api_key,
        # temperature=0 keeps the model deterministic.  In a tool-calling loop
        # the model must reliably decide when to stop calling tools and produce
        # a final answer.  Any temperature above 0 risks stochastic variation
        # that can cause unnecessary extra tool calls or premature stopping.
        temperature=0,
    )


def tool_schema_version() -> str:
    """A short fingerprint of everything the model is shown about the tools: each one's name, description and argument schema.

    The evaluation harness fingerprints the system prompt (eval/run_eval.py::_prompt_version) but a tool's
    docstring or argument schema is not part of it, so a change to either would leave the prompt version, and any
    cache keyed on it, untouched.  Recording this beside it makes such a change visible in every results file, and
    the harness tags it into the agent-output cache key (eval/run_eval.py::_cache_key) so the change cannot be
    served from a cache generated before it.
    The in-process tools only: the MCP server restates them in mcp_server/server.py, which the contract test pins.
    """
    import hashlib
    import json

    from langchain_core.utils.function_calling import convert_to_openai_tool

    spec = [convert_to_openai_tool(t) for t in TOOLS]
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def build_agent_executor():
    """Construct and return a ready-to-use LangGraph agent (LangChain 1.2 API).

    LangChain 1.2 replaced the old text-parsing ReAct loop + AgentExecutor with
    a LangGraph-backed one. This uses langgraph.prebuilt.create_react_agent,
    which compiles a LangGraph StateGraph.  The reasoning loop is equivalent —
    the model iterates tool calls until it produces a final answer — but tool
    invocations are structured JSON rather than parsed text, eliminating the
    formatting errors that required handle_parsing_errors in the old API.

    Assembly steps
    --------------
    1. Build the Gemini LLM (temperature=0 for deterministic financial answers).
    2. Collect the five domain tools into a list.
    3. Call create_react_agent() with the LLM, tools, and prompt.  Internally
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
    llm = build_llm()

    tools = list(TOOLS)

    # create_react_agent() from langgraph.prebuilt binds the LLM and tools,
    # then compiles a LangGraph StateGraph that drives the model→tools→model
    # loop automatically.  prompt is prepended as a system message each turn.
    return create_react_agent(model=llm, tools=tools, prompt=system_prompt())


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
    # Guard the public entry point: a non-answer is raised as a named failure
    # rather than returned as a string that looks like a result.
    return raise_for_terminal_state(result["messages"][-1].content)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Smoke test: run a multi-company comparison question that exercises the
    # search tools — list_available_companies (to confirm tickers), then
    # compare_companies or lookup_financial_fact per company, then synthesis.
    #   python -m agent.financial_agent
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_question = "Compare Apple and Microsoft revenue for their most recent fiscal year"

    print(f"\nQuestion: {test_question}")
    print("-" * 70)

    answer = run_agent(test_question)

    print("\n" + "-" * 70)
    print(f"Final Answer:\n{answer}")
