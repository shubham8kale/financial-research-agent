# agent/meter.py
#
# PURPOSE
# -------
# Everything a single agent run cost, measured by us rather than read off a
# vendor dashboard: wall-clock latency, how many model calls it made, how many
# tokens went in and out, what that costs at the published price, which tools
# it called and how long each took, and the root run id — the id LangSmith
# shows as the trace when tracing is on, so a number in a results file can be
# opened as a trace.
#
# The meter is a LangChain callback handler.  Pass one instance per run in
# ``config={"callbacks": [meter]}``; LangGraph propagates it to every model and
# tool call underneath.  Token counts are taken from the messages the run
# returns (each AIMessage carries ``usage_metadata``), with the callback's own
# count as the fallback, because the messages are what the harness stores and
# a stored number should be recomputable from stored data.

import time
from collections import Counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from agent.pricing import cost_usd


def tokens_from_messages(messages) -> dict:
    """Sum ``usage_metadata`` over the AI messages of one run."""
    tokens_in = tokens_out = 0
    calls = 0
    for m in messages or []:
        usage = getattr(m, "usage_metadata", None)
        if usage:
            calls += 1
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
    return {"llm_calls": calls, "input_tokens": tokens_in, "output_tokens": tokens_out}


class QueryMeter(BaseCallbackHandler):
    """Per-run latency, model calls, tokens, cost, tool timings and the root run id."""

    run_inline = True  # called on the caller's thread: no executor hops, no lost timings

    def __init__(self, model: str | None = None):
        self.model = model
        self._t0 = time.perf_counter()
        self._t_end: float | None = None
        self.root_run_id: str | None = None
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls: list[dict] = []
        self._open_tools: dict[UUID, tuple[str, float]] = {}

    # ── run boundaries ─────────────────────────────────────────────────

    def on_chain_start(self, serialized, inputs, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs):
        if parent_run_id is None and self.root_run_id is None:
            self.root_run_id = str(run_id)
            self._t0 = time.perf_counter()

    def on_chain_end(self, outputs, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs):
        if parent_run_id is None and str(run_id) == self.root_run_id:
            self._t_end = time.perf_counter()

    def on_chain_error(self, error, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs):
        if parent_run_id is None and str(run_id) == self.root_run_id:
            self._t_end = time.perf_counter()

    def mark_end(self) -> None:
        """Close the latency window now.

        The agent's own chain ends before the output contract's structuring
        call; a caller that verifies the draft calls this afterwards so the
        latency it reports is the latency of the answer it served.
        """
        self._t_end = time.perf_counter()

    # ── model calls ────────────────────────────────────────────────────

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.llm_calls += 1

    def on_llm_start(self, serialized, prompts, **kwargs):
        self.llm_calls += 1

    def on_llm_end(self, response, **kwargs):
        try:
            gen = response.generations[0][0]
            usage = getattr(getattr(gen, "message", None), "usage_metadata", None)
        except (IndexError, AttributeError):
            usage = None
        if not usage and getattr(response, "llm_output", None):
            usage = response.llm_output.get("usage_metadata") or response.llm_output.get("token_usage")
        if usage:
            self.input_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)

    # ── tools ──────────────────────────────────────────────────────────

    def on_tool_start(self, serialized, input_str, *, run_id: UUID, **kwargs):
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._open_tools[run_id] = (name, time.perf_counter())

    def on_tool_end(self, output, *, run_id: UUID, **kwargs):
        self._close_tool(run_id, error=False)

    def on_tool_error(self, error, *, run_id: UUID, **kwargs):
        self._close_tool(run_id, error=True)

    def _close_tool(self, run_id: UUID, error: bool) -> None:
        name, t0 = self._open_tools.pop(run_id, ("tool", time.perf_counter()))
        self.tool_calls.append({"name": name, "ms": round((time.perf_counter() - t0) * 1000, 1), "error": error})

    # ── summary ────────────────────────────────────────────────────────

    def summary(self, messages=None) -> dict:
        """The run's meter as a plain dict.  *messages* (the run's output) is the preferred token source."""
        end = self._t_end if self._t_end is not None else time.perf_counter()
        from_msgs = tokens_from_messages(messages) if messages is not None else None
        if from_msgs and (from_msgs["input_tokens"] or from_msgs["output_tokens"]):
            llm_calls, tin, tout = from_msgs["llm_calls"], from_msgs["input_tokens"], from_msgs["output_tokens"]
        else:
            llm_calls, tin, tout = self.llm_calls, self.input_tokens, self.output_tokens
        counts = Counter(t["name"] for t in self.tool_calls)
        return {
            "trace_id": self.root_run_id,
            "model": self.model,
            "latency_ms": round((end - self._t0) * 1000, 1),
            "llm_calls": llm_calls,
            "input_tokens": tin,
            "output_tokens": tout,
            "total_tokens": tin + tout,
            "cost_usd": cost_usd(self.model, tin, tout),
            "tool_calls": self.tool_calls,
            "tools": dict(counts),
            "tool_ms_total": round(sum(t["ms"] for t in self.tool_calls), 1),
        }


def public_meta(summary: dict[str, Any]) -> dict[str, Any]:
    """The subset of a meter summary that is safe and useful to return to a client."""
    return {
        "trace_id": summary.get("trace_id"),
        "latency_ms": summary.get("latency_ms"),
        "llm_calls": summary.get("llm_calls"),
        "input_tokens": summary.get("input_tokens"),
        "output_tokens": summary.get("output_tokens"),
        "cost_usd": summary.get("cost_usd"),
        "tools": summary.get("tools") or {},
        "tool_ms_total": summary.get("tool_ms_total"),
    }
