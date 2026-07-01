# tests/test_query_stream.py
#
# Smoke test for POST /query/stream.
#
# Runs WITHOUT a real LLM, a built ChromaDB index, or GEMINI_API_KEY by:
#   1. Injecting a fake agent into app.state, and
#   2. NOT using TestClient as a context manager — so the app lifespan (which
#      would build the real MCP + direct agents, needing the key/index) never
#      runs.
# Asserts the SSE stream emits token events before the terminal done event, and
# that a sources event carrying the citation is included.

import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from api import main as api_main


class _FakeAgent:
    """Minimal stand-in for the compiled LangGraph agent.

    Returns a ToolMessage (so _extract_sources finds a citation) and a final
    AIMessage (so _final_answer returns text), matching the shape the endpoint
    consumes from a real agent run.
    """

    async def ainvoke(self, payload, config=None):
        return {
            "messages": [
                ToolMessage(
                    content="[1] ticker=AAPL  chunk_idx=42\n    Apple total net sales were higher.",
                    tool_call_id="call_1",
                ),
                AIMessage(
                    content="Apple reported strong revenue growth (AAPL chunk 42)."
                ),
            ]
        }


def _parse_sse(text: str):
    """Extract the JSON payload of each SSE 'data:' line."""
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def test_query_stream_emits_tokens_then_done():
    # No MCP agent; the direct agent is our fake. Set before the request so the
    # endpoint reads them off app.state at request time.
    api_main.app.state.mcp_agent = None
    api_main.app.state.direct_agent = _FakeAgent()

    client = TestClient(api_main.app)
    resp = client.post(
        "/query/stream",
        json={"question": "What was Apple's revenue?", "ticker": "AAPL"},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    # token events are emitted, and the stream ends with a done event.
    assert "token" in types
    assert types[-1] == "done"
    assert types.index("token") < types.index("done")

    # a sources event is emitted (before done) carrying the AAPL citation.
    assert "sources" in types
    assert types.index("sources") < types.index("done")
    sources_event = next(e for e in events if e["type"] == "sources")
    assert any(item["ticker"] == "AAPL" for item in sources_event["items"])
    assert any(item["chunk_idx"] == "42" for item in sources_event["items"])

    # the reconstructed answer matches the fake final message.
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "Apple reported strong revenue growth" in answer
