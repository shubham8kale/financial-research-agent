# tests/test_terminal_failures.py
#
# The agent can stop without producing a usable answer in two distinct ways, and
# both were observed on this corpus during evaluation:
#
#   empty_answer     the model returns a final message with no text at all
#                    (finish_reason STOP). Measured at 10/66 items on
#                    gemini-2.5-flash-lite.
#   recursion_limit  the graph exhausts its step budget and LangGraph
#                    substitutes its own placeholder string.
#
# Before the guard, neither was detected: /query returned HTTP 200 with an empty
# answer, /query/stream rendered a blank bubble with sources attached, and the
# recursion placeholder streamed through as if it were the model's considered
# answer. These tests exist so that regression cannot happen silently again.
#
# Runs without a real LLM, a built ChromaDB index, or GEMINI_API_KEY: fake agents
# are injected into app.state, and TestClient is deliberately NOT used as a
# context manager so the app lifespan (which would build the real agents) never
# runs.

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from agent.financial_agent import (
    OUTCOME_EMPTY_ANSWER,
    OUTCOME_RECURSION_LIMIT,
    AgentTerminalFailure,
    EmptyAnswerError,
    RecursionLimitError,
    classify_terminal_state,
    raise_for_terminal_state,
)
from api import main as api_main

RECURSION_TEXT = "Sorry, need more steps to process this request."


# ── Classifier ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "answer,expected",
    [
        ("", OUTCOME_EMPTY_ANSWER),
        ("   ", OUTCOME_EMPTY_ANSWER),
        ("\n\t ", OUTCOME_EMPTY_ANSWER),
        (None, OUTCOME_EMPTY_ANSWER),
        (RECURSION_TEXT, OUTCOME_RECURSION_LIMIT),
        (RECURSION_TEXT.upper(), OUTCOME_RECURSION_LIMIT),
        ("Total net sales were $416,161 million.", None),
        # A real answer that merely mentions steps must NOT be misclassified.
        ("The filing describes the steps management took to reduce costs.", None),
    ],
)
def test_classify_terminal_state(answer, expected):
    assert classify_terminal_state(answer) == expected


def test_raise_for_terminal_state_raises_named_errors():
    with pytest.raises(EmptyAnswerError) as empty:
        raise_for_terminal_state("")
    assert empty.value.outcome == OUTCOME_EMPTY_ANSWER

    with pytest.raises(RecursionLimitError) as recursion:
        raise_for_terminal_state(RECURSION_TEXT)
    assert recursion.value.outcome == OUTCOME_RECURSION_LIMIT

    # Both are catchable through the shared base class.
    for bad in ("", RECURSION_TEXT):
        with pytest.raises(AgentTerminalFailure):
            raise_for_terminal_state(bad)


def test_raise_for_terminal_state_passes_usable_answers_through():
    answer = "Apple's total net sales in fiscal 2025 were $416,161 million."
    assert raise_for_terminal_state(answer) == answer


# ── Fakes ────────────────────────────────────────────────────────────────────

class _AgentReturning:
    """Minimal stand-in for the compiled LangGraph agent.

    Emits a ToolMessage (so source extraction finds a citation, exactly as in the
    failure we observed — sources were present while the answer was blank) and a
    final AIMessage carrying the supplied content.
    """

    def __init__(self, content):
        self._content = content

    async def ainvoke(self, payload, config=None):
        return {
            "messages": [
                ToolMessage(
                    content="[1] ticker=AAPL  chunk_idx=42\n    Total net sales $ 416,161",
                    tool_call_id="call_1",
                ),
                AIMessage(content=self._content),
            ]
        }


def _install(monkeypatch, content):
    api_main.app.state.mcp_agent = None
    api_main.app.state.direct_agent = _AgentReturning(content)
    return TestClient(api_main.app)


def _parse_sse(text: str):
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# ── /query must not serve a non-answer as success ────────────────────────────

@pytest.mark.parametrize(
    "content,outcome",
    [
        ("", OUTCOME_EMPTY_ANSWER),
        ("   ", OUTCOME_EMPTY_ANSWER),
        (RECURSION_TEXT, OUTCOME_RECURSION_LIMIT),
    ],
)
def test_query_rejects_terminal_failures(monkeypatch, content, outcome):
    client = _install(monkeypatch, content)
    resp = client.post("/query", json={"question": "What was Apple's revenue?"})

    # 502, not 200-with-blank and not 500: the agent ran fine, its upstream model
    # returned something unusable.
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail and isinstance(detail, str)
    # Provider internals must never reach a public endpoint (see SECURITY.md).
    for leak in ("gemini", "groq", "openai", "quota", "api_key", "Traceback"):
        assert leak.lower() not in detail.lower()


def test_query_still_serves_a_real_answer(monkeypatch):
    client = _install(monkeypatch, "Total net sales were $416,161 million.")
    resp = client.post("/query", json={"question": "What was Apple's revenue?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Total net sales were $416,161 million."
    assert any(s["ticker"] == "AAPL" for s in body["sources"])


# ── /query/stream must emit an error event, not a blank answer ────────────────

@pytest.mark.parametrize(
    "content,outcome",
    [
        ("", OUTCOME_EMPTY_ANSWER),
        (RECURSION_TEXT, OUTCOME_RECURSION_LIMIT),
    ],
)
def test_stream_emits_error_for_terminal_failures(monkeypatch, content, outcome):
    client = _install(monkeypatch, content)
    resp = client.post(
        "/query/stream", json={"question": "What was Apple's revenue?"}
    )
    assert resp.status_code == 200  # the SSE channel itself opens fine
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    assert "error" in types
    err = next(e for e in events if e["type"] == "error")
    assert err["outcome"] == outcome
    # The client must not be handed a blank answer or a false completion.
    assert "token" not in types
    assert "done" not in types
    # The recursion placeholder must not be echoed back as if it were content.
    assert RECURSION_TEXT not in json.dumps(events)


def test_stream_still_streams_a_real_answer(monkeypatch):
    client = _install(monkeypatch, "Total net sales were $416,161 million.")
    resp = client.post(
        "/query/stream", json={"question": "What was Apple's revenue?"}
    )
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "token" in types and types[-1] == "done"
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert answer == "Total net sales were $416,161 million."
