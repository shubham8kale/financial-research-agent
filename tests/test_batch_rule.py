# tests/test_batch_rule.py
#
# AGENT_BATCH_RULE=on|off: the batching rule appended to the system prompt.
# Off (the default until a measured gate decides) the prompt is the one it has
# always been, byte for byte, and so is its recorded version; on, the rule is
# appended, the version changes, and both the direct and the MCP agent are
# built with the composed prompt.

import asyncio
import hashlib

import pytest

from agent import financial_agent, mcp_agent
from eval import run_eval


def digest(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AGENT_BATCH_RULE", raising=False)


def test_off_is_the_default_and_leaves_the_prompt_and_its_version_untouched(monkeypatch):
    assert financial_agent.batch_rule_enabled() is False
    assert financial_agent.system_prompt() == financial_agent._SYSTEM_PROMPT
    assert run_eval._prompt_version() == digest(financial_agent._SYSTEM_PROMPT)
    for off in ("off", "OFF", "false", "0", "no", ""):
        monkeypatch.setenv("AGENT_BATCH_RULE", off)
        assert financial_agent.system_prompt() == financial_agent._SYSTEM_PROMPT


def test_on_appends_the_rule_and_changes_the_recorded_prompt_version(monkeypatch):
    base = financial_agent._SYSTEM_PROMPT
    off_version = run_eval._prompt_version()
    for on in ("on", "ON", "true", "1", "yes"):
        monkeypatch.setenv("AGENT_BATCH_RULE", on)
        prompt = financial_agent.system_prompt()
        assert prompt.startswith(base) and prompt == base + financial_agent._BATCH_RULE
    assert run_eval._prompt_version() == digest(base + financial_agent._BATCH_RULE) != off_version
    assert run_eval._batch_rule_enabled() is True


def test_the_rule_says_what_it_is_meant_to_say(monkeypatch):
    rule = financial_agent._BATCH_RULE
    assert rule.startswith("\n9. ")                               # a numbered rule after rule 8
    assert "ONE step" in rule and "several companies, years or concepts" in rule
    assert "compare_companies" in rule and "step budget" in rule
    # the rejected lookups of the committed runs happened inside batched steps, so the rule repeats the requirement
    assert "required arguments" in rule and "concept" in rule
    assert "\n9." not in financial_agent._SYSTEM_PROMPT


def test_an_unknown_value_is_an_error_not_a_silent_off(monkeypatch):
    monkeypatch.setenv("AGENT_BATCH_RULE", "maybe")
    with pytest.raises(ValueError, match="AGENT_BATCH_RULE"):
        financial_agent.system_prompt()


def test_the_direct_agent_is_built_with_the_composed_prompt(monkeypatch):
    seen = []
    monkeypatch.setattr(financial_agent, "build_llm", lambda: object())
    monkeypatch.setattr(financial_agent, "create_react_agent", lambda **kw: seen.append(kw) or object())
    financial_agent.build_agent_executor()
    monkeypatch.setenv("AGENT_BATCH_RULE", "on")
    financial_agent.build_agent_executor()
    assert seen[0]["prompt"] == financial_agent._SYSTEM_PROMPT
    assert seen[1]["prompt"] == financial_agent._SYSTEM_PROMPT + financial_agent._BATCH_RULE
    assert [t.name for t in seen[1]["tools"]] == [t.name for t in financial_agent.TOOLS]


def test_the_mcp_agent_is_built_with_the_same_composed_prompt(monkeypatch):
    seen = []

    async def discover():
        return ["tool"]

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(mcp_agent, "_discover_tools", discover)
    monkeypatch.setattr(mcp_agent, "ChatGoogleGenerativeAI", lambda **kw: object())
    monkeypatch.setattr(mcp_agent, "create_react_agent", lambda **kw: seen.append(kw) or object())
    asyncio.run(mcp_agent.build_agent_executor())
    monkeypatch.setenv("AGENT_BATCH_RULE", "on")
    asyncio.run(mcp_agent.build_agent_executor())
    assert seen[0]["prompt"] == financial_agent._SYSTEM_PROMPT
    assert seen[1]["prompt"] == financial_agent._SYSTEM_PROMPT + financial_agent._BATCH_RULE
