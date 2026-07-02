# agent/mcp_agent.py
#
# PURPOSE
# -------
# A drop-in replacement for agent/financial_agent.py that sources its tools
# from the MCP server in mcp_server/server.py instead of importing Python
# functions directly.  The reasoning loop (LangGraph's create_react_agent) and
# the system prompt are identical; only the tool transport has changed.
#
# WHY MOVE TOOLS BEHIND MCP?
# --------------------------
# The original agent imported search_filings, list_available_companies, and
# compare_companies from the same Python process.  That worked for a single-
# machine prototype but couples the agent to the data layer:
#
#   - The agent process must import ChromaDB, sentence-transformers, and every
#     filing-ingestion dependency even if it only needs to reason.
#   - Scaling means running N agent replicas × N copies of the vectorstore.
#   - Other teams / other languages / Claude Desktop cannot reuse the tools.
#
# Behind MCP, the tools are a network service: the agent process only needs a
# thin adapter, and the ChromaDB-loaded server can be scaled independently.
#
# WHY langchain-mcp-adapters?
# ---------------------------
# LangGraph's tool-calling loop consumes LangChain BaseTool objects.  The MCP
# Python SDK exposes tools over JSON-RPC but does not produce BaseTools.  The
# adapter bridges the two: it connects to an MCP server, discovers the tools,
# and wraps each one in a StructuredTool whose .ainvoke() call marshals the
# arguments into an MCP tool call and unmarshals the result.  From LangGraph's
# perspective the tools look and behave exactly like @tool-decorated functions.
#
# The user-facing class name in the requirements spec was
# "StreamableHTTPMultiServerMCPClient"; in this version of the library the
# class is simply MultiServerMCPClient and the streamable-HTTP transport is
# selected per-connection via {"transport": "streamable_http"}.  The adapter
# supports multiple servers keyed by name — we configure only one here, but
# adding a second (e.g. a market-data MCP server) is a one-line change.
#
# WIRE FORMAT NOTE
# ----------------
# The FastMCP CLI argument is "streamable-http" (hyphen), but the
# langchain-mcp-adapters connection dict expects "streamable_http" (underscore).
# This is a quirk of the two libraries, not a typo.
#
# ARCHITECTURE
# ------------
#   ┌────────────────────────┐      HTTP/1.1      ┌──────────────────────────┐
#   │  LangGraph agent       │ ─────JSON-RPC────► │  FastMCP server          │
#   │  (this module)         │ ◄────results────── │  (mcp_server/server.py)  │
#   │                        │                    │                          │
#   │  create_react_agent    │                    │  search_filings          │
#   │    + Gemini LLM        │                    │  list_available_companies│
#   │    + discovered tools  │                    │  compare_companies       │
#   └────────────────────────┘                    └──────────────────────────┘
#
# RUNNING
# -------
#   1. Start the MCP server in one terminal:
#        python -m mcp_server.server
#   2. Run the agent in another terminal:
#        python -m agent.mcp_agent

import asyncio
import logging
import os

from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")

# URL of the running MCP server.  Overridable via env so the same agent binary
# can point at a local dev server or a deployed one without code changes.
# IMPORTANT: the path must end in /mcp (the FastMCP streamable-HTTP mount
# point); the adapter will POST JSON-RPC requests there.
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/mcp")

# Logical name for the server in the MultiServerMCPClient connections dict.
# Shows up in tool-name prefixes if tool_name_prefix=True is enabled and in
# error messages, so keep it short and human-readable.
MCP_SERVER_KEY = "sec_filings"


# ── System prompt ─────────────────────────────────────────────────────────────
#
# Copied verbatim from agent/financial_agent.py so that A/B comparisons between
# the direct-import agent and the MCP-backed agent measure only the transport
# difference, not a prompt change.  If the prompt is ever tuned, update both
# files together (or factor it into a shared module).

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
    "resolve the ambiguity by searching."
)


# ── Custom exception ──────────────────────────────────────────────────────────

class MCPConnectionError(RuntimeError):
    """Raised when the agent cannot reach the MCP server.

    Wraps the underlying transport exception so callers can distinguish
    "server unreachable" from "agent reasoning failed" without inspecting
    nested ExceptionGroups from the MCP client internals.
    """


# ── MCP tool discovery ────────────────────────────────────────────────────────

def _build_mcp_client() -> MultiServerMCPClient:
    """Create the multi-server MCP client configured for our one server.

    The adapter supports connecting to multiple MCP servers at once (e.g. a
    filings server + a market-data server) by adding more entries to the
    connections dict.  We only have one today; the dict keeps future growth
    trivial.
    """
    return MultiServerMCPClient(
        {
            MCP_SERVER_KEY: {
                # Note the underscore: the adapter uses "streamable_http" while
                # the FastMCP CLI uses "streamable-http".
                "transport": "streamable_http",
                "url": MCP_SERVER_URL,
            }
        }
    )


async def _discover_tools() -> list:
    """Connect to the MCP server and return the tools it advertises.

    The adapter's get_tools() opens a short-lived session, issues the MCP
    "tools/list" RPC, wraps each returned definition in a LangChain
    StructuredTool, and closes the session.  The returned tools are stateless
    wrappers — each subsequent .ainvoke() opens its own session on demand,
    which is why the agent process does not need to keep a long-lived
    connection open.

    Raises
    ------
    MCPConnectionError
        If the server is unreachable, refuses the connection, or the HTTP
        handshake fails.  The original exception is chained via ``from`` so
        tracebacks still show the root cause.
    """
    client = _build_mcp_client()
    try:
        tools = await client.get_tools()
    except Exception as exc:
        # The MCP stack raises a variety of exceptions depending on failure
        # mode (ConnectionRefusedError wrapped in ExceptionGroup, httpx
        # ConnectError, asyncio timeouts, etc.).  Surface a single, actionable
        # error to callers rather than leaking the internal hierarchy.
        raise MCPConnectionError(
            f"Failed to connect to MCP server at {MCP_SERVER_URL}. "
            f"Start it with `python -m mcp_server.server` and try again. "
            f"Underlying error: {exc!r}"
        ) from exc

    if not tools:
        raise MCPConnectionError(
            f"MCP server at {MCP_SERVER_URL} returned no tools. "
            f"Check that mcp_server/server.py registered its @mcp.tool() "
            f"functions before mcp.run()."
        )

    logger.info(
        "Discovered %d tools from MCP server: %s",
        len(tools),
        [t.name for t in tools],
    )
    return tools


# ── Agent factory ─────────────────────────────────────────────────────────────

async def build_agent_executor():
    """Build a LangGraph agent whose tools come from the MCP server.

    Differences from agent.financial_agent.build_agent_executor:
      1. The tools are discovered at runtime from the MCP server rather than
         imported at module load time.  The function is therefore async.
      2. A connection to the server is required at build time; if the server
         is down, MCPConnectionError is raised before the LLM is constructed.

    Returns
    -------
    A compiled LangGraph StateGraph ready to accept
    ``await .ainvoke({"messages": [("human", question)]})`` calls.

    Raises
    ------
    EnvironmentError
        If GEMINI_API_KEY is not set.
    MCPConnectionError
        If the MCP server at MCP_SERVER_URL is unreachable.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file:  GEMINI_API_KEY=your-key-here\n"
            "Get a free key at https://aistudio.google.com/app/apikey"
        )

    tools = await _discover_tools()

    llm = ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=api_key,
        # temperature=0 keeps tool-calling decisions deterministic; see the
        # extended rationale in agent/financial_agent.py.
        temperature=0,
    )

    return create_react_agent(model=llm, tools=tools, prompt=_SYSTEM_PROMPT)


# ── Public entry points ───────────────────────────────────────────────────────

async def arun_agent(question: str) -> str:
    """Async version of run_agent. Preferred inside existing event loops."""
    agent = await build_agent_executor()
    logger.info("Running MCP-backed agent on question: %r", question)
    # ainvoke rather than invoke because the MCP-wrapped tools are async under
    # the hood — calling the sync invoke would force a nested event loop.
    result = await agent.ainvoke(
        {"messages": [("human", question)]},
        # Each tool-call round trip costs 2 graph steps; limit=20 ≈ 10 calls.
        config={"recursion_limit": 20},
    )
    return result["messages"][-1].content


def run_agent(question: str) -> str:
    """Run the MCP-backed agent on a single question and return its answer.

    Synchronous convenience wrapper.  Uses asyncio.run(), so do NOT call this
    from inside an existing event loop (use arun_agent there instead).

    Parameters
    ----------
    question:
        A natural-language question about the indexed SEC 10-K filings.

    Returns
    -------
    The agent's final answer as a plain string.

    Raises
    ------
    MCPConnectionError
        If the MCP server is unreachable at startup.
    EnvironmentError
        If GEMINI_API_KEY is not set.
    """
    return asyncio.run(arun_agent(question))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Smoke test: same question as agent/financial_agent.py so the two agents
    # can be compared side by side.  Requires the MCP server to be running at
    # MCP_SERVER_URL:
    #   Terminal 1:  python -m mcp_server.server
    #   Terminal 2:  python -m agent.mcp_agent
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_question = "Compare Apple and Microsoft revenue for their most recent fiscal year"

    print(f"\nQuestion: {test_question}")
    print("─" * 70)

    try:
        answer = run_agent(test_question)
    except MCPConnectionError as e:
        print(f"\n[MCP connection error] {e}")
        raise SystemExit(1)
    except EnvironmentError as e:
        print(f"\n[Configuration error] {e}")
        raise SystemExit(2)

    print("\n" + "─" * 70)
    print(f"Final Answer:\n{answer}")
