# mcp_server/server.py
#
# PURPOSE
# -------
# Expose the three SEC filing research tools (search_filings,
# list_available_companies, compare_companies) as an MCP server so that any
# MCP-compatible client — including LangChain, Claude Desktop, or a custom
# agent — can call them over a network transport rather than importing Python
# functions directly.
#
# WHAT IS MCP?
# ------------
# The Model Context Protocol (MCP, https://modelcontextprotocol.io) is an
# open standard that defines a JSON-RPC 2.0 wire format for LLMs to discover
# and call tools hosted by external servers.  Key concepts:
#
#   Tool       — a named, typed function the model can call (like a REST
#                endpoint).  Described by a JSON Schema so the model knows
#                what parameters to pass.
#   Transport  — the byte-level channel.  We use "streamable-http" (HTTP/1.1
#                with optional SSE streaming) so the server is reachable from
#                any language and from remote machines.
#   Lifespan   — an async context manager that runs once at startup/shutdown,
#                used here to load the ChromaDB index once and share it across
#                all requests via ctx.request_context.lifespan_context.
#
# WHY FastMCP?
# ------------
# FastMCP (bundled with the `mcp` SDK) is the high-level Python server API.
# It handles:
#   - Tool registration via the @mcp.tool() decorator
#   - JSON Schema generation from Python type hints and Pydantic Field metadata
#   - Transport negotiation (stdio / SSE / streamable-http)
#   - Context injection: ctx.request_context.lifespan_context carries state
#     initialised in the lifespan hook into every tool call, avoiding globals.
#     (Note: older docs/tutorials call this attribute ``lifespan_state`` —
#     in mcp >= 1.9 it is named ``lifespan_context`` and holds the raw value
#     yielded by the lifespan async context manager.)
#
# ARCHITECTURE
# ------------
#                          ┌─────────────────────────────────┐
#   MCP client             │   FastMCP server (port 8000)    │
#   (LangChain /           │                                 │
#    Claude Desktop /  ────► search_filings                  │
#    custom agent)    ────► list_available_companies   ──────► ChromaDB
#                    ────► compare_companies                  │
#                          │                                 │
#                          │  lifespan: vectorstore loaded   │
#                          │  once at startup                │
#                          └─────────────────────────────────┘
#
# TRANSPORT: streamable-http
# --------------------------
# "streamable-http" listens on a plain HTTP port and supports optional
# Server-Sent Events (SSE) streaming for long-running tool calls.  It is the
# recommended transport for networked deployments because:
#   - It works through standard HTTP reverse proxies (nginx, Cloudflare).
#   - Both streaming and non-streaming clients are supported on the same port.
#   - No WebSocket upgrade is required (simpler firewall rules).
#
# RUNNING THE SERVER
# ------------------
#   # From the repo root:
#   python -m mcp_server.server
#
#   # Or via the MCP CLI (hot-reload during development):
#   mcp dev mcp_server/server.py
#
# The server listens on http://0.0.0.0:8000 by default.
# MCP clients connect to http://<host>:8000/mcp/

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator

from pydantic import BaseModel, Field

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ingestion.embedder import build_vectorstore
from retrieval.facts import FactStore, compute, format_fact_observation
from retrieval.retriever import RetrievalConfig, Retriever

logger = logging.getLogger(__name__)

# ── Constants (mirror agent/financial_agent.py) ───────────────────────────────

# Tickers present in the ChromaDB index.  Returned by list_available_companies
# without hitting the database, keeping that tool instant.
INDEXED_TICKERS: list[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]

# Number of chunks returned per similarity search call.  Five gives the model
# enough evidence for a single-company question without flooding the context.
# compare_companies multiplies this by the number of tickers requested.
TOP_K: int = 5


# ── Lifespan: load ChromaDB once at server startup ────────────────────────────
#
# FastMCP calls this async context manager once when the server starts and once
# when it shuts down.  Whatever the generator yields becomes lifespan_context —
# a dict shared across all tool calls via ctx.request_context.lifespan_context.
#
# Using lifespan avoids:
#   - Module-level globals that are hard to test and reset.
#   - Per-request re-loading of the embedding model + SQLite index (~1–2 s each).
#
# The vectorstore is thread-safe for reads (similarity_search does not mutate
# state), so sharing one instance across concurrent requests is safe.

@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Load ChromaDB once at startup; tear down on shutdown."""
    logger.info("MCP server starting — loading ChromaDB vectorstore …")
    vectorstore = build_vectorstore()
    logger.info("ChromaDB vectorstore ready.")
    # Same retriever the in-process tools use, configured from RETRIEVAL_*
    # env vars, built once so a reranker or BM25 index loads once per process.
    retriever = Retriever(RetrievalConfig.from_env(), vectorstore=vectorstore)
    logger.info("Retriever config: %s", retriever.config.as_dict())
    try:
        facts = FactStore()
        logger.info("XBRL fact store ready (%s).", ", ".join(facts.tickers()))
    except FileNotFoundError as exc:
        facts = None
        logger.warning("XBRL fact store unavailable: %s — lookup_financial_fact will say so.", exc)
    try:
        yield {"vectorstore": vectorstore, "retriever": retriever, "facts": facts}
    finally:
        # ChromaDB (LangChain Chroma wrapper) has no explicit close() method;
        # the underlying sqlite3 connection is managed by the Chroma client and
        # released when the object is garbage-collected.  Log the shutdown so
        # operators can confirm clean teardown in server logs.
        logger.info("MCP server shutting down — releasing vectorstore.")


# ── FastMCP server instance ───────────────────────────────────────────────────

mcp = FastMCP(
    name="sec_filings_mcp",
    lifespan=_lifespan,
    host="0.0.0.0",
    port=8000,
    instructions=(
        "SEC 10-K filing research server. "
        "Use search_filings for open-ended queries across all companies, "
        "list_available_companies to discover indexed tickers, and "
        "compare_companies for side-by-side retrieval of per-company evidence."
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# ── Pydantic input models ─────────────────────────────────────────────────────
#
# FastMCP generates the JSON Schema that clients receive during tool discovery
# from Python type annotations.  Using Pydantic Field() on Annotated parameters
# embeds description strings directly into the schema, giving the calling model
# precise guidance on what each argument means.
#
# We define lightweight models here for tools that take multiple parameters so
# that the schema groups related fields clearly.  Single-parameter tools use
# Annotated[str, Field(...)] inline for brevity.

class CompareCompaniesInput(BaseModel):
    """Input schema for the compare_companies tool."""

    question: Annotated[
        str,
        Field(
            description=(
                "The comparison question or topic to search for within each "
                "company's filings, e.g. 'total revenue fiscal year 2024' or "
                "'cloud segment growth drivers'."
            )
        ),
    ]
    tickers: Annotated[
        str,
        Field(
            description=(
                "Comma-separated ticker symbols to compare, e.g. 'AAPL, MSFT'. "
                "Each ticker must be one of the values returned by "
                "list_available_companies."
            )
        ),
    ]


# ── Tools ─────────────────────────────────────────────────────────────────────
#
# Each tool is an async function decorated with @mcp.tool().
#
# ANNOTATION FLAGS
# ----------------
# readOnlyHint=True  — signals that the tool never mutates persistent state
#   (it only reads from ChromaDB).  Clients can use this hint to decide whether
#   to require user confirmation before calling the tool.
# openWorldHint=False — signals that the tool draws only from a known, bounded
#   data set (the five indexed filings), not from the open internet.  This
#   helps the model calibrate its confidence in the tool's coverage.
#
# CONTEXT PARAMETER
# -----------------
# FastMCP injects the Context argument automatically when the parameter is
# typed as `ctx: Context`.  It is NOT part of the tool's public schema — the
# client never needs to supply it.

@mcp.tool(
    description=(
        "Search SEC 10-K filings for passages relevant to the given query. "
        "Performs a semantic (embedding-based) similarity search across all "
        "indexed filings and returns the top 5 most relevant text chunks with "
        "their source metadata (ticker + chunk_idx for citation). "
        "Use for open-ended questions that do not require per-company filtering. "
        "For explicit company comparisons, prefer compare_companies instead."
    ),
    annotations={
        "readOnlyHint": True,
        "openWorldHint": False,
    },
)
async def search_filings(
    query: Annotated[
        str,
        Field(
            description=(
                "A natural-language question or topic, e.g. "
                "'Apple revenue fiscal year 2024' or "
                "'cloud segment growth drivers'. "
                "Include the company name and fiscal year for precise results."
            )
        ),
    ],
    ctx: Context,
) -> str:
    """Return the top-5 most relevant 10-K filing chunks for the query."""
    retriever = ctx.request_context.lifespan_context["retriever"]

    await ctx.info(f"Searching filings for: {query!r}")
    chunks = retriever.retrieve(query, k=TOP_K)

    if not chunks:
        return "No results found."

    lines = []
    for i, c in enumerate(chunks, start=1):
        snippet = c.text[:500].replace("\n", " ").strip()
        lines.append(f"[{i}] ticker={c.ticker}  chunk_idx={c.chunk_idx}\n    {snippet}")

    return "\n\n".join(lines)


@mcp.tool(
    description=(
        "Return the list of company tickers whose 10-K filings are indexed. "
        "Call this first when a question could apply to any company, or to "
        "confirm which tickers are valid before passing them to compare_companies."
    ),
    annotations={
        "readOnlyHint": True,
        "openWorldHint": False,
    },
)
async def list_available_companies(ctx: Context) -> str:
    """Return a comma-separated string of all indexed stock tickers."""
    await ctx.info("Returning list of indexed companies.")
    return ", ".join(INDEXED_TICKERS)


@mcp.tool(
    description=(
        "Retrieve 10-K filing passages for each specified company and return "
        "them together in labelled sections. For each ticker, runs a separate "
        "similarity search filtered to that company's filings, then combines "
        "all results so the model can write a grounded side-by-side comparison. "
        "Use when the user explicitly asks to compare two or more companies."
    ),
    annotations={
        "readOnlyHint": True,
        "openWorldHint": False,
    },
)
async def compare_companies(
    question: Annotated[
        str,
        Field(
            description=(
                "The comparison question or topic to search for within each "
                "company's filings, e.g. 'total revenue fiscal year 2024' or "
                "'cloud segment growth drivers'."
            )
        ),
    ],
    tickers: Annotated[
        str,
        Field(
            description=(
                "Comma-separated ticker symbols to compare, e.g. 'AAPL, MSFT'. "
                "Each ticker must be one of the values returned by "
                "list_available_companies."
            )
        ),
    ],
    ctx: Context,
) -> str:
    """Return per-company filing passages grouped under labelled headers."""
    retriever = ctx.request_context.lifespan_context["retriever"]

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return (
            "No tickers provided. "
            "Please supply a comma-separated list such as 'AAPL, MSFT'."
        )

    await ctx.info(f"Comparing companies: {ticker_list} on topic: {question!r}")

    # The question is passed to the retriever unmodified. It used to be
    # prefixed with "total net sales", which injected revenue vocabulary into
    # every comparison - so "compare Meta and Alphabet headcount" was embedded
    # as "total net sales compare Meta and Alphabet headcount" and retrieved
    # against the wrong passages. The prefix assumed all comparisons are about
    # revenue. Query composition is already the largest uncontrolled variable
    # in the evaluation (eval/EVALUATION.md finding 3); deliberately corrupting
    # the query the agent composed made it worse.
    search_query = question

    sections: list[str] = []
    for ticker in ticker_list:
        chunks = retriever.retrieve(search_query, k=TOP_K, ticker=ticker)

        if not chunks:
            sections.append(f"=== {ticker} ===\nNo results found for this ticker.")
            continue

        lines: list[str] = []
        for i, c in enumerate(chunks, start=1):
            snippet = c.text[:500].replace("\n", " ").strip()
            lines.append(f"  [{i}] chunk_idx={c.chunk_idx}\n      {snippet}")

        sections.append(f"=== {ticker} ===\n" + "\n\n".join(lines))

    return "\n\n".join(sections)


@mcp.tool(
    description=(
        "Look up an exact financial figure from a company's 10-K XBRL data: revenue, "
        "net income, operating income, EPS, total assets, cash flow, capital expenditures, "
        "or a segment/product figure (iPhone, Intelligent Cloud, Google Cloud, AWS, Reality "
        "Labs). Returns the tagged value with its fiscal year, period and unit. Use FIRST for "
        "any headline number; leave fiscal_year unset for the most recent year in the filing."
    ),
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def lookup_financial_fact(
    ticker: Annotated[str, Field(description="Indexed ticker, e.g. 'AAPL'.")],
    concept: Annotated[str, Field(description="Plain language ('total net sales', 'diluted EPS') or a concept name ('us-gaap:Revenues').")],
    ctx: Context,
    fiscal_year: Annotated[int | None, Field(description="Fiscal year wanted, e.g. 2025. Unset = most recent in the filing.")] = None,
    segment: Annotated[str | None, Field(description="Segment or product to restrict to, e.g. 'iPhone', 'AWS'. Unset = consolidated.")] = None,
) -> str:
    """Return tagged XBRL facts as '[n] ticker=... fact_id=...' entries."""
    store = ctx.request_context.lifespan_context.get("facts")
    if store is None:
        return "Fact database unavailable on this server. Use search_filings instead."
    await ctx.info(f"Fact lookup: {ticker} {concept!r} fy={fiscal_year} segment={segment!r}")
    rows, info = store.lookup(ticker, concept, fiscal_year, segment)
    return format_fact_observation(rows, info, concept)


@mcp.tool(
    description=(
        "Deterministic arithmetic on figures from lookup_financial_fact. operation: difference (a-b), "
        "sum, ratio (a/b), pct_change ((a-b)/b*100, a=newer), margin_pct (a/b*100), cagr_pct (b->a over n). "
        "Never compute in your head; quote the result as returned."
    ),
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def compute_metric(
    operation: Annotated[str, Field(description="difference | sum | ratio | pct_change | margin_pct | cagr_pct")],
    a: Annotated[float, Field(description="First operand (the newer figure for pct_change).")],
    b: Annotated[float, Field(description="Second operand (the older figure for pct_change).")],
    ctx: Context,
    n: Annotated[int | None, Field(description="Number of periods, for cagr_pct only.")] = None,
) -> str:
    """Return the computed value with the formula used."""
    out = compute(operation, a, b, n)
    if "error" in out:
        return f"Error: {out['error']}"
    n_note = f", n={n}" if n else ""
    return f"[1] calc={out['operation']}\n    {out['formula']} with a={a:,.10g}, b={b:,.10g}{n_note} = {out['result']:,.2f}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run with:  python -m mcp_server.server
    # Or during development:  mcp dev mcp_server/server.py
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mcp.run(
        transport="streamable-http",
    )
