# tests/test_query_meta.py
#
# The per-request meter on the API (upgrade 4): /query returns a `meta`
# object and fills `tokens_used`; /query/stream emits one `meta` event after
# the sources and before `done`.  Token counts come from the usage_metadata
# the fake agent's AI message carries, so no model, key or index is needed.

import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from api import main as api_main


class _MeteredFakeAgent:
    """Returns one tool observation and a final answer that carries token usage."""

    def __init__(self):
        self.configs = []

    async def ainvoke(self, payload, config=None):
        self.configs.append(config)
        return {
            "messages": [
                AIMessage(content="", usage_metadata={"input_tokens": 1500, "output_tokens": 40, "total_tokens": 1540}),
                ToolMessage(content="[1] ticker=AAPL  chunk_idx=42\n    Total net sales were $416,161 million.",
                            tool_call_id="call_1"),
                AIMessage(content="Total net sales were $416,161 million.",
                          usage_metadata={"input_tokens": 2500, "output_tokens": 60, "total_tokens": 2560}),
            ]
        }


def _events(text: str):
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


def test_query_returns_meta_and_tokens_used(monkeypatch):
    monkeypatch.setattr(api_main.financial_agent, "LLM_MODEL", "gemini-3.1-flash-lite")
    agent = _MeteredFakeAgent()
    api_main.app.state.mcp_agent = None
    api_main.app.state.direct_agent = agent
    body = TestClient(api_main.app).post("/query", json={"question": "Apple net sales?"}).json()

    assert body["tokens_used"] == 4100
    meta = body["meta"]
    assert meta["backend"] == "direct" and meta["llm_calls"] == 2
    assert (meta["input_tokens"], meta["output_tokens"]) == (4000, 100)
    assert meta["cost_usd"] == round((4000 * 0.25 + 100 * 1.50) / 1e6, 6)
    assert meta["latency_ms"] >= 0 and "tools" in meta and "trace_id" in meta
    # the meter reached the agent as a callback
    assert agent.configs and agent.configs[0]["callbacks"]


def test_stream_emits_meta_after_sources_before_done():
    api_main.app.state.mcp_agent = None
    api_main.app.state.direct_agent = _MeteredFakeAgent()
    resp = TestClient(api_main.app).post("/query/stream", json={"question": "Apple net sales?"})
    types = [e["type"] for e in _events(resp.text)]
    assert types.index("sources") < types.index("meta") < types.index("done")
    meta = next(e for e in _events(resp.text) if e["type"] == "meta")
    assert meta["input_tokens"] == 4000 and meta["backend"] == "direct" and meta["cost_usd"] is not None
