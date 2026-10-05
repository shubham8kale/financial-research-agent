# tests/test_tool_schema_version.py
#
# The fingerprint of what the model is shown about the tools.  _prompt_version()
# hashes the system prompt only, so a tool docstring or schema edit would
# otherwise change nothing the harness records; this pins that it does.

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
