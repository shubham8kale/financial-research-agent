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
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field

from agent import financial_agent, mcp_agent
from agent.contract import averify_answer
from agent.meter import QueryMeter, public_meta
from agent.observations import FALLBACK_RE, observation_text, parse_observation
from agent.financial_agent import (
    OUTCOME_EMPTY_ANSWER,
    OUTCOME_RECURSION_LIMIT,
    classify_terminal_state,
    content_text,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
# Max seconds for an agent run before we give up. Configurable via env because the
# right value depends on the host: a fast local box would manage in 30s, but a free-tier
# CPU deployment (HF Spaces) needs more headroom for multi-step reasoning +
# per-call query embedding + Gemini latency. Default 120s for deployed use.
AGENT_TIMEOUT_SECONDS = float(os.getenv("AGENT_TIMEOUT_SECONDS", "120"))
# One budget for the whole request: the MCP attempt and the direct fallback
# share it, so the worst case is AGENT_TIMEOUT_SECONDS, not twice that.
MAX_QUESTION_CHARS = 2000
SSE_KEEPALIVE_SECONDS = 10.0

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
    # Bounded: every character of the question is a model-call token paid for
    # twice (the agent turn and the structuring call); an unbounded body on a
    # public endpoint is an invitation.  2,000 characters is ten times the
    # longest benchmark question.
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    ticker: Optional[str] = Field(default=None, max_length=10)


class SourceChunk(BaseModel):
    text: str
    ticker: str
    source_file: str
    # True when a claim in the verified record cites this observation
    # (agent/contract.py); the UI renders those as verified sources.
    cited: bool = False


class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceChunk]
    tokens_used: Optional[int] = None
    # Per-request meter: latency_ms, llm_calls, input/output tokens, cost_usd at
    # the repo's price table, tools called, tool_ms_total, trace_id (the
    # LangSmith trace when tracing is on) and which backend answered.  Additive
    # to the contract; older clients ignore it.
    meta: Optional[Dict[str, Any]] = None
    # The output contract's verdict (agent/contract.py): status verified |
    # unverified | refused | skipped, the claims' figure counts, the failures
    # by name, and which observation ids the record cites.  In strict mode a
    # refused answer's `answer` is the refusal text, never the draft.
    verification: Optional[Dict[str, Any]] = None


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

# The regexes and the line walker live in agent/observations.py, shared with the
# eval harness so a citation here and a scored context there name the same
# chunk.  _parse_tool_content is kept as the API-shaped wrapper.
def _parse_tool_content(content: str) -> List[SourceChunk]:
    return [
        SourceChunk(text=c.text, ticker=c.ticker, source_file=c.chunk_id)
        for c in parse_observation(content)
        if c.kind != "calc"   # a computed number is not a source
    ]


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
        for raw_ticker, raw_idx in FALLBACK_RE.findall(combined):
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


# Messages shown to callers for each terminal failure.  Deliberately generic:
# they describe what happened to the request, never which model or provider was
# involved (see SECURITY.md — provider internals must not reach public routes).
_TERMINAL_DETAIL = {
    OUTCOME_EMPTY_ANSWER: (
        "The agent did not produce an answer for this question. This is a known "
        "failure mode on some models; please retry or rephrase."
    ),
    OUTCOME_RECURSION_LIMIT: (
        "The agent exhausted its step budget before answering. Try a narrower "
        "question, or one covering fewer companies."
    ),
}


def _terminal_http_error(outcome: str) -> HTTPException:
    """Map a named terminal failure to a 502.

    502 rather than 500: the agent ran without raising, but its upstream model
    returned something unusable.  Serving it as a 200 with an empty ``answer``
    is what this guard exists to prevent — a blank response rendered as a
    successful one is indistinguishable, to a caller, from "the filings say
    nothing", which is a materially different claim.
    """
    return HTTPException(status_code=502, detail=_TERMINAL_DETAIL.get(
        outcome, "The agent did not produce a usable answer."))


def _build_question(req: QueryRequest) -> str:
    if req.ticker:
        return f"{req.question} (company: {req.ticker.upper()})"
    return req.question


def _final_answer(result: dict) -> str:
    """Return the last message's content as plain text.

    Flattening lives in agent.financial_agent so that this route, the two CLI
    entry points, the MCP agent and the eval harness all render the identical
    string; it used to be reimplemented here.
    """
    messages = result.get("messages") or []
    if not messages:
        return ""
    return content_text(messages[-1].content)


# ── Streaming (SSE) helpers ─────────────────────────────────────────────────────
#
# STREAMING METHOD (shipped): chunked final answer.
# We run the agent to completion with ainvoke() — reusing the exact MCP→direct
# fallback and AGENT_TIMEOUT_SECONDS timeout as /query — then stream the FINAL answer
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
    """Recover the chunk index from a 'TICKER_10K_chunk_IDX' source_file string.

    A tagged XBRL fact ('TICKER_10K_fact_ID') is reported as 'fact ID' so the
    UI can label it as a fact rather than a passage.
    """
    if "_chunk_" in source_file:
        return source_file.rsplit("_chunk_", 1)[-1]
    if "_fact_" in source_file:
        return "fact " + source_file.rsplit("_fact_", 1)[-1]
    return ""


def _new_config(meter: QueryMeter) -> dict:
    return {"recursion_limit": 20, "callbacks": [meter]}


def _remaining(deadline: float) -> float:
    """Seconds left until *deadline* on the running loop's clock; never below a tenth of a second."""
    return max(0.1, deadline - asyncio.get_running_loop().time())


async def _complete(question: str, result: dict, meter: QueryMeter, backend: str):
    """Everything that happens between the agent's draft and the response.

    The draft is turned into a record of claims and verified against the
    observations of this run (agent/contract.py): one structuring call, one
    repair, then refused or flagged per VERIFY_MODE.  The structuring calls
    are metered with the agent's own, so `meta` is the cost of the answer
    served.  A terminal-failure draft is passed through untouched for the
    caller's guard to classify.  Returns (answer, sources, meta, verification).
    """
    messages = result.get("messages") or []
    draft = _final_answer(result)
    sources = _extract_sources(messages)
    extra: list = []
    if classify_terminal_state(draft):
        answer, verification = draft, {"status": "skipped", "reason": "terminal failure"}
    else:
        observations = [observation_text(m.content) for m in messages if isinstance(m, ToolMessage)]
        answer, verdict, extra = await averify_answer(question, draft, observations, callbacks=[meter])
        verification = verdict.public_dict()
        if verdict.status == "verified":   # an unverified record's citations are claims, not evidence
            for src in sources:
                src.cited = src.source_file in verdict.cited
    meter.mark_end()
    meta = public_meta(meter.summary(list(messages) + extra))
    meta["backend"] = backend
    return answer, sources, meta, verification


async def _run_with_fallback(request: Request, question: str):
    """Run the agent (MCP first, then direct) and return (answer, sources, meta, verification).

    Mirrors /query's fallback order and AGENT_TIMEOUT_SECONDS timeout (120 s by
    default) so the streaming endpoint has
    identical semantics; only the response transport differs. Propagates
    asyncio.TimeoutError if the direct agent also times out, or the underlying
    exception if it fails, so the caller can emit an SSE error event.
    """
    payload = {"messages": [("human", question)]}
    deadline = asyncio.get_running_loop().time() + AGENT_TIMEOUT_SECONDS

    mcp_exec = getattr(request.app.state, "mcp_agent", None)
    if mcp_exec is not None:
        meter = QueryMeter(model=financial_agent.LLM_MODEL)
        try:
            result = await asyncio.wait_for(
                mcp_exec.ainvoke(payload, config=_new_config(meter)),
                timeout=_remaining(deadline),
            )
            answer = _final_answer(result)
            if not classify_terminal_state(answer):
                logger.info("Stream answered via MCP agent")
                return await _complete(question, result, meter, "mcp")
            logger.warning("MCP agent produced no usable answer; falling back")
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
    meter = QueryMeter(model=financial_agent.LLM_MODEL)
    result = await asyncio.wait_for(
        direct_exec.ainvoke(payload, config=_new_config(meter)),
        timeout=_remaining(deadline),
    )
    logger.info("Stream answered via direct agent")
    return await _complete(question, result, meter, "direct")


async def _sse_event_stream(request: Request, question: str):
    """Async generator yielding SSE lines: token* → sources → verification → meta → done (or error)."""
    # Run the agent as a task and emit SSE keepalive comments while it works. The
    # chunked-answer design produces no output until the agent finishes, so on a
    # slow free-tier host that silent gap can trip a proxy idle-timeout and drop
    # the connection. Comment lines (": ...") are ignored by SSE clients.
    run = asyncio.create_task(_run_with_fallback(request, question))
    try:
        while True:
            finished, _ = await asyncio.wait({run}, timeout=SSE_KEEPALIVE_SECONDS)
            if finished:
                break
            if await request.is_disconnected():
                # The browser left: stop paying for an answer nobody will read.
                logger.info("Stream client disconnected; cancelling the agent run")
                run.cancel()
                return
            yield ": keepalive\n\n"
    finally:
        # Closing the generator early (client gone, server shutting down) must
        # not leave the agent task running to completion on its own.
        if not run.done():
            run.cancel()

    try:
        answer, sources, meta, verification = run.result()
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

    # The agent can finish without producing a usable answer, in two distinct
    # ways, and BOTH must be errors rather than content:
    #
    #   empty_answer     streams as a blank bubble with sources attached, which
    #                    reads to a user as "the filings say nothing" — a
    #                    materially different and false claim.
    #   recursion_limit  is NOT empty; LangGraph substitutes its own placeholder
    #                    string, which previously streamed through as if it were
    #                    the model's considered answer.
    outcome = classify_terminal_state(answer)
    if outcome:
        logger.warning("Stream terminal failure: %s", outcome)
        yield _sse({
            "type": "error",
            "outcome": outcome,
            "message": _TERMINAL_DETAIL.get(
                outcome, "The agent did not produce a usable answer."),
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
                "cited": s.cited,
            }
            for s in sources
        ],
    })
    # The contract's verdict, after the sources it refers to and before the
    # meter: status, figure counts, failures by name, cited observation ids.
    yield _sse({"type": "verification", **verification})
    # What the answer cost, after the sources and before the terminal marker:
    # latency, model calls, tokens, dollars, tools and the trace id.  Additive
    # to the token/sources/done/error contract; a client that does not know
    # "meta" ignores it.
    yield _sse({"type": "meta", **meta})
    yield _sse({"type": "done"})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, request: Request) -> QueryResponse:
    question = _build_question(req)
    payload = {"messages": [("human", question)]}
    deadline = asyncio.get_running_loop().time() + AGENT_TIMEOUT_SECONDS

    mcp_exec = getattr(request.app.state, "mcp_agent", None)
    if mcp_exec is not None:
        try:
            meter = QueryMeter(model=financial_agent.LLM_MODEL)
            result = await asyncio.wait_for(
                mcp_exec.ainvoke(payload, config=_new_config(meter)),
                timeout=_remaining(deadline),
            )
            answer = _final_answer(result)
            mcp_outcome = classify_terminal_state(answer)
            if mcp_outcome:
                # Fall through to the direct agent: a terminal failure on the MCP
                # path is exactly the case the fallback exists for.
                raise RuntimeError(f"MCP agent terminal failure: {mcp_outcome}")
            logger.info("Query answered via MCP agent")
            answer, sources, meta, verification = await _complete(question, result, meter, "mcp")
            return QueryResponse(
                answer=answer,
                sources=sources,
                tokens_used=(meta["input_tokens"] or 0) + (meta["output_tokens"] or 0) or None,
                meta=meta,
                verification=verification,
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
        meter = QueryMeter(model=financial_agent.LLM_MODEL)
        result = await asyncio.wait_for(
            direct_exec.ainvoke(payload, config=_new_config(meter)),
            timeout=_remaining(deadline),
        )
        answer = _final_answer(result)
        outcome = classify_terminal_state(answer)
        if outcome:
            logger.warning("Direct agent terminal failure: %s", outcome)
            raise _terminal_http_error(outcome)
        logger.info("Query answered via direct agent")
        answer, sources, meta, verification = await _complete(question, result, meter, "direct")
        return QueryResponse(
            answer=answer,
            sources=sources,
            tokens_used=(meta["input_tokens"] or 0) + (meta["output_tokens"] or 0) or None,
            meta=meta,
            verification=verification,
        )
    except HTTPException:
        # Already a deliberate, classified failure — do not re-wrap it as a 500.
        raise
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
      {"type":"verification",...}          once: the output contract's verdict
      {"type":"meta",...}                  once: latency, tokens, cost, trace id
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
