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
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
AGENT_TIMEOUT_SECONDS = 30.0


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
    allow_origins=["*"],
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
            chunk_idx = header.group(2)
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
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        for chunk in _parse_tool_content(content):
            key = (chunk.ticker, chunk.source_file)
            if key in seen:
                continue
            seen.add(key)
            unique.append(chunk)

    if not unique and tool_messages:
        combined = "\n".join(
            (m.content if isinstance(m.content, str) else str(m.content))
            for m in tool_messages
        )
        for raw_ticker, raw_idx in _FALLBACK_RE.findall(combined):
            ticker = raw_ticker.upper()
            chunk_idx = raw_idx.rstrip(",")
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


def _final_answer(result: dict) -> str:
    messages = result.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    return last.content if isinstance(last.content, str) else str(last.content)


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
        logger.exception("Direct agent failed")
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {fallback_exc}",
        ) from fallback_exc


@app.get("/health")
async def health():
    mcp_reachable = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(MCP_SERVER_URL)
            mcp_reachable = resp.status_code < 500
    except Exception as exc:
        logger.debug("MCP server at %s unreachable: %s", MCP_SERVER_URL, exc)
    return {"status": "healthy", "mcp_server": mcp_reachable}
