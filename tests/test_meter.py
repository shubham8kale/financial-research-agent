# tests/test_meter.py
#
# The per-run meter (agent/meter.py) and the price table (eval/pricing.py):
# what one agent run cost in time, calls, tokens and dollars, and which
# tools it called.  Callback events are driven by hand; no model runs.

import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.meter import (ARG_VALUE_CHARS, ARGS_TOTAL_CHARS, ERROR_MESSAGE_CHARS, QueryMeter, public_meta, safe_args,
                         tokens_from_messages)
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


# ── tool-call detail: arguments, start offset, error message, thread safety ──────────────────────────────────

def _run_tool(meter, name, args, *, run_id=None, fail=None, sleep=0.0):
    run_id = run_id or uuid4()
    meter.on_tool_start({"name": name}, str(args), run_id=run_id, inputs=args)
    if sleep:
        time.sleep(sleep)
    if fail is None:
        meter.on_tool_end("ok", run_id=run_id)
    else:
        meter.on_tool_error(fail, run_id=run_id)
    return run_id


def test_tool_arguments_start_offset_and_error_message_are_recorded_and_old_keys_survive():
    meter = QueryMeter(model="gemini-3.1-flash-lite")
    meter.on_chain_start({}, {}, run_id=uuid4(), parent_run_id=None)
    _run_tool(meter, "lookup_financial_fact", {"ticker": "AAPL", "concept": "net income", "fiscal_year": 2025}, sleep=0.01)
    _run_tool(meter, "lookup_financial_fact", {"ticker": "AAPL", "fiscal_year": 2025},
              fail=ValueError("1 validation error for lookup_financial_fact\nconcept\n  Field required"))
    first, second = meter.summary()["tool_calls"]
    # the keys that existed before are exactly as they were
    assert {"name", "ms", "error"} <= set(first) and first["name"] == "lookup_financial_fact"
    assert isinstance(first["ms"], float) and first["error"] is False
    assert first["args"] == {"ticker": "AAPL", "concept": "net income", "fiscal_year": 2025}
    assert "error_message" not in first                       # only a failed call carries one
    assert second["error"] is True and second["args"] == {"ticker": "AAPL", "fiscal_year": 2025}
    assert "concept" in second["error_message"] and "Field required" in second["error_message"]
    # t0_ms is the start offset from the run's start, so it is ordered by start, not by completion
    assert 0 <= first["t0_ms"] <= second["t0_ms"]


def test_error_message_is_cut_to_300_characters():
    meter = QueryMeter()
    _run_tool(meter, "search_filings", {"query": "q"}, fail=RuntimeError("x" * 1000))
    assert len(meter.summary()["tool_calls"][0]["error_message"]) == ERROR_MESSAGE_CHARS == 300


def test_safe_args_bounds_values_and_the_whole_object():
    assert safe_args({"query": "a" * 500, "n": 3, "flag": True, "none": None, "tickers": ["AAPL", "MSFT"]}) == {
        "query": "a" * ARG_VALUE_CHARS, "n": 3, "flag": True, "none": None, "tickers": '["AAPL", "MSFT"]'}
    wide = {f"k{i}": "v" * 150 for i in range(10)}            # each value fits, the object does not
    cut = safe_args(wide)
    assert list(cut) == ["_truncated"] and len(cut["_truncated"]) == ARGS_TOTAL_CHARS == 500
    assert safe_args({"ticker": "AAPL"}) == {"ticker": "AAPL"}


def test_safe_args_falls_back_to_the_text_form_and_never_raises():
    assert safe_args(None, "{'ticker': 'AAPL', 'concept': 'revenue'}") == {"ticker": "AAPL", "concept": "revenue"}
    assert safe_args(None, '{"ticker": "AAPL"}') == {"ticker": "AAPL"}
    assert safe_args(None, "not a dict") == {} and safe_args(None, None) == {} and safe_args("scalar") == {}
    assert safe_args({"obj": object()})["obj"].startswith("\"<object object")                  # json.dumps(default=str)


def test_overlapping_tool_calls_are_both_recorded_with_overlapping_windows():
    meter = QueryMeter()
    meter.on_chain_start({}, {}, run_id=uuid4(), parent_run_id=None)
    both_started = threading.Barrier(2)

    def call(name, args):
        run_id = uuid4()
        meter.on_tool_start({"name": name}, str(args), run_id=run_id, inputs=args)
        both_started.wait(timeout=5)             # neither call ends before the other has started
        time.sleep(0.02)
        meter.on_tool_end("ok", run_id=run_id)

    threads = [threading.Thread(target=call, args=("lookup_financial_fact", {"ticker": "AAPL", "concept": "revenue"})),
               threading.Thread(target=call, args=("lookup_financial_fact", {"ticker": "MSFT", "concept": "revenue"}))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    calls = meter.summary()["tool_calls"]
    assert len(calls) == 2 and {c["args"]["ticker"] for c in calls} == {"AAPL", "MSFT"}
    a, b = calls
    assert a["t0_ms"] < b["t0_ms"] + b["ms"] and b["t0_ms"] < a["t0_ms"] + a["ms"]          # the windows overlap


def test_concurrent_appends_lose_nothing():
    meter = QueryMeter()
    meter.on_chain_start({}, {}, run_id=uuid4(), parent_run_id=None)

    def worker(w):
        for i in range(25):
            _run_tool(meter, "search_filings", {"query": f"{w}-{i}"}, fail=RuntimeError("e") if i % 5 == 0 else None)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    s = meter.summary()
    assert len(s["tool_calls"]) == 16 * 25 and s["tools"] == {"search_filings": 400}
    assert sum(1 for c in s["tool_calls"] if c["error"]) == 16 * 5
    assert len({c["args"]["query"] for c in s["tool_calls"]}) == 400


def test_public_meta_never_carries_arguments():
    meter = QueryMeter(model="gemini-3.1-flash-lite")
    _run_tool(meter, "lookup_financial_fact", {"ticker": "AAPL", "concept": "net income secret-marker"})
    meta = public_meta(meter.summary())
    assert set(meta) == {"trace_id", "latency_ms", "llm_calls", "input_tokens", "output_tokens", "cost_usd", "tools", "tool_ms_total"}
    assert "secret-marker" not in json.dumps(meta) and meta["tools"] == {"lookup_financial_fact": 1}


def test_a_real_toolnode_reports_arguments_overlap_and_the_missing_argument_failure():
    # The same events through LangGraph's own ToolNode, with a scripted model and no network: three tool calls in ONE
    # model step run concurrently on worker threads, one of them without the required `concept`.
    from langchain.tools import tool
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langgraph.prebuilt import create_react_agent

    class Scripted(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def lookup_financial_fact(ticker: str, concept: str, fiscal_year: int | None = None) -> str:
        """Look up a fact."""
        time.sleep(0.05)
        return f"[1] ticker={ticker}  fact_id=1\n    {concept} {fiscal_year}"

    step = AIMessage(content="", tool_calls=[
        {"name": "lookup_financial_fact", "args": {"ticker": "AAPL", "concept": "revenue", "fiscal_year": 2025}, "id": "c1"},
        {"name": "lookup_financial_fact", "args": {"ticker": "MSFT", "concept": "revenue", "fiscal_year": 2025}, "id": "c2"},
        {"name": "lookup_financial_fact", "args": {"ticker": "AAPL", "fiscal_year": 2025}, "id": "c3"},
    ])
    graph = create_react_agent(model=Scripted(messages=iter([step, AIMessage(content="done")])),
                               tools=[lookup_financial_fact], prompt="x")
    meter = QueryMeter(model="gemini-3.1-flash-lite")
    graph.invoke({"messages": [("human", "q")]}, config={"recursion_limit": 20, "callbacks": [meter]})

    calls = meter.summary()["tool_calls"]
    assert len(calls) == 3 and [c["name"] for c in calls] == ["lookup_financial_fact"] * 3
    by_ticker = {(c["args"]["ticker"], "concept" in c["args"]): c for c in calls}
    ok_a, ok_b, bad = by_ticker[("AAPL", True)], by_ticker[("MSFT", True)], by_ticker[("AAPL", False)]
    assert ok_a["args"] == {"ticker": "AAPL", "concept": "revenue", "fiscal_year": 2025} and not ok_a["error"]
    assert bad["error"] is True and "concept" in bad["error_message"] and "Field required" in bad["error_message"]
    assert ok_a["t0_ms"] < ok_b["t0_ms"] + ok_b["ms"] and ok_b["t0_ms"] < ok_a["t0_ms"] + ok_a["ms"]   # one step, overlapping


def test_safe_args_never_raises_on_a_text_that_is_not_a_literal_dict():
    assert safe_args(None, "{[1]: 2}") == {}                  # an unhashable key: literal_eval raises TypeError
    assert safe_args(None, "(" * 5000) == {}                  # too deep to parse
    assert safe_args(None, "{'a': 1") == {}                   # unterminated
