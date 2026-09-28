# tests/test_api_edges.py
#
# Production edges on the API (review issue 5): the question body is bounded,
# the MCP attempt and the direct fallback share ONE deadline, and the stream
# cancels the agent when the client disconnects so a closed browser stops the
# spend.  Fake agents, verification off, no model or key.

import asyncio
import time

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from api import main as api_main

CHUNK = "[1] ticker=AAPL  chunk_idx=42\n    Total net sales were $416,161 million."


class _Agent:
    def __init__(self, delay: float = 0.0, answer: str = "Total net sales were $416,161 million."):
        self.delay, self.answer = delay, answer
        self.calls, self.cancelled = 0, False

    async def ainvoke(self, payload, config=None):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return {"messages": [ToolMessage(content=CHUNK, tool_call_id="c1"), AIMessage(content=self.answer)]}


def _client(monkeypatch, direct, mcp=None) -> TestClient:
    monkeypatch.setenv("VERIFY_MODE", "off")
    api_main.app.state.mcp_agent = mcp
    api_main.app.state.direct_agent = direct
    return TestClient(api_main.app)


def test_question_length_is_bounded(monkeypatch):
    client = _client(monkeypatch, _Agent())
    too_long = "x" * (api_main.MAX_QUESTION_CHARS + 1)
    assert client.post("/query", json={"question": too_long}).status_code == 422
    assert client.post("/query/stream", json={"question": too_long}).status_code == 422
    assert client.post("/query", json={"question": ""}).status_code == 422
    assert client.post("/query", json={"question": "ok?", "ticker": "A" * 11}).status_code == 422
    assert client.post("/query", json={"question": "ok?"}).status_code == 200


def test_mcp_attempt_and_direct_fallback_share_one_deadline(monkeypatch):
    monkeypatch.setattr(api_main, "AGENT_TIMEOUT_SECONDS", 0.5)
    mcp, direct = _Agent(delay=5.0), _Agent(delay=5.0)
    client = _client(monkeypatch, direct, mcp)
    t0 = time.perf_counter()
    resp = client.post("/query", json={"question": "q"})
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 504
    assert mcp.calls == 1 and direct.calls == 1
    assert elapsed < 0.95, f"two full timeouts would take 1.0 s; took {elapsed:.2f}"   # one budget, not two


def test_stream_reports_a_timeout_within_one_deadline(monkeypatch):
    monkeypatch.setattr(api_main, "AGENT_TIMEOUT_SECONDS", 0.4)
    client = _client(monkeypatch, _Agent(delay=5.0), _Agent(delay=5.0))
    t0 = time.perf_counter()
    resp = client.post("/query/stream", json={"question": "q"})
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200 and '"type": "error"' in resp.text and "timed out" in resp.text
    assert elapsed < 0.85


def test_stream_cancels_the_agent_when_the_client_leaves(monkeypatch):
    monkeypatch.setenv("VERIFY_MODE", "off")
    monkeypatch.setattr(api_main, "SSE_KEEPALIVE_SECONDS", 0.05)
    agent = _Agent(delay=5.0)

    class _State:
        mcp_agent = None
        direct_agent = agent

    class _App:
        state = _State()

    class _Request:
        app = _App()
        polls = 0

        async def is_disconnected(self):
            self.polls += 1
            return self.polls >= 2          # still there on the first keepalive, gone on the second

    async def main():
        lines = [line async for line in api_main._sse_event_stream(_Request(), "q")]
        await asyncio.sleep(0.05)           # let the cancellation reach the agent's sleep
        return lines

    t0 = time.perf_counter()
    lines = asyncio.run(main())
    assert agent.cancelled, "the agent task kept running after the client left"
    assert lines and all(line.startswith(":") for line in lines)      # keepalives only, no answer
    assert time.perf_counter() - t0 < 1.0
