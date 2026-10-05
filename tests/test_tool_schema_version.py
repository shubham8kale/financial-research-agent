# tests/test_tool_schema_version.py
#
# The fingerprint of what the model is shown about the tools.  _prompt_version()
# hashes the system prompt only, so a tool docstring or schema edit would
# otherwise change nothing the harness records; this pins that it does.

import json
import re

from agent import financial_agent
from eval import run_eval


def test_the_fingerprint_is_stable_and_names_a_12_hex_hash():
    v = financial_agent.tool_schema_version()
    assert re.fullmatch(r"sha256:[0-9a-f]{12}", v)
    assert v == financial_agent.tool_schema_version() == run_eval._tool_schema_version()


def test_it_covers_the_five_tools_the_agent_is_bound_to():
    assert [t.name for t in financial_agent.TOOLS] == [
        "search_filings", "list_available_companies", "compare_companies", "lookup_financial_fact", "compute_metric"]


def test_a_tool_description_change_changes_the_fingerprint_and_not_the_prompt_version(monkeypatch):
    before, prompt_before = financial_agent.tool_schema_version(), run_eval._prompt_version()
    monkeypatch.setattr(financial_agent.lookup_financial_fact, "description",
                        financial_agent.lookup_financial_fact.description + " concept is REQUIRED.")
    assert financial_agent.tool_schema_version() != before
    assert run_eval._prompt_version() == prompt_before          # the trap this fingerprint exists for


def test_an_argument_schema_change_changes_the_fingerprint(monkeypatch):
    from langchain_core.tools import StructuredTool

    def lookup(ticker: str, concept: str, fiscal_year: int | None = None) -> str:
        """Look up a fact."""
        return ""

    def lookup_loose(ticker: str, concept: str = "", fiscal_year: int | None = None) -> str:
        """Look up a fact."""
        return ""

    strict = StructuredTool.from_function(lookup, name="lookup_financial_fact")
    loose = StructuredTool.from_function(lookup_loose, name="lookup_financial_fact")
    base = list(financial_agent.TOOLS)
    swap = lambda tool: [tool if t.name == "lookup_financial_fact" else t for t in base]  # noqa: E731
    monkeypatch.setattr(financial_agent, "TOOLS", swap(strict))
    a = financial_agent.tool_schema_version()
    monkeypatch.setattr(financial_agent, "TOOLS", swap(loose))
    assert financial_agent.tool_schema_version() != a


# ── the fingerprint is part of the cache key main() builds ─────────────────────────────────────────────────

def _drive_main(monkeypatch, tmp_path, *argv):
    """Run eval.run_eval.main() with the agent stubbed out and nothing written outside tmp_path; returns its exit code."""
    import sys

    from eval import leaderboard

    monkeypatch.setattr(run_eval, "load_dotenv", lambda *a, **k: None)            # a developer's .env must not change the key
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")                       # resolve_judge wants one; nothing here calls Gemini
    monkeypatch.setenv("VERIFY_MODE", "off")
    for var in ("RETRIEVAL_MODE", "RETRIEVAL_K", "RETRIEVAL_FETCH_K", "RETRIEVAL_RRF_K", "RETRIEVAL_DENSE_WEIGHT",
                "RETRIEVAL_SPARSE_WEIGHT", "RETRIEVAL_RERANK", "RETRIEVAL_RERANK_MODEL", "RETRIEVAL_TICKER_FILTER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(run_eval, "AGENT_SLEEP_SECONDS", 0)
    monkeypatch.setattr(leaderboard, "write_leaderboard", lambda results_dir: results_dir / "LEADERBOARD.md")
    monkeypatch.setattr(sys, "argv", ["run_eval", "--ids", "qa_0001", "--label", "t", "--out", str(tmp_path / "out.json"),
                                      "--cache-file", str(tmp_path / "cache.json"), *argv])
    return run_eval.main()


def test_a_tool_docstring_change_changes_the_cache_key_main_builds(monkeypatch, tmp_path):
    calls = []

    def capture(question, verify_mode="off"):
        calls.append(question)
        return "an answer", ["[1] ticker=AAPL  chunk_idx=1\n    text"], 3, {"meter": None}

    monkeypatch.setattr(run_eval, "run_agent_capture", capture)
    cache_path = tmp_path / "cache.json"

    assert _drive_main(monkeypatch, tmp_path, "--generate-only") == 0
    (first_key,) = json.loads(cache_path.read_text(encoding="utf-8"))
    assert first_key.endswith("|ts=" + run_eval._tool_schema_tag(financial_agent.tool_schema_version()))
    assert _drive_main(monkeypatch, tmp_path, "--generate-only") == 0
    assert len(calls) == 1                                           # the same tools: served from the cache

    monkeypatch.setattr(financial_agent.lookup_financial_fact, "description",
                        financial_agent.lookup_financial_fact.description + " A docstring-only change.")
    assert _drive_main(monkeypatch, tmp_path, "--generate-only") == 0
    keys = set(json.loads(cache_path.read_text(encoding="utf-8")))
    assert len(calls) == 2 and len(keys) == 2 and first_key in keys   # a new key: regenerated, the old entry untouched
    assert {k.rsplit("|ts=", 1)[0] for k in keys} == {first_key.rsplit("|ts=", 1)[0]}   # only the fingerprint differs


def test_score_only_never_regenerates_and_finds_nothing_in_a_cache_written_before_the_tag(monkeypatch, tmp_path, caplog):
    def forbidden(question, verify_mode="off"):
        raise AssertionError("--score-only must not call the agent")

    monkeypatch.setattr(run_eval, "run_agent_capture", forbidden)
    old_key = run_eval._cache_key("qa_0001", financial_agent.LLM_MODEL, run_eval._prompt_version())   # as cached before
    run_eval.save_cache({old_key: {"id": "qa_0001", "question": "q", "ground_truth": "g", "answer": "a",
                                   "observations": [], "question_type": "numerical"}}, tmp_path / "cache.json")
    assert _drive_main(monkeypatch, tmp_path, "--score-only") == 1
    assert "found no cached agent outputs" in caplog.text and "tool_schema=" in caplog.text
