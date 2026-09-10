# tests/test_retrieval.py
#
# Unit tests for the retrieval path: retrieval/query_engine.ask() and the
# context block it builds for the model.
#
# No network, no API key, no ChromaDB index. A fake vectorstore is injected via
# ask()'s `vectorstore` parameter and the LLM factory is monkeypatched, so these
# exercise OUR retrieval and prompt-assembly logic rather than re-testing Chroma
# or Gemini.
#
# The behaviours pinned here are the ones that would silently corrupt answers:
#   - k must actually reach the vector store (a k that is ignored means the
#     documented knob does nothing)
#   - an empty retrieval must short-circuit BEFORE the LLM call, otherwise the
#     model is asked to answer from nothing and will confabulate
#   - retrieved passages must reach the prompt intact, in rank order, with the
#     metadata the agent cites

import pytest
from langchain_core.documents import Document

import retrieval.query_engine as qe
from retrieval.query_engine import (
    INSUFFICIENT_CONTEXT_PHRASE,
    QueryResult,
    _build_context_block,
    ask,
)


# ── Fakes ────────────────────────────────────────────────────────────────────

class _FakeVectorstore:
    """Records similarity_search calls and returns a scripted document list."""

    def __init__(self, docs):
        self._docs = docs
        self.calls: list[tuple[str, int]] = []

    def similarity_search(self, query, k=5, **kwargs):
        self.calls.append((query, k))
        return list(self._docs[:k])


class _FakeLLM:
    """Stands in for ChatGoogleGenerativeAI, capturing the messages it receives."""

    def __init__(self, reply="Total net sales were $416,161 million."):
        self._reply = reply
        self.messages = None
        self.call_count = 0

    def invoke(self, messages):
        self.call_count += 1
        self.messages = messages

        class _Response:
            content = self._reply
        return _Response()


def _doc(ticker="AAPL", idx=0, text="Total net sales $ 416,161"):
    return Document(page_content=text, metadata={"ticker": ticker, "chunk_idx": idx})


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")


@pytest.fixture
def fake_llm(monkeypatch):
    llm = _FakeLLM()
    monkeypatch.setattr(qe, "_build_llm", lambda _key: llm)
    return llm


# ── Context block formatting ─────────────────────────────────────────────────

def test_context_block_numbers_passages_from_one():
    # The model is told to cite by rank; zero-indexing here would make every
    # citation off by one.
    block = _build_context_block([_doc(idx=10), _doc(idx=11)])
    assert block.startswith("[1] ")
    assert "[2] " in block
    assert "[0] " not in block


def test_context_block_carries_citation_metadata():
    block = _build_context_block([_doc(ticker="MSFT", idx=42)])
    assert "ticker=MSFT" in block
    assert "chunk_idx=42" in block


def test_context_block_tolerates_missing_metadata():
    # Documents indexed before the metadata schema settled, or written by a
    # different path, must not crash retrieval.
    block = _build_context_block([Document(page_content="orphan passage", metadata={})])
    assert "ticker=unknown" in block
    assert "chunk_idx=?" in block
    assert "orphan passage" in block


def test_context_block_preserves_passage_text_verbatim():
    # Financial figures must survive formatting untouched — a mangled number
    # here becomes a wrong answer the judge cannot detect.
    text = "Total net sales $ 416,161  6 % $ 391,035  2 % $ 383,285"
    assert text in _build_context_block([_doc(text=text)])


def test_context_block_separates_passages_with_a_blank_line():
    block = _build_context_block([_doc(idx=1, text="first"), _doc(idx=2, text="second")])
    assert "first\n\n[2]" in block


def test_context_block_empty_input():
    assert _build_context_block([]) == ""


# ── ask(): guard rails ───────────────────────────────────────────────────────

def test_ask_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(EnvironmentError) as exc:
        ask("What was Apple's revenue?", vectorstore=_FakeVectorstore([_doc()]))
    assert "GEMINI_API_KEY" in str(exc.value)


def test_ask_short_circuits_when_retrieval_is_empty(api_key, fake_llm):
    # This is the important one. With no passages there is nothing to ground an
    # answer in, so the LLM must not be called at all — calling it would invite
    # an answer from parametric memory presented as if it came from a filing.
    vs = _FakeVectorstore([])
    result = ask("What was Apple's revenue?", vectorstore=vs)

    assert isinstance(result, QueryResult)
    assert result.answer == INSUFFICIENT_CONTEXT_PHRASE
    assert result.sources == []
    assert fake_llm.call_count == 0, "LLM must not be invoked with zero context"


def test_ask_returns_sources_alongside_the_answer(api_key, fake_llm):
    docs = [_doc(idx=1), _doc(idx=2)]
    result = ask("What was Apple's revenue?", vectorstore=_FakeVectorstore(docs))

    assert result.answer == "Total net sales were $416,161 million."
    assert len(result.sources) == 2
    assert [d.metadata["chunk_idx"] for d in result.sources] == [1, 2]


# ── ask(): retrieval wiring ──────────────────────────────────────────────────

def test_ask_passes_the_question_and_default_k_to_the_vector_store(api_key, fake_llm):
    vs = _FakeVectorstore([_doc(idx=i) for i in range(10)])
    ask("What was Apple's revenue?", vectorstore=vs)

    assert len(vs.calls) == 1
    query, k = vs.calls[0]
    assert query == "What was Apple's revenue?"
    assert k == qe.TOP_K


def test_ask_honours_an_explicit_k(api_key, fake_llm):
    # k is a documented knob; if it were dropped, callers tuning it would see
    # no effect and reach false conclusions about retrieval depth.
    vs = _FakeVectorstore([_doc(idx=i) for i in range(10)])
    result = ask("Compare all five companies", k=3, vectorstore=vs)

    assert vs.calls[0][1] == 3
    assert len(result.sources) == 3


# ── ask(): prompt assembly ───────────────────────────────────────────────────

def test_ask_sends_system_then_human_message(api_key, fake_llm):
    ask("What was Apple's revenue?", vectorstore=_FakeVectorstore([_doc()]))
    system, human = fake_llm.messages
    assert type(system).__name__ == "SystemMessage"
    assert type(human).__name__ == "HumanMessage"


def test_ask_puts_every_retrieved_passage_in_the_prompt(api_key, fake_llm):
    docs = [
        _doc(ticker="AAPL", idx=1, text="Apple total net sales $ 416,161"),
        _doc(ticker="MSFT", idx=2, text="Microsoft total revenue $ 258,661"),
    ]
    ask("Compare Apple and Microsoft", vectorstore=_FakeVectorstore(docs))

    human_text = fake_llm.messages[1].content
    assert "416,161" in human_text
    assert "258,661" in human_text
    assert "ticker=AAPL" in human_text and "ticker=MSFT" in human_text


def test_ask_prompt_preserves_retrieval_rank_order(api_key, fake_llm):
    # Rank encodes similarity. Reordering would make "[1]" no longer the best
    # match and quietly degrade every citation the model produces.
    docs = [_doc(idx=1, text="FIRST PASSAGE"), _doc(idx=2, text="SECOND PASSAGE")]
    ask("q", vectorstore=_FakeVectorstore(docs))

    human_text = fake_llm.messages[1].content
    assert human_text.index("FIRST PASSAGE") < human_text.index("SECOND PASSAGE")


def test_ask_includes_the_question_in_the_human_message(api_key, fake_llm):
    question = "What were Apple's total net sales in fiscal 2025?"
    ask(question, vectorstore=_FakeVectorstore([_doc()]))
    assert question in fake_llm.messages[1].content


def test_ask_strips_surrounding_whitespace_from_the_answer(api_key, monkeypatch):
    llm = _FakeLLM(reply="   \n Total net sales were $416,161 million.  \n ")
    monkeypatch.setattr(qe, "_build_llm", lambda _key: llm)
    result = ask("q", vectorstore=_FakeVectorstore([_doc()]))
    assert result.answer == "Total net sales were $416,161 million."
