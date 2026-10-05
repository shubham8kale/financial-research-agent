# tests/test_query_threads.py
#
# Conversation memory on the API (agent/memory.py): a follow-up carries the
# thread's earlier turns in front of its question, a request without a thread id
# is byte-for-byte what it always was, an invalid id is a 422, /query and
# /query/stream behave identically, and an answer that did not pass
# verification is never remembered.  Stub agents, no model, no key, no index.

import json
import logging

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from agent.contract import Verification
from agent.memory import HISTORY_PREFACE, ThreadMemory
from api import main as api_main

CHUNK = "[1] ticker=AAPL  chunk_idx=42\n    Total net sales were $416,161 million."
T1, T2 = "thread-aaaaaaaa", "thread-bbbbbbbb"


class Agent:
    """Records every payload and config; answers with one observation and a canned final message."""

    def __init__(self, answer="Total net sales were $416,161 million."):
        self.answer = answer
        self.payloads, self.configs = [], []

    async def ainvoke(self, payload, config=None):
        self.payloads.append(payload)
        self.configs.append(config)
        return {"messages": [ToolMessage(content=CHUNK, tool_call_id="c1"), AIMessage(content=self.answer)]}


@pytest.fixture
def api(monkeypatch):
    """(client, agent): the direct agent is the stub, VERIFY_MODE is off (conftest), memory starts empty."""
    monkeypatch.delenv("THREAD_MEMORY", raising=False)
    agent = Agent()
    api_main.app.state.mcp_agent = None
    api_main.app.state.direct_agent = agent
    api_main.app.state.thread_memory = ThreadMemory()
    return TestClient(api_main.app), agent


def sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


def ask(client, endpoint, question, **extra):
    """POST one question; returns (status, answer, meta) whichever endpoint it was."""
    body = {"question": question, **extra}
    resp = client.post(endpoint, json=body)
    if endpoint == "/query":
        data = resp.json() if resp.status_code == 200 else {}
        return resp.status_code, data.get("answer"), data.get("meta")
    events = sse(resp.text)
    meta = next((e for e in events if e["type"] == "meta"), None)
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    return resp.status_code, answer or None, meta


ENDPOINTS = pytest.mark.parametrize("endpoint", ["/query", "/query/stream"])


@ENDPOINTS
def test_a_request_without_a_thread_id_sends_exactly_todays_payload(api, endpoint):
    client, agent = api
    assert ask(client, endpoint, "What was Apple's revenue?")[0] == 200
    assert agent.payloads == [{"messages": [("human", "What was Apple's revenue?")]}]
    assert ask(client, endpoint, "And its net income?", ticker="aapl")[0] == 200
    assert agent.payloads[1] == {"messages": [("human", "And its net income? (company: AAPL)")]}
    assert "metadata" not in agent.configs[0]                       # no thread, no run metadata
    assert len(api_main.app.state.thread_memory) == 0               # and nothing was stored


@ENDPOINTS
def test_a_follow_up_carries_the_earlier_turn_in_one_human_message(api, endpoint):
    client, agent = api
    ask(client, endpoint, "What was Apple's revenue?", thread_id=T1)
    assert agent.payloads[0] == {"messages": [("human", "What was Apple's revenue?")]}        # first turn: nothing to carry
    ask(client, endpoint, "And Microsoft?", thread_id=T1)
    (role, content), = agent.payloads[1]["messages"]
    assert role == "human" and len(agent.payloads[1]["messages"]) == 1
    assert content.startswith(HISTORY_PREFACE)
    assert "[Earlier turn 1] Question: What was Apple's revenue?" in content
    assert "[Earlier turn 1] Answer: Total net sales were $416,161 million." in content
    assert content.endswith("Current question:\nAnd Microsoft?")


@ENDPOINTS
def test_meta_says_which_thread_and_how_many_earlier_turns_were_shown(api, endpoint):
    client, _ = api
    metas = [ask(client, endpoint, f"question {i}", thread_id=T1)[2] for i in range(3)]
    assert [(m["thread_id"], m["thread_turns"]) for m in metas] == [(T1, 0), (T1, 1), (T1, 2)]
    plain = ask(client, endpoint, "no thread here")[2]
    assert "thread_id" not in plain and "thread_turns" not in plain       # additive: absent unless a thread was sent


@ENDPOINTS
def test_two_threads_never_see_each_others_turns(api, endpoint):
    client, agent = api
    ask(client, endpoint, "about Apple", thread_id=T1)
    ask(client, endpoint, "about Meta", thread_id=T2)
    ask(client, endpoint, "and again?", thread_id=T1)
    ask(client, endpoint, "and again?", thread_id=T2)
    first, second = agent.payloads[2]["messages"][0][1], agent.payloads[3]["messages"][0][1]
    assert "about Apple" in first and "about Meta" not in first
    assert "about Meta" in second and "about Apple" not in second


@pytest.mark.parametrize("bad", ["short", "x" * 65, "has space 12345", "semi;colon-123", "", "résumé-12345", "../../etc/passwd"])
@ENDPOINTS
def test_an_invalid_thread_id_is_a_422(api, endpoint, bad):
    client, agent = api
    assert client.post(endpoint, json={"question": "q", "thread_id": bad}).status_code == 422
    assert agent.payloads == []


@ENDPOINTS
def test_valid_thread_ids_at_both_ends_of_the_pattern_are_accepted(api, endpoint):
    client, _ = api
    for good in ("a" * 8, "A-z_0-9" + "x" * 57, "550e8400-e29b-41d4-a716-446655440000"):
        assert len(good) in (8, 64, 36) and ask(client, endpoint, "q", thread_id=good)[0] == 200


@ENDPOINTS
def test_the_off_switch_makes_the_api_ignore_the_thread_id(api, monkeypatch, endpoint):
    client, agent = api
    api_main.app.state.thread_memory = ThreadMemory(enabled=False)
    _, _, meta = ask(client, endpoint, "first", thread_id=T1)
    ask(client, endpoint, "second", thread_id=T1)
    assert agent.payloads == [{"messages": [("human", "first")]}, {"messages": [("human", "second")]}]
    assert "thread_id" not in meta and "metadata" not in agent.configs[0]
    # and the same through the environment, when no memory object exists yet
    monkeypatch.setenv("THREAD_MEMORY", "off")
    del api_main.app.state.thread_memory
    ask(client, endpoint, "third", thread_id=T1)
    ask(client, endpoint, "fourth", thread_id=T1)
    assert agent.payloads[-1] == {"messages": [("human", "fourth")]}


def _verifying(monkeypatch, status):
    """Replace the contract's structuring call with one that returns *status*; records the question it was given."""
    seen = []

    async def averify(question, draft, observations, llm=None, mode=None, callbacks=None):
        seen.append(question)
        served = draft if status in ("verified", "unverified", "skipped") else "I could not verify my draft answer."
        return served, Verification(status=status, mode="strict"), []

    monkeypatch.setattr(api_main, "averify_answer", averify)
    return seen


@pytest.mark.parametrize("status", ["refused", "unverified"])
@ENDPOINTS
def test_an_answer_that_did_not_pass_verification_is_never_remembered(api, monkeypatch, endpoint, status):
    client, agent = api
    monkeypatch.setenv("VERIFY_MODE", "strict")
    _verifying(monkeypatch, status)
    ask(client, endpoint, "What was Apple's revenue?", thread_id=T1)
    ask(client, endpoint, "And Microsoft?", thread_id=T1)
    assert agent.payloads[1] == {"messages": [("human", "And Microsoft?")]}      # nothing to carry
    assert len(api_main.app.state.thread_memory) == 0


@ENDPOINTS
def test_a_verified_answer_is_remembered_and_verification_sees_the_current_question_alone(api, monkeypatch, endpoint):
    client, agent = api
    monkeypatch.setenv("VERIFY_MODE", "strict")
    seen = _verifying(monkeypatch, "verified")
    ask(client, endpoint, "What was Apple's revenue?", thread_id=T1)
    ask(client, endpoint, "And Microsoft?", thread_id=T1)
    assert "[Earlier turn 1] Question: What was Apple's revenue?" in agent.payloads[1]["messages"][0][1]
    assert seen == ["What was Apple's revenue?", "And Microsoft?"]       # the contract never sees the history block


@ENDPOINTS
def test_a_terminal_failure_is_never_remembered(api, endpoint):
    client, agent = api
    agent.answer = "Sorry, need more steps to process this request."
    status, _, _ = ask(client, endpoint, "q1", thread_id=T1)
    assert status in (200, 502)                                           # /query is a 502, the stream an error event
    agent.answer = "A real answer."
    ask(client, endpoint, "q2", thread_id=T1)
    assert agent.payloads[1] == {"messages": [("human", "q2")]}
    assert len(api_main.app.state.thread_memory) == 1                     # only the real answer


def test_query_and_stream_build_identical_payloads_and_meta_for_the_same_conversation(api):
    client, agent = api
    runs = {}
    for endpoint in ("/query", "/query/stream"):
        agent.payloads.clear()
        api_main.app.state.thread_memory = ThreadMemory()
        metas = [ask(client, endpoint, q, thread_id=T1)[2] for q in ("one?", "two?", "three?")]
        runs[endpoint] = (list(agent.payloads), [(m["thread_id"], m["thread_turns"], m["backend"]) for m in metas])
    assert runs["/query"] == runs["/query/stream"]


def test_the_history_is_bounded_to_the_most_recent_turns(api):
    client, agent = api
    for i in range(9):
        ask(client, "/query", f"question number {i}", thread_id=T1)
    content = agent.payloads[-1]["messages"][0][1]
    assert content.count("] Question: ") == 6                             # THREAD_MEMORY_MAX_TURNS default
    assert "question number 2" in content and "question number 1" not in content


def test_the_mcp_path_remembers_and_carries_history_identically(api):
    client, direct = api
    mcp = Agent()
    api_main.app.state.mcp_agent = mcp
    try:
        ask(client, "/query", "first?", thread_id=T1)
        _, _, meta = ask(client, "/query/stream", "second?", thread_id=T1)
    finally:
        api_main.app.state.mcp_agent = None
    assert meta["backend"] == "mcp" and meta["thread_turns"] == 1
    assert "[Earlier turn 1] Question: first?" in mcp.payloads[1]["messages"][0][1] and direct.payloads == []


def test_the_thread_id_rides_as_run_metadata_only_and_no_message_content_is_logged(api, caplog):
    client, agent = api
    caplog.set_level(logging.DEBUG)
    ask(client, "/query", "a distinctive question about frobnication", thread_id=T1)
    ask(client, "/query/stream", "and a second distinctive follow-up", thread_id=T1)
    assert [c["metadata"] for c in agent.configs] == [{"thread_id": T1}, {"thread_id": T1}]
    assert all(set(c) == {"recursion_limit", "callbacks", "metadata"} for c in agent.configs)
    assert "frobnication" not in caplog.text and "distinctive follow-up" not in caplog.text
    assert T1 not in caplog.text                                         # not even the id reaches a log line
