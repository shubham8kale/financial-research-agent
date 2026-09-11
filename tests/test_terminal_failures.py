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

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from agent import financial_agent, mcp_agent
from agent.financial_agent import (
    OUTCOME_EMPTY_ANSWER,
    OUTCOME_RECURSION_LIMIT,
    AgentTerminalFailure,
    EmptyAnswerError,
    RecursionLimitError,
    classify_terminal_state,
    content_text,
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


# ── List-shaped message content ──────────────────────────────────────────────
#
# Message content is not reliably a string. The shipped agent model returns a
# LIST of content blocks on most questions — 60 of 66 items in the recorded
# rerun (eval/results/rerun66-af83fa6.json, final_content_type) — and every
# caller that touched .content directly broke on it:
#
#   agent.financial_agent.run_agent   handed the raw list to the classifier,
#                                     which called .strip() on it  → AttributeError
#   retrieval.query_engine.ask        called .content.strip()      → AttributeError
#   agent.mcp_agent.arun_agent        returned the list unchanged  → repr in the answer
#
# Those are the two commands README step 5 tells a new user to run first. The
# API and the eval harness escaped only because each had its own private copy of
# the flattening logic. There is now one copy, here, and these tests feed list
# content through every entry point that reaches it.

LIST_CONTENT = [
    {"type": "text", "text": "Total net sales were $416,161 million.", "extras": {}},
]


@pytest.mark.parametrize(
    "content,expected",
    [
        # Plain strings pass through untouched.
        ("already text", "already text"),
        ("", ""),
        (None, ""),
        # The shape the shipped model actually returns.
        (LIST_CONTENT, "Total net sales were $416,161 million."),
        # Multiple blocks are concatenated in order, not joined with separators.
        (
            [{"type": "text", "text": "Apple: $416,161M. "},
             {"type": "text", "text": "Microsoft: $245,122M."}],
            "Apple: $416,161M. Microsoft: $245,122M.",
        ),
        # Bare strings inside the list are kept.
        (["a", {"type": "text", "text": "b"}], "ab"),
        # Non-text blocks contribute nothing rather than leaking a repr.
        ([{"type": "image_url", "image_url": "x"}], ""),
        ([{"type": "text", "text": "kept"}, {"type": "thinking", "thinking": "no"}],
         "kept"),
        # An empty list is an empty answer, not the string "[]".
        ([], ""),
    ],
)
def test_content_text_flattens_every_shape(content, expected):
    assert content_text(content) == expected


def test_content_text_never_leaks_a_python_repr():
    for content in (LIST_CONTENT, [{"type": "image_url", "image_url": "x"}], []):
        flattened = content_text(content)
        assert "{" not in flattened and "[" not in flattened


def test_classify_terminal_state_accepts_list_content():
    # The regression: this raised AttributeError: 'list' object has no
    # attribute 'strip' before content_text was shared.
    assert classify_terminal_state(LIST_CONTENT) is None
    assert classify_terminal_state([]) == OUTCOME_EMPTY_ANSWER
    assert classify_terminal_state(
        [{"type": "text", "text": "   "}]
    ) == OUTCOME_EMPTY_ANSWER
    assert classify_terminal_state(
        [{"type": "text", "text": RECURSION_TEXT}]
    ) == OUTCOME_RECURSION_LIMIT


def test_raise_for_terminal_state_returns_flattened_text():
    # Returns the flattened string, not the input, so callers can print and
    # slice what comes back.
    assert raise_for_terminal_state(LIST_CONTENT) == (
        "Total net sales were $416,161 million."
    )
    with pytest.raises(EmptyAnswerError):
        raise_for_terminal_state([])
    with pytest.raises(RecursionLimitError):
        raise_for_terminal_state([{"type": "text", "text": RECURSION_TEXT}])


class _SyncAgentReturning:
    """Stand-in for the compiled graph, for the synchronous run_agent path."""

    def __init__(self, content):
        self._content = content

    def invoke(self, payload, config=None):
        return {"messages": [AIMessage(content=self._content)]}


def test_run_agent_returns_text_for_list_content(monkeypatch):
    # This is `python -m agent.financial_agent` — README step 5.
    monkeypatch.setattr(
        financial_agent, "build_agent_executor",
        lambda: _SyncAgentReturning(LIST_CONTENT),
    )
    assert financial_agent.run_agent("What were Apple's total net sales?") == (
        "Total net sales were $416,161 million."
    )


def test_run_agent_still_raises_on_list_shaped_non_answers(monkeypatch):
    monkeypatch.setattr(
        financial_agent, "build_agent_executor", lambda: _SyncAgentReturning([]),
    )
    with pytest.raises(EmptyAnswerError):
        financial_agent.run_agent("q")

    monkeypatch.setattr(
        financial_agent, "build_agent_executor",
        lambda: _SyncAgentReturning([{"type": "text", "text": RECURSION_TEXT}]),
    )
    with pytest.raises(RecursionLimitError):
        financial_agent.run_agent("q")


def test_arun_agent_returns_text_for_list_content(monkeypatch):
    # This is `python -m agent.mcp_agent`, which returned the raw list before.
    async def _build():
        return _AgentReturning(LIST_CONTENT)

    monkeypatch.setattr(mcp_agent, "build_agent_executor", _build)
    answer = asyncio.run(mcp_agent.arun_agent("What were Apple's total net sales?"))
    assert answer == "Total net sales were $416,161 million."


def test_api_and_agent_flatten_identically(monkeypatch):
    # The point of sharing the helper: one string, whatever the layer.
    client = _install(monkeypatch, LIST_CONTENT)
    resp = client.post("/query", json={"question": "What was Apple's revenue?"})
    assert resp.status_code == 200, resp.text

    monkeypatch.setattr(
        financial_agent, "build_agent_executor",
        lambda: _SyncAgentReturning(LIST_CONTENT),
    )
    assert resp.json()["answer"] == financial_agent.run_agent("q")
