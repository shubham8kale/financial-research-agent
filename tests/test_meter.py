# tests/test_meter.py
#
# The per-run meter (agent/meter.py) and the price table (eval/pricing.py):
# what one agent run cost in time, calls, tokens and dollars, and which
# tools it called.  Callback events are driven by hand; no model runs.

import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.meter import QueryMeter, public_meta, tokens_from_messages
from agent.pricing import PRICES_USD_PER_M, cost_usd, price


def test_price_lookup_tolerates_prefixes_and_refuses_unknown():
    assert price("gemini-3.1-flash-lite") == (0.25, 1.50)
    assert price("models/gemini-3.6-flash") == (0.75, 3.75)
    assert price("gemini-3.1-flash-lite-001") == (0.25, 1.50)
    assert price("gpt-oss-120b") is None and price(None) is None
    assert all(len(v) == 2 for v in PRICES_USD_PER_M.values())


def test_cost_is_tokens_times_price_and_none_when_unknown():
    assert cost_usd("gemini-3.1-flash-lite", 1_000_000, 0) == 0.25
    assert cost_usd("gemini-3.1-flash-lite", 4_000, 1_000) == pytest.approx(0.0025)
    assert cost_usd("mystery-model", 1000, 1000) is None


def test_tokens_from_messages_sums_ai_usage_only():
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}),
        AIMessage(content="done", usage_metadata={"input_tokens": 300, "output_tokens": 40, "total_tokens": 340}),
        AIMessage(content="no usage"),
    ]
    assert tokens_from_messages(msgs) == {"llm_calls": 2, "input_tokens": 400, "output_tokens": 50}


def test_meter_records_trace_id_tools_and_summary():
    meter = QueryMeter(model="gemini-3.1-flash-lite")
    root, child, tool_a, tool_b = uuid4(), uuid4(), uuid4(), uuid4()
    meter.on_chain_start({}, {}, run_id=root, parent_run_id=None)
    meter.on_chain_start({}, {}, run_id=child, parent_run_id=root)       # nested chain does not replace the root
    meter.on_chat_model_start({}, [], run_id=uuid4(), parent_run_id=child)
    meter.on_tool_start({"name": "lookup_financial_fact"}, "x", run_id=tool_a)
    time.sleep(0.01)
    meter.on_tool_end("ok", run_id=tool_a)
    meter.on_tool_start({"name": "search_filings"}, "y", run_id=tool_b)
    meter.on_tool_error(RuntimeError("boom"), run_id=tool_b)
    resp = SimpleNamespace(generations=[[SimpleNamespace(message=SimpleNamespace(
        usage_metadata={"input_tokens": 1000, "output_tokens": 200}))]], llm_output=None)
    meter.on_llm_end(resp)
    meter.on_chain_end({}, run_id=root, parent_run_id=None)

    s = meter.summary()
    assert s["trace_id"] == str(root)
    assert s["llm_calls"] == 1 and s["input_tokens"] == 1000 and s["output_tokens"] == 200
    assert s["cost_usd"] == pytest.approx((1000 * 0.25 + 200 * 1.50) / 1e6)
    assert s["tools"] == {"lookup_financial_fact": 1, "search_filings": 1}
    assert [t["name"] for t in s["tool_calls"]] == ["lookup_financial_fact", "search_filings"]
    assert s["tool_calls"][0]["ms"] >= 9 and s["tool_calls"][0]["error"] is False
    assert s["tool_calls"][1]["error"] is True
    assert s["latency_ms"] >= 9 and s["tool_ms_total"] >= s["tool_calls"][0]["ms"]


def test_summary_prefers_token_counts_from_messages():
    meter = QueryMeter(model="gemini-3.1-flash-lite")
    meter.input_tokens, meter.output_tokens, meter.llm_calls = 5, 5, 9
    msgs = [AIMessage(content="a", usage_metadata={"input_tokens": 700, "output_tokens": 30, "total_tokens": 730})]
    s = meter.summary(messages=msgs)
    assert (s["llm_calls"], s["input_tokens"], s["output_tokens"], s["total_tokens"]) == (1, 700, 30, 730)
    # messages without usage fall back to the callback's own count
    s2 = meter.summary(messages=[AIMessage(content="a")])
    assert (s2["llm_calls"], s2["input_tokens"]) == (9, 5)


def test_public_meta_is_a_stable_subset():
    meter = QueryMeter(model="gemini-3.1-flash-lite")
    meta = public_meta(meter.summary())
    assert set(meta) == {"trace_id", "latency_ms", "llm_calls", "input_tokens", "output_tokens", "cost_usd", "tools", "tool_ms_total"}
    assert meta["tools"] == {} and meta["cost_usd"] == 0.0
