# api/main.py
#
# FastAPI REST endpoint for the financial research agent.
#
# FALLBACK PATTERN
# ----------------
# /query tries the MCP-backed agent first (agent.mcp_agent), which sources its
# tools from a separately-running MCP server over streamable-HTTP. If that
# agent fails for any reason — the MCP server is down, the connection times
# out, the agent raises, or the response is empty — the request falls through
# to the in-process direct agent (agent.financial_agent) which talks to
# ChromaDB directly. This keeps the API available during MCP server outages
# while preserving the MCP architecture as the preferred path.

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import ToolMessage
from pydantic import BaseModel

from agent import financial_agent, mcp_agent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
# Max seconds for an agent run before we give up. Configurable via env because the
# right value depends on the host: a fast local box handles 30s, but a free-tier
# CPU deployment (HF Spaces) needs more headroom for multi-step reasoning +
# per-call query embedding + Gemini latency. Default 120s for deployed use.
AGENT_TIMEOUT_SECONDS = float(os.getenv("AGENT_TIMEOUT_SECONDS", "120"))

# Browser origins allowed to call the API (CORS). The Next.js dev server runs on
# http://localhost:3000; the deployed Vercel domain is supplied at deploy time
# via FRONTEND_ORIGINS (comma-separated). Server-to-server callers are unaffected
# by CORS, so restricting this to the known frontends is safe.
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,https://your-app.vercel.app",
    ).split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.mcp_agent = await mcp_agent.build_agent_executor()
        logger.info("MCP agent built successfully at startup")
    except Exception as exc:
        logger.warning(
            "Failed to build MCP agent at startup (%s: %s). "
            "Requests will use the direct agent only.",
            type(exc).__name__,
            exc,
        )
        app.state.mcp_agent = None

    app.state.direct_agent = financial_agent.build_agent_executor()
    logger.info("Direct agent built successfully at startup")

    yield


app = FastAPI(title="Financial Research Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ───────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    ticker: Optional[str] = None


class SourceChunk(BaseModel):
    text: str
    ticker: str
    source_file: str


class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceChunk]
    tokens_used: Optional[int] = None


# ── Source extraction ─────────────────────────────────────────────────────────
#
# Primary path: _parse_tool_content walks tool output line-by-line so it can
# honour compare_companies' "=== TICKER ===" section headers (where ticker is
# not inline with chunk_idx).
#
# Fallback path: if the primary parser extracts nothing, combine all
# ToolMessage contents and do a single re.findall across the combined blob.
# This catches format drift (tool output tweaks, MCP-layer reformatting) at
# the cost of losing the snippet text.

_SECTION_RE = re.compile(r"^===\s*(\S+)\s*===")
_CHUNK_HEADER_RE = re.compile(
    r"^\s*\[\d+\]\s*(?:ticker=(\S+)\s+)?chunk_idx=(\S+)"
)
_FALLBACK_RE = re.compile(r"ticker=(\S+)\s+chunk_idx=(\S+)")


def _parse_tool_content(content: str) -> List[SourceChunk]:
    sources: List[SourceChunk] = []
    current_ticker: Optional[str] = None
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        section = _SECTION_RE.match(line)
        if section:
            current_ticker = section.group(1).upper()
            i += 1
            continue

        header = _CHUNK_HEADER_RE.match(line)
        if header:
            ticker = (header.group(1) or current_ticker or "UNKNOWN").upper()
            chunk_idx = header.group(2).strip()
            i += 1
            snippet_lines: List[str] = []
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    break
                if _SECTION_RE.match(nxt) or _CHUNK_HEADER_RE.match(nxt):
                    break
                snippet_lines.append(nxt.strip())
                i += 1
            text = " ".join(snippet_lines).strip()
            if text:
                sources.append(
                    SourceChunk(
                        text=text,
                        ticker=ticker,
                        source_file=f"{ticker}_10K_chunk_{chunk_idx}",
                    )
                )
            continue
        i += 1
    return sources


def _extract_sources(messages) -> List[SourceChunk]:
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    logger.debug(
        "Extracting sources: %d total messages, %d ToolMessages",
        len(messages),
        len(tool_messages),
    )

    seen = set()
    unique: List[SourceChunk] = []
    for msg in tool_messages:
        # MCP ToolMessages wrap content as a list of dicts: [{"type": "text", "text": "..."}]
        # Direct agent ToolMessages return plain strings.
        if isinstance(msg.content, list):
            content = "\n".join(
                block["text"] for block in msg.content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        elif isinstance(msg.content, str):
            content = msg.content
        else:
            content = str(msg.content)
        for chunk in _parse_tool_content(content):
            key = (chunk.ticker, chunk.source_file)
            if key in seen:
                continue
            seen.add(key)
            unique.append(chunk)

    if not unique and tool_messages:
        def _get_text(m):
            if isinstance(m.content, list):
                return "\n".join(
                    block["text"] for block in m.content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            return m.content if isinstance(m.content, str) else str(m.content)
        combined = "\n".join(_get_text(m) for m in tool_messages)
        for raw_ticker, raw_idx in _FALLBACK_RE.findall(combined):
            ticker = raw_ticker.upper()
            chunk_idx = raw_idx.rstrip(",").strip()
            key = (ticker, chunk_idx)
            if key in seen:
                continue
            seen.add(key)
            unique.append(
                SourceChunk(
                    text="",
                    ticker=ticker,
                    source_file=f"{ticker}_10K_chunk_{chunk_idx}",
                )
            )
    return unique


def _build_question(req: QueryRequest) -> str:
    if req.ticker:
        return f"{req.question} (company: {req.ticker.upper()})"
    return req.question


def _content_text(content) -> str:
    """Flatten an AIMessage content payload to plain text.

    Older Gemini models return message content as a plain string, but newer ones
    (e.g. gemini-2.5-flash) return a LIST of content blocks such as
    [{"type": "text", "text": "...", "extras": {...}}]. Without this, the API
    would leak the raw repr of that list into answers.
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
    return str(content)


def _final_answer(result: dict) -> str:
    messages = result.get("messages") or []
    if not messages:
        return ""
    return _content_text(messages[-1].content)


# ── Streaming (SSE) helpers ─────────────────────────────────────────────────────
#
# STREAMING METHOD (shipped): chunked final answer.
# We run the agent to completion with ainvoke() — reusing the exact MCP→direct
# fallback and 30s timeout as /query — then stream the FINAL answer to the client
# word-by-word as SSE "token" events, followed by one "sources" event and a "done"
# event. We deliberately did NOT use LangGraph astream_events for per-token LLM
# streaming: create_react_agent emits model-stream events for *every* LLM turn
# (including the intermediate tool-deciding turns), and reliably isolating only the
# final-answer tokens across the Gemini + MCP-fallback paths proved brittle. The
# chunked approach guarantees the client sees only final-answer text, keeps source
# extraction identical to /query, and still streams incrementally (live-typing feel).

_WORD_RE = re.compile(r"\S+\s*")


def _sse(payload: dict) -> str:
    """Serialise one event as a single SSE 'data:' line (one JSON object)."""
    return f"data: {json.dumps(payload)}\n\n"


def _chunk_idx_of(source_file: str) -> str:
    """Recover the chunk index from a 'TICKER_10K_chunk_IDX' source_file string."""
    return source_file.rsplit("_chunk_", 1)[-1] if "_chunk_" in source_file else ""


async def _run_with_fallback(request: Request, question: str):
    """Run the agent (MCP first, then direct) and return (answer, sources).

    Mirrors /query's fallback order and 30s timeout so the streaming endpoint has
    identical semantics; only the response transport differs. Propagates
    asyncio.TimeoutError if the direct agent also times out, or the underlying
    exception if it fails, so the caller can emit an SSE error event.
    """
    payload = {"messages": [("human", question)]}
    config = {"recursion_limit": 20}

    mcp_exec = getattr(request.app.state, "mcp_agent", None)
    if mcp_exec is not None:
        try:
            result = await asyncio.wait_for(
                mcp_exec.ainvoke(payload, config=config),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
            answer = _final_answer(result)
            if answer.strip():
                logger.info("Stream answered via MCP agent")
                return answer, _extract_sources(result["messages"])
            logger.warning("MCP agent returned empty response; falling back")
        except asyncio.TimeoutError:
            logger.warning("MCP agent timed out; falling back to direct agent")
        except Exception as mcp_exc:
            logger.warning(
                "MCP agent failed (%s: %s); falling back to direct agent",
                type(mcp_exc).__name__,
                mcp_exc,
            )
    else:
        logger.info("No MCP agent available; using direct agent")

    direct_exec = request.app.state.direct_agent
    result = await asyncio.wait_for(
        direct_exec.ainvoke(payload, config=config),
        timeout=AGENT_TIMEOUT_SECONDS,
    )
    logger.info("Stream answered via direct agent")
    return _final_answer(result), _extract_sources(result["messages"])


async def _sse_event_stream(request: Request, question: str):
    """Async generator yielding SSE lines: token* → sources → done (or error)."""
    # Run the agent as a task and emit SSE keepalive comments while it works. The
    # chunked-answer design produces no output until the agent finishes, so on a
    # slow free-tier host that silent gap can trip a proxy idle-timeout and drop
    # the connection. Comment lines (": ...") are ignored by SSE clients.
    run = asyncio.create_task(_run_with_fallback(request, question))
    while True:
        finished, _ = await asyncio.wait({run}, timeout=10)
        if finished:
            break
        yield ": keepalive\n\n"

    try:
        answer, sources = run.result()
    except asyncio.TimeoutError:
        yield _sse({
            "type": "error",
            "message": f"Agent execution timed out after {AGENT_TIMEOUT_SECONDS:.0f} seconds",
        })
        return
    except Exception:
        # Full traceback goes to the server log only. Never echo exception text to
        # the client: provider errors embed internal details (model names, quota
        # ids, endpoints) that shouldn't reach a public, unauthenticated endpoint.
        logger.exception("Streaming agent failed")
        yield _sse({
            "type": "error",
            "message": "The agent hit an internal error. Please try again shortly.",
        })
        return

    # The agent can finish without producing final-answer text (e.g. it exhausted
    # its tool-call budget). Surface that as an error rather than streaming an
    # empty message that renders as a blank bubble.
    if not answer.strip():
        yield _sse({
            "type": "error",
            "message": "The agent didn't produce an answer. Please try rephrasing your question.",
        })
        return

    for word in _WORD_RE.findall(answer):
        yield _sse({"type": "token", "text": word})
        # Cooperative yield so each token flushes to the client rather than the
        # whole answer buffering into a single write.
        await asyncio.sleep(0)

    yield _sse({
        "type": "sources",
        "items": [
            {
                "ticker": s.ticker,
                "chunk_idx": _chunk_idx_of(s.source_file),
                "source": s.source_file,
            }
            for s in sources
        ],
    })
    yield _sse({"type": "done"})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, request: Request) -> QueryResponse:
    question = _build_question(req)
    payload = {"messages": [("human", question)]}
    config = {"recursion_limit": 20}

    mcp_exec = getattr(request.app.state, "mcp_agent", None)
    if mcp_exec is not None:
        try:
            result = await asyncio.wait_for(
                mcp_exec.ainvoke(payload, config=config),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
            answer = _final_answer(result)
            if not answer.strip():
                raise RuntimeError("MCP agent returned an empty response")
            logger.info("Query answered via MCP agent")
            return QueryResponse(
                answer=answer,
                sources=_extract_sources(result["messages"]),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MCP agent timed out after %.0fs. Falling back to direct agent.",
                AGENT_TIMEOUT_SECONDS,
            )
        except Exception as mcp_exc:
            logger.warning(
                "MCP agent failed (%s: %s). Falling back to direct agent.",
                type(mcp_exc).__name__,
                mcp_exc,
            )
    else:
        logger.info("No MCP agent available; using direct agent")

    direct_exec = request.app.state.direct_agent
    try:
        result = await asyncio.wait_for(
            direct_exec.ainvoke(payload, config=config),
            timeout=AGENT_TIMEOUT_SECONDS,
        )
        answer = _final_answer(result)
        logger.info("Query answered via direct agent")
        return QueryResponse(
            answer=answer,
            sources=_extract_sources(result["messages"]),
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Agent execution timed out after {AGENT_TIMEOUT_SECONDS:.0f} seconds",
        )
    except Exception as fallback_exc:
        # Log the full traceback server-side; return a generic message so provider
        # internals (model names, quota ids) never reach the public endpoint.
        logger.exception("Direct agent failed")
        raise HTTPException(
            status_code=500,
            detail="Agent execution failed. Please try again shortly.",
        ) from fallback_exc


@app.post("/query/stream")
async def query_stream(req: QueryRequest, request: Request):
    """Stream the agent's final answer as Server-Sent Events (text/event-stream).

    Emits one JSON object per SSE data line:
      {"type":"token","text":"<delta>"}   repeated — the answer text
      {"type":"sources","items":[...]}     once, after the tokens
      {"type":"done"}                       terminal success marker
      {"type":"error","message":"..."}     terminal error marker
    See the streaming-helpers comment above for why the final answer is chunked
    rather than streamed via astream_events.
    """
    question = _build_question(req)
    return StreamingResponse(
        _sse_event_stream(request, question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable proxy buffering (e.g. nginx on Render) so tokens stream.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health():
    mcp_reachable = False
    try:
        # Parse host and port from MCP_SERVER_URL to do a simple TCP check
        # instead of hitting the MCP endpoint (which triggers session creation)
        from urllib.parse import urlparse
        parsed = urlparse(MCP_SERVER_URL)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=2.0
        )
        writer.close()
        await writer.wait_closed()
        mcp_reachable = True
    except Exception as exc:
        logger.debug("MCP server unreachable: %s", exc)
    return {"status": "healthy", "mcp_server": mcp_reachable}
